"""The stored snapshot turned into analytics inputs, shared by matchup predictions and
waiver rankings.

Per player: blended per-game rates (`rates.blend_rates`), games from the NBA
schedule, each adjusted for the opponent (`opponent.opponent_factors`), a chance
of playing from ESPN's injury status (`p_play`; a player ruled out with an expected
return this week can't play before it and is day-to-day from it), and a start
priority: his G-score value from
blended z-scores (baseline: rostered players), weighted by each category's fitted
weekly bounce (`zscores.gscore_weights`). Players in the IR slot are left out of a team.

Rest-of-season views (Hold, trades, power rankings) instead count an injured player's
games from ESPN's expected return date on (`return_period`) and every game after it at
full strength; out for the season means none. An injured player with no listed return
counts from next week on, as if healthy (`unknown_returns`). A player in the IR slot joins
his team when he's back, and the team drops its lowest-priority player to make room
(`waivers.on_roster`).
Game-to-game spread comes from the box-score history (`variance.fit_dispersion`),
falling back to Poisson until there are enough logged games.

Today is live: each team's totals so far include today's lines (`actuals`), and a
game that has tipped is locked into the player's set slot with only the part
still to play projected; a free agent can't join a game already under way.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from statistics import fmean, pstdev

from sqlalchemy.orm import Session

from fantasy_helper.analytics.actuals import week_actuals
from fantasy_helper.analytics.matchup import SimPlayer
from fantasy_helper.analytics.opponent import opponent_factors
from fantasy_helper.analytics.rates import blend_rates
from fantasy_helper.analytics.variance import fit_dispersion
from fantasy_helper.analytics.zscores import (
    Baseline,
    PlayerZ,
    baselines,
    game_baselines,
    gscore_weights,
    load_weekly_bounce,
    player_zscores,
    z_by_cat,
)
from fantasy_helper.calendar import MatchupCalendar, MatchupWeek, load_calendar
from fantasy_helper.categories import Category
from fantasy_helper.db import models as m
from fantasy_helper.db import queries as q
from fantasy_helper.domain import STAT_KEYS, Injury, LiveGame, Player, PlayerGameStat, RosterEntry

OUT_STATUSES = {"OUT", "SUSPENSION", "INJURY_RESERVE"}
HEALTHY_STATUSES = {None, "ACTIVE"}
DAY_TO_DAY_P_PLAY = 0.5
NON_ROSTER_SLOTS = {"IR"}


def p_play(injury_status: str | None) -> float:
    if injury_status in OUT_STATUSES:
        return 0.0
    if injury_status == "DAY_TO_DAY":
        return DAY_TO_DAY_P_PLAY
    return 1.0


def injury_assumption(ctx: "LeagueContext") -> str:
    """How the rest-of-season views count injured players, for the reports' assumptions."""
    text = (
        "Injured players count from ESPN's expected return date (an estimate, often the early end of the reported "
        "range) and play every game after it; out for the season means no games. A player in the IR slot takes a "
        "roster spot once he's back, and his team drops its lowest-value player for him."
    )
    unknown = sorted(ctx.players[pid].name for pid in ctx.unknown_returns(ctx.players))
    if len(unknown) > 5:
        text += f" {len(unknown)} injured players have no return date listed, so they count as healthy after this week."
    elif unknown:
        text += f" No return date is listed for {', '.join(unknown)}, so they count as healthy after this week."
    return text


