"""Read the stored snapshot back into domain types, so analytics never touches ORM rows."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from fantasy_helper.categories import KNOWN_CATEGORIES, Category
from fantasy_helper.db import models as m
from fantasy_helper.domain import RosterEntry, StatLine, Team

_STAT_KEYS_BY_COLUMN = {col: key for key, col in m.STAT_COLUMNS.items()}
_CATEGORIES_BY_KEY = {c.key: c for c in KNOWN_CATEGORIES.values()}


def load_categories(session: Session, season: int) -> list[Category]:
    league = session.get(m.League, season)
    if league is None:
        raise LookupError(f"No league stored for season {season}; run fh-ingest first")
    return [_CATEGORIES_BY_KEY[key] for key in league.categories]


def load_teams(session: Session, season: int) -> list[Team]:
    rows = session.scalars(select(m.Team).where(m.Team.season == season).order_by(m.Team.team_id))
    return [Team(r.team_id, r.name, r.abbrev, r.wins, r.losses, r.ties) for r in rows]


def load_rosters(session: Session, season: int) -> list[RosterEntry]:
    rows = session.scalars(select(m.RosterEntry).where(m.RosterEntry.season == season))
    return [RosterEntry(r.team_id, r.player_id, r.lineup_slot, r.acquisition_type) for r in rows]


def load_stat_lines(session: Session, season: int, window: str) -> list[StatLine]:
    rows = session.scalars(select(m.StatLine).where(m.StatLine.season == season, m.StatLine.window == window))
    return [
        StatLine(
            player_id=r.player_id,
            window=r.window,
            gp=r.gp,
            mpg=r.mpg,
            per_game={key: getattr(r, col) for col, key in _STAT_KEYS_BY_COLUMN.items()},
        )
        for r in rows
    ]
