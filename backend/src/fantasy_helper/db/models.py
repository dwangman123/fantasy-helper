"""Relational schema. Portable across SQLite (local dev) and Postgres (prod).

Season-scoped tables hold the latest snapshot and are replaced wholesale on
each ingest; `players` is keyed by ESPN player id across seasons.
"""

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# Domain stat key -> column name (column names can't start with a digit)
STAT_COLUMNS = {
    "PTS": "pts",
    "REB": "reb",
    "AST": "ast",
    "STL": "stl",
    "BLK": "blk",
    "3PM": "tpm",
    "TO": "tov",
    "FGM": "fgm",
    "FGA": "fga",
    "FTM": "ftm",
    "FTA": "fta",
}


class League(Base):
    __tablename__ = "leagues"

    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    categories: Mapped[list] = mapped_column(JSON)  # category keys in display order
    current_matchup_period: Mapped[int] = mapped_column(Integer)
    current_scoring_period: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Nullable so the migration can add them to an existing row; set on every ingest.
    lineup_slot_counts: Mapped[dict | None] = mapped_column(JSON)  # slot name -> count, incl. BE/IR
    scoring_type: Mapped[str | None] = mapped_column(String(40))


class Team(Base):
    __tablename__ = "teams"

    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    abbrev: Mapped[str] = mapped_column(String(20))
    wins: Mapped[int] = mapped_column(Integer)
    losses: Mapped[int] = mapped_column(Integer)
    ties: Mapped[int] = mapped_column(Integer)
    owner: Mapped[str | None] = mapped_column(String(200))  # null until the first ingest that stores it


class Player(Base):
    __tablename__ = "players"

    player_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    pro_team: Mapped[str] = mapped_column(String(10))
    position: Mapped[str] = mapped_column(String(10))
    eligible_slots: Mapped[list] = mapped_column(JSON)
    injury_status: Mapped[str | None] = mapped_column(String(40))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RosterEntry(Base):
    __tablename__ = "roster_entries"

    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.player_id"), primary_key=True)
    team_id: Mapped[int] = mapped_column(Integer, index=True)
    lineup_slot: Mapped[str] = mapped_column(String(10))
    acquisition_type: Mapped[str | None] = mapped_column(String(20))
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DailyLineup(Base):
    """Each rostered player's slot per day, today through the end of the matchup week, as
    set on ESPN. Replaced with the snapshot; past days' slots are on player_game_stats."""

    __tablename__ = "daily_lineups"

    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    scoring_period: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(Integer, index=True)
    lineup_slot: Mapped[str] = mapped_column(String(10))


class StatLine(Base):
    """Per-game averages for one player over one window (season, last_7, ..., projected)."""

    __tablename__ = "player_stat_lines"

    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.player_id"), primary_key=True)
    window: Mapped[str] = mapped_column(String(20), primary_key=True)
    gp: Mapped[float] = mapped_column(Float)
    mpg: Mapped[float] = mapped_column(Float)
    pts: Mapped[float] = mapped_column(Float)
    reb: Mapped[float] = mapped_column(Float)
    ast: Mapped[float] = mapped_column(Float)
    stl: Mapped[float] = mapped_column(Float)
    blk: Mapped[float] = mapped_column(Float)
    tpm: Mapped[float] = mapped_column(Float)
    tov: Mapped[float] = mapped_column(Float)
    fgm: Mapped[float] = mapped_column(Float)
    fga: Mapped[float] = mapped_column(Float)
    ftm: Mapped[float] = mapped_column(Float)
    fta: Mapped[float] = mapped_column(Float)


class PlayerGameStat(Base):
    """One player's box score for one day. Upserted and never wiped: this is the
    game-log history (variance fitting, backtests). No foreign key to players,
    since a free agent's past game can predate his place in the current pool."""

    __tablename__ = "player_game_stats"

    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scoring_period: Mapped[int] = mapped_column(Integer, primary_key=True)
    pro_team: Mapped[str | None] = mapped_column(String(10))
    team_id: Mapped[int | None] = mapped_column(Integer)
    lineup_slot: Mapped[str | None] = mapped_column(String(10))
    minutes: Mapped[float] = mapped_column(Float)
    pts: Mapped[float] = mapped_column(Float)
    reb: Mapped[float] = mapped_column(Float)
    ast: Mapped[float] = mapped_column(Float)
    stl: Mapped[float] = mapped_column(Float)
    blk: Mapped[float] = mapped_column(Float)
    tpm: Mapped[float] = mapped_column(Float)
    tov: Mapped[float] = mapped_column(Float)
    fgm: Mapped[float] = mapped_column(Float)
    fga: Mapped[float] = mapped_column(Float)
    ftm: Mapped[float] = mapped_column(Float)
    fta: Mapped[float] = mapped_column(Float)