@dataclass
class LeagueContext:
    season: int
    categories: list[Category]
    calendar: MatchupCalendar
    week: MatchupWeek  # the current matchup week
    scoring_period: int  # today
    slot_counts: dict[str, int]
    rosters: list[RosterEntry]
    players: dict[int, Player]  # rostered players and the stored free-agent pool
    rates: dict[int, dict[str, float]]  # blended per-game rates
    z: dict[int, PlayerZ]  # blended z-scores, valued as G-scores
    g_weights: dict[str, float]  # category -> G-score weight; {} means plain z-sum
    factors: dict[str, dict[str, float]]  # NBA team -> per-stat multiplier for its opponents
    dispersion: dict[str, float]
    schedule: dict[str, dict[int, str]]  # NBA team -> {scoring period: opponent}, today on
    actuals: dict[int, dict[str, float]]  # fantasy team -> started totals this week so far, today included
    live_games: dict[str, LiveGame]  # NBA team -> its game today
    live_stats: dict[int, PlayerGameStat]  # player -> his line so far today
    today_slots: dict[int, str]  # player -> set lineup slot today
    game_base: dict[str, Baseline]  # single-game scale per category (`zscores.game_baselines`)
    game_scale: tuple[float, float]  # mean and sd of one game's value, over rostered players' games
    injuries: dict[int, Injury] = field(default_factory=dict)  # player -> ESPN's expected return

    def game_value(self, stats: dict[str, float]) -> dict[str, float]:
        """One line's value per category on the single-game scale; the game's value is the sum."""
        return z_by_cat(stats, self.categories, self.game_base)

    @property
    def matchup_period(self) -> int:
        return self.week.matchup_period

    @property
    def days_left(self) -> list[int]:
        return [sp for sp in self.week.scoring_periods if sp >= self.scoring_period]

    @property
    def later_weeks(self) -> list[MatchupWeek]:
        """The matchup weeks after this one, playoffs included: what rest-of-season views cover."""
        return [w for w in self.calendar.weeks if w.matchup_period > self.matchup_period]

    @property
    def roster_spots(self) -> int:
        return sum(count for slot, count in self.slot_counts.items() if slot not in NON_ROSTER_SLOTS)

    def actual(self, team_id: int) -> dict[str, float]:
        return self.actuals.get(team_id) or dict.fromkeys(STAT_KEYS, 0.0)

    def team_entries(self, team_id: int) -> list[RosterEntry]:
        """Roster entries that hold a roster spot (IR doesn't)."""
        return [r for r in self.rosters if r.team_id == team_id and r.lineup_slot not in NON_ROSTER_SLOTS]

    def free_agent_ids(self) -> list[int]:
        rostered = {r.player_id for r in self.rosters}
        return sorted(pid for pid in self.rates if pid not in rostered and pid in self.players)

    def injury(self, player_id: int) -> Injury | None:
        """ESPN's listed injury for a player whose fantasy status says he's hurt. A player the
        fantasy API calls ACTIVE is healthy even if the injury feed still lists him."""
        p = self.players.get(player_id)
        if p is None or p.injury_status in HEALTHY_STATUSES:
            return None
        return self.injuries.get(player_id)

    def return_period(self, player_id: int) -> int | None:
        """The first day an injured player is expected back, for the rest-of-season views: past
        the last day if he's out for the season, None if he counts as available (healthy, or
        hurt with no listed return; see `unknown_returns`)."""
        injury = self.injury(player_id)
        if injury is None:
            return None
        if injury.out_for_season:
            return self.calendar.weeks[-1].last_scoring_period + 1
        if injury.return_date is None:
            return None
        return self.calendar.scoring_period_for(injury.return_date)

    def unknown_returns(self, player_ids: Iterable[int]) -> list[int]:
        """Players listed OUT, suspended or on IR with no return date, counted as healthy after this week."""
        return [
            pid
            for pid in player_ids
            if pid in self.players
            and self.players[pid].injury_status in OUT_STATUSES
            and self.return_period(pid) is None
        ]

    def sim_player(self, player_id: int, days: Iterable[int], rest_of_season: bool = False) -> SimPlayer:
        """`rest_of_season`: no chance of missing a game, but no games before his expected return."""
        p = self.players[player_id]
        games = self.schedule.get(p.pro_team, {})
        back = self.return_period(player_id) if rest_of_season else None
        sim = SimPlayer(
            player_id=player_id,
            eligible_slots=p.eligible_slots,
            per_game=self.rates[player_id],
            games={d: self.factors.get(games[d], {}) for d in days if d in games and (back is None or d >= back)},
            p_play=1.0 if rest_of_season else p_play(p.injury_status),
            priority=self.z[player_id].value,
        )
        injury = None if rest_of_season or p.injury_status not in OUT_STATUSES else self.injury(player_id)
        if injury is not None and injury.return_date is not None and not injury.out_for_season:
            # ESPN's date tends to be the early end, so from it on he's as likely to sit as play.
            sim.p_play, sim.out_before = DAY_TO_DAY_P_PLAY, self.return_period(player_id)
        today, live = self.scoring_period, self.live_games.get(p.pro_team)
        if today not in sim.games or live is None or live.state == "pre":
            return sim
        slot = self.today_slots.get(player_id)
        if slot is None:  # not rostered: too late to add him for this game
            del sim.games[today]
            return sim
        # Locked into his slot; what he has so far is in `actuals`, so only the rest is projected.
        sim.locked[today] = slot
        if live.remaining > 0:
            factors = sim.games[today]
            sim.games[today] = {k: factors.get(k, 1.0) * live.remaining for k in STAT_KEYS}
        else:
            del sim.games[today]
        if player_id in self.live_stats:
            sim.p_play, sim.out_before = 1.0, None  # he's played today
        return sim

    def team_sim_players(self, team_id: int, days: Iterable[int], rest_of_season: bool = False) -> list[SimPlayer]:
        """`rest_of_season` adds the players in the IR slot, each holding a roster spot from his
        expected return (the first of `days` if none is listed); one out for the season is left off."""
        days = list(days)
        players = [
            self.sim_player(r.player_id, days, rest_of_season)
            for r in self.team_entries(team_id)
            if r.player_id in self.rates
        ]
        if not rest_of_season or not days:
            return players
        for r in self.rosters:
            if r.team_id == team_id and r.lineup_slot in NON_ROSTER_SLOTS and r.player_id in self.rates:
                sim = self.sim_player(r.player_id, days, rest_of_season=True)
                if sim.games:
                    sim.ir_back = max(self.return_period(r.player_id) or days[0], days[0])
                    players.append(sim)
        return players


