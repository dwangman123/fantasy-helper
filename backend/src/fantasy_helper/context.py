"""The stored snapshot turned into analytics inputs, shared by matchup predictions and
waiver rankings.

Per player: blended per-game rates (`rates.blend_rates`), games from the NBA
schedule, each adjusted for the opponent (`opponent.opponent_factors`), a chance
of playing from ESPN's injury status, and a start priority from blended z-scores
(baseline: rostered players). Players in the IR slot are left out of a team.
Game-to-game spread comes from the box-score history (`variance.fit_dispersion`),
falling back to Poisson until there are enough logged games.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from fantasy_helper.analytics.matchup import SimPlayer
from fantasy_helper.analytics.opponent import opponent_factors
from fantasy_helper.analytics.rates import blend_rates
from fantasy_helper.analytics.variance import fit_dispersion
from fantasy_helper.analytics.zscores import PlayerZ, player_zscores
from fantasy_helper.calendar import MatchupCalendar, MatchupWeek, load_calendar
from fantasy_helper.categories import Category
from fantasy_helper.db import models as m
from fantasy_helper.db import queries as q
from fantasy_helper.domain import STAT_KEYS, Player, RosterEntry

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
    z: dict[int, PlayerZ]  # blended z-scores
    factors: dict[str, dict[str, float]]  # NBA team -> per-stat multiplier for its opponents
    dispersion: dict[str, float]
    schedule: dict[str, dict[int, str]]  # NBA team -> {scoring period: opponent}, today on

    @property
    def matchup_period(self) -> int:
        return self.week.matchup_period

    @property
    def days_left(self) -> list[int]:
        return [sp for sp in self.week.scoring_periods if sp >= self.scoring_period]

    def team_entries(self, team_id: int) -> list[RosterEntry]:
        """Roster entries that hold a roster spot (IR doesn't)."""
        return [r for r in self.rosters if r.team_id == team_id and r.lineup_slot not in NON_ROSTER_SLOTS]

    def free_agent_ids(self) -> list[int]:
        rostered = {r.player_id for r in self.rosters}
        return sorted(pid for pid in self.rates if pid not in rostered and pid in self.players)

    def sim_player(self, player_id: int, days: Iterable[int], healthy: bool = False) -> SimPlayer:
        p = self.players[player_id]
        games = self.schedule.get(p.pro_team, {})
        return SimPlayer(
            player_id=player_id,
            eligible_slots=p.eligible_slots,
            per_game=self.rates[player_id],
            games={d: self.factors.get(games[d], {}) for d in days if d in games},
            p_play=1.0 if healthy else p_play(p.injury_status),
            priority=self.z[player_id].total,
        )

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
    return LeagueContext(
        season=season,
        categories=categories,
        calendar=calendar,
        week=calendar.week(league.current_matchup_period),
        scoring_period=league.current_scoring_period,
        slot_counts=league.lineup_slot_counts,
        rosters=rosters,
        players=q.load_players(session, rostered_ids | {s.player_id for s in lines}),
        rates={s.player_id: s.per_game for s in lines},
        z=player_zscores(lines, categories, baseline_ids=rostered_ids),
        factors=opponent_factors(q.load_opponent_stats(session, season)),
        dispersion=fit_dispersion(q.load_game_stats(session, season), STAT_KEYS),
        schedule=q.load_schedule(session, season, range(league.current_scoring_period, last_day + 1)),
    )
