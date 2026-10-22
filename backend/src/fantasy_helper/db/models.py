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


class Team(Base):
    __tablename__ = "teams"

    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    abbrev: Mapped[str] = mapped_column(String(20))
    wins: Mapped[int] = mapped_column(Integer)
    losses: Mapped[int] = mapped_column(Integer)
    ties: Mapped[int] = mapped_column(Integer)


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