def load_context(session: Session, season: int) -> LeagueContext:
    league = session.get(m.League, season)
    if league is None or not league.lineup_slot_counts:
        raise LookupError(f"No league settings stored for season {season}; run fh-ingest first")
    calendar = load_calendar(season)
    rosters = q.load_rosters(session, season)
    rostered_ids = {r.player_id for r in rosters}
    lines = blend_rates(q.load_stat_lines(session, season, "season"), q.load_stat_lines(session, season, "projected"))
    categories = q.load_categories(session, season)
    last_day = calendar.weeks[-1].last_scoring_period
    week, today = calendar.week(league.current_matchup_period), league.current_scoring_period
    game_stats = q.load_game_stats(session, season)
    live_stats = q.load_live_stats(session, season)
    completed = [g for g in game_stats if g.scoring_period in week.scoring_periods and g.scoring_period < today]
    rostered_base = baselines([s for s in lines if s.player_id in rostered_ids], categories)
    g_weights = gscore_weights(rostered_base, load_weekly_bounce(season), categories)
    rostered_games = [g for g in game_stats if g.player_id in rostered_ids and g.minutes > 0]
    game_base = game_baselines(rostered_games, categories)
    game_values = [sum(z_by_cat(g.stats, categories, game_base).values()) for g in rostered_games]
    game_scale = (fmean(game_values), pstdev(game_values)) if game_values else (0.0, 0.0)
    return LeagueContext(
        season=season,
        categories=categories,
        calendar=calendar,
        week=week,
        scoring_period=today,
        slot_counts=league.lineup_slot_counts,
        rosters=rosters,
        players=q.load_players(session, rostered_ids | {s.player_id for s in lines}),
        rates={s.player_id: s.per_game for s in lines},
        z=player_zscores(lines, categories, baseline_ids=rostered_ids, weights=g_weights),
        g_weights=g_weights,
        factors=opponent_factors(q.load_opponent_stats(session, season)),
        dispersion=fit_dispersion(game_stats, STAT_KEYS),
        schedule=q.load_schedule(session, season, range(today, last_day + 1)),
        actuals=week_actuals([*completed, *live_stats]),
        live_games=q.load_live_games(session, season),
        live_stats={g.player_id: g for g in live_stats},
        today_slots=q.load_day_slots(session, season, today),
        game_base=game_base,
        game_scale=game_scale,
        injuries=q.load_injuries(session, season),
    )
