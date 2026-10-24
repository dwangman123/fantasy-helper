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
    first_tip_at: datetime | None = None  # earliest game today (current scoring period), UTC
    game_stats: list[PlayerGameStat] = field(default_factory=list)  # completed days fetched this run
    opponent_stats: list[OpponentStats] = field(default_factory=list)  # empty if that fetch failed
    warnings: list[str] = field(default_factory=list)  # non-fatal fetch problems
