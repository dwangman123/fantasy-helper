"""Normalized, ESPN-independent snapshot of the league. Everything downstream
(database, analytics, agent tools) consumes these types, never espn_api objects."""

from dataclasses import dataclass, field
from datetime import date, datetime

from fantasy_helper.categories import Category

# Per-game stats kept for every player/window. Percentages are deliberately
# absent: FG% and FT% are always derived from makes and attempts.
STAT_KEYS = ["PTS", "REB", "AST", "STL", "BLK", "3PM", "TO", "FGM", "FGA", "FTM", "FTA"]

# ESPN stat-window key suffix -> our window name
STAT_WINDOWS = {
    "total": "season",
    "last_7": "last_7",
    "last_15": "last_15",
    "last_30": "last_30",
    "projected": "projected",
}


@dataclass
class Player:
    player_id: int
    name: str
    pro_team: str
    position: str
    eligible_slots: list[str]
    injury_status: str | None


@dataclass
class StatLine:
    player_id: int
    window: str
    gp: float
    mpg: float
    per_game: dict[str, float]  # keyed by STAT_KEYS


@dataclass
class RosterEntry:
    team_id: int
    player_id: int
    lineup_slot: str
    acquisition_type: str | None
    acquired_at: datetime | None = None  # when his current team got him


@dataclass
class Team:
    team_id: int
    name: str
    abbrev: str
    wins: int
    losses: int
    ties: int


@dataclass
class ProGame:
    pro_team: str
    scoring_period: int
    opponent: str
    game_date: date


@dataclass
class Matchup:
    espn_id: int
    matchup_period: int
    home_team_id: int
    away_team_id: int | None  # None for playoff byes
    winner: str  # HOME / AWAY / TIE / UNDECIDED
    playoff_tier: str
    # Totals so far, keyed by category key plus the FGM/FGA/FTM/FTA components;
    # empty before games are played.
    home_cats: dict[str, float] = field(default_factory=dict)
    away_cats: dict[str, float] = field(default_factory=dict)


@dataclass
class PlayerGameStat:
    """One player's box score for one day (scoring period)."""

    player_id: int
    scoring_period: int
    pro_team: str | None
    minutes: float
    stats: dict[str, float]  # game totals keyed by STAT_KEYS
    team_id: int | None = None  # fantasy team rostering him that day; None for free agents
    lineup_slot: str | None = None  # his slot that day, e.g. "BE"


@dataclass
class DailySlot:
    """A rostered player's lineup slot on one day, as set on ESPN. ESPN keeps a lineup
    for every future day too, so this covers today through the end of the week."""

    team_id: int
    player_id: int
    scoring_period: int
    lineup_slot: str  # e.g. "PG", "UT", "BE"


@dataclass
class LiveGame:
    """An NBA team's game today, as it stood when fetched."""

    pro_team: str  # fantasy schedule code, e.g. "GSW"
    tip_at: datetime | None  # UTC
    state: str  # "pre", "live" or "final"
    period: int  # 0 before tip; 5+ is overtime
    clock: str  # game clock in the period, e.g. "5:21"
    remaining: float  # share of the game still to play: 1 before tip, 0 at the final


@dataclass
class Move:
    """A player added to or dropped from a fantasy team, through free agency or waivers.
    Trades aren't here: without a signed-in session ESPN lists them without their players,
    so they come from roster entries acquired by trade instead."""

    transaction_id: str
    player_id: int
    player_name: str | None
    team_id: int
    type: str  # ADD / DROP
    kind: str  # FREEAGENT / WAIVER
    scoring_period: int
    at: datetime | None  # UTC


@dataclass
class OpponentStats:
    """What opponents average against one NBA team: its per-game stats allowed."""

    pro_team: str  # fantasy schedule code, e.g. "GSW"
    gp: float
    per_game: dict[str, float]  # keyed by STAT_KEYS


@dataclass
class LeagueSnapshot:
    league_id: int
    season: int
    name: str
    categories: list[Category]
    current_matchup_period: int
    current_scoring_period: int
    teams: list[Team]
    players: list[Player]
    rosters: list[RosterEntry]
    stat_lines: list[StatLine]
    pro_games: list[ProGame]
    matchups: list[Matchup]
    matchup_ids: dict[int, list[str]]  # ESPN's played scoring periods per matchup
    fetched_at: datetime
    lineup_slot_counts: dict[str, int] = field(default_factory=dict)  # slot name -> count, incl. BE/IR
    scoring_type: str | None = None  # e.g. H2H_MOST_CATEGORIES
    game_stats: list[PlayerGameStat] = field(default_factory=list)  # completed days fetched this run
    opponent_stats: list[OpponentStats] = field(default_factory=list)  # empty if that fetch failed
    daily_lineups: list[DailySlot] = field(default_factory=list)  # set lineups, today through the week's end
    live_stats: list[PlayerGameStat] = field(default_factory=list)  # rostered players' lines today so far
    live_games: list[LiveGame] = field(default_factory=list)  # today's NBA games
    moves: list[Move] = field(default_factory=list)  # adds and drops on the days fetched this run
    warnings: list[str] = field(default_factory=list)  # non-fatal fetch problems
