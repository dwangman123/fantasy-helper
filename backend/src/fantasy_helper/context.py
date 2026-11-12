"""The stored snapshot turned into analytics inputs, shared by matchup predictions and
waiver rankings.

Per player: blended per-game rates (`rates.blend_rates`), games from the NBA
schedule, each adjusted for the opponent (`opponent.opponent_factors`), a chance
of playing from ESPN's injury status, and a start priority: his G-score value from
blended z-scores (baseline: rostered players), weighted by each category's fitted
weekly bounce (`zscores.gscore_weights`). Players in the IR slot are left out of a team.
Game-to-game spread comes from the box-score history (`variance.fit_dispersion`),
falling back to Poisson until there are enough logged games.

Today is live: each team's totals so far include today's lines (`actuals`), and a
game that has tipped is locked into the player's set slot with only the part
still to play projected; a free agent can't join a game already under way.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from fantasy_helper.analytics.actuals import week_actuals
from fantasy_helper.analytics.matchup import SimPlayer
from fantasy_helper.analytics.opponent import opponent_factors
from fantasy_helper.analytics.rates import blend_rates
from fantasy_helper.analytics.variance import fit_dispersion
from fantasy_helper.analytics.zscores import PlayerZ, baselines, gscore_weights, load_weekly_bounce, player_zscores
from fantasy_helper.calendar import MatchupCalendar, MatchupWeek, load_calendar
from fantasy_helper.categories import Category
from fantasy_helper.db import models as m
from fantasy_helper.db import queries as q
from fantasy_helper.domain import STAT_KEYS, LiveGame, Player, PlayerGameStat, RosterEntry

OUT_STATUSES = {"OUT", "SUSPENSION", "INJURY_RESERVE"}
DAY_TO_DAY_P_PLAY = 0.5
NON_ROSTER_SLOTS = {"IR"}


def p_play(injury_status: str | None) -> float:
    if injury_status in OUT_STATUSES:
        return 0.0
    if injury_status == "DAY_TO_DAY":
        return DAY_TO_DAY_P_PLAY
    return 1.0


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

    def sim_player(self, player_id: int, days: Iterable[int], healthy: bool = False) -> SimPlayer:
        p = self.players[player_id]
        games = self.schedule.get(p.pro_team, {})
        sim = SimPlayer(
            player_id=player_id,
            eligible_slots=p.eligible_slots,
            per_game=self.rates[player_id],
            games={d: self.factors.get(games[d], {}) for d in days if d in games},
            p_play=1.0 if healthy else p_play(p.injury_status),
            priority=self.z[player_id].value,
        )
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
            sim.p_play = 1.0  # he's played today
        return sim

    def team_sim_players(self, team_id: int, days: Iterable[int], healthy: bool = False) -> list[SimPlayer]:
        days = list(days)
        return [
            self.sim_player(r.player_id, days, healthy) for r in self.team_entries(team_id) if r.player_id in self.rates
        ]


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
    )