class LiveGameStat(Base):
    """Rostered players' box scores so far today. Replaced with the snapshot: once the day
    is over, its final lines are fetched into player_game_stats instead."""

    __tablename__ = "live_game_stats"

    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scoring_period: Mapped[int] = mapped_column(Integer)
    pro_team: Mapped[str | None] = mapped_column(String(10))
    team_id: Mapped[int | None] = mapped_column(Integer)
    lineup_slot: Mapped[str | None] = mapped_column(String(10))
    minutes: Mapped[float] = mapped_column(Float)
    pts: Mapped[float] = mapped_column(Float)
    reb: Mapped[float] = mapped_column(Float)
    ast: Mapped[float] = mapped_column(Float)
    stl: Mapped[float] = mapped_column(Float)
    blk: Mapped[float] = mapped_column(Float)
    tpm: Mapped[float] = mapped_column(Float)
    tov: Mapped[float] = mapped_column(Float)
    fgm: Mapped[float] = mapped_column(Float)
    fga: Mapped[float] = mapped_column(Float)
    ftm: Mapped[float] = mapped_column(Float)
    fta: Mapped[float] = mapped_column(Float)


class LiveGame(Base):
    """Each NBA team's game today as it stood at the ingest. Replaced with the snapshot."""

    __tablename__ = "live_games"

    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    pro_team: Mapped[str] = mapped_column(String(10), primary_key=True)
    tip_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(10))  # pre / live / final
    period: Mapped[int] = mapped_column(Integer)
    clock: Mapped[str] = mapped_column(String(10))
    remaining: Mapped[float] = mapped_column(Float)  # share of the game still to play


class Move(Base):
    """Free-agent and waiver adds and drops. Upserted and never wiped: the league's move history."""

    __tablename__ = "moves"

    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    player_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_name: Mapped[str | None] = mapped_column(String(200))
    team_id: Mapped[int] = mapped_column(Integer, index=True)
    type: Mapped[str] = mapped_column(String(10))  # ADD / DROP
    kind: Mapped[str] = mapped_column(String(20))  # FREEAGENT / WAIVER
    scoring_period: Mapped[int] = mapped_column(Integer)
    at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OpponentStat(Base):
    """Per-game stats each NBA team allows. Replaced on ingests where the fetch succeeds."""

    __tablename__ = "nba_team_opponent_stats"

    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    pro_team: Mapped[str] = mapped_column(String(10), primary_key=True)
    gp: Mapped[float] = mapped_column(Float)
    pts: Mapped[float] = mapped_column(Float)
    reb: Mapped[float] = mapped_column(Float)
    ast: Mapped[float] = mapped_column(Float)
    stl: Mapped[float] = mapped_column(Float)
    blk: Mapped[float] = mapped_column(Float)
    tpm: Mapped[float] = mapped_column(Float)
    tov: Mapped[float] = mapped_column(Float)
    fgm: Mapped[float] = mapped_column(Float)
    fga: Mapped[float] = mapped_column(Float)
    ftm: Mapped[float] = mapped_column(Float)
    fta: Mapped[float] = mapped_column(Float)


class ProGame(Base):
    __tablename__ = "pro_games"

    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    pro_team: Mapped[str] = mapped_column(String(10), primary_key=True)
    scoring_period: Mapped[int] = mapped_column(Integer, primary_key=True)
    opponent: Mapped[str] = mapped_column(String(10))
    game_date: Mapped[date] = mapped_column(Date)


class Matchup(Base):
    __tablename__ = "matchups"

    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    espn_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    matchup_period: Mapped[int] = mapped_column(Integer, index=True)
    home_team_id: Mapped[int] = mapped_column(Integer)
    away_team_id: Mapped[int | None] = mapped_column(Integer)
    winner: Mapped[str] = mapped_column(String(20))
    playoff_tier: Mapped[str] = mapped_column(String(40))
    home_cats: Mapped[dict] = mapped_column(JSON)
    away_cats: Mapped[dict] = mapped_column(JSON)


class MatchupPrediction(Base):
    """One prediction per matchup per run. Append-only: the history shows how the
    odds moved through the week and is what a backtest scores."""

    __tablename__ = "matchup_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ingest_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingest_runs.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    season: Mapped[int] = mapped_column(Integer)
    espn_id: Mapped[int] = mapped_column(Integer)
    matchup_period: Mapped[int] = mapped_column(Integer, index=True)
    scoring_period: Mapped[int] = mapped_column(Integer)  # first day still to play when predicted
    home_team_id: Mapped[int] = mapped_column(Integer)
    away_team_id: Mapped[int] = mapped_column(Integer)
    p_home: Mapped[float] = mapped_column(Float)
    p_tie: Mapped[float] = mapped_column(Float)
    p_away: Mapped[float] = mapped_column(Float)
    cat_probs: Mapped[dict] = mapped_column(JSON)
    expected_score: Mapped[dict] = mapped_column(JSON)
    most_likely_score: Mapped[list] = mapped_column(JSON)
    projected_totals: Mapped[dict] = mapped_column(JSON)
    expected_games: Mapped[dict] = mapped_column(JSON)
    n_sims: Mapped[int] = mapped_column(Integer)
    model_version: Mapped[str] = mapped_column(String(20))
    warnings: Mapped[list | None] = mapped_column(JSON)


class IngestRun(Base):
    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20))  # running / success / failed
    counts: Mapped[dict | None] = mapped_column(JSON)
    warnings: Mapped[list | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
