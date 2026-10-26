"""Read the stored snapshot back into domain types, so analytics never touches ORM rows."""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from fantasy_helper.categories import KNOWN_CATEGORIES, Category
from fantasy_helper.db import models as m
from fantasy_helper.domain import (
    DailySlot,
    Matchup,
    OpponentStats,
    Player,
    PlayerGameStat,
    RosterEntry,
    StatLine,
    Team,
)

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


def load_daily_lineups(session: Session, season: int, team_id: int) -> list[DailySlot]:
    rows = session.scalars(
        select(m.DailyLineup).where(m.DailyLineup.season == season, m.DailyLineup.team_id == team_id)
    )
    return [DailySlot(r.team_id, r.player_id, r.scoring_period, r.lineup_slot) for r in rows]


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


def load_players(session: Session, player_ids: Iterable[int]) -> dict[int, Player]:
    rows = session.scalars(select(m.Player).where(m.Player.player_id.in_(list(player_ids))))
    return {
        r.player_id: Player(r.player_id, r.name, r.pro_team, r.position, r.eligible_slots, r.injury_status)
        for r in rows
    }


def load_matchups(session: Session, season: int, matchup_period: int) -> list[Matchup]:
    rows = session.scalars(
        select(m.Matchup)
        .where(m.Matchup.season == season, m.Matchup.matchup_period == matchup_period)
        .order_by(m.Matchup.espn_id)
    )
    return [
        Matchup(
            r.espn_id,
            r.matchup_period,
            r.home_team_id,
            r.away_team_id,
            r.winner,
            r.playoff_tier,
            r.home_cats,
            r.away_cats,
        )
        for r in rows
    ]


def load_schedule(session: Session, season: int, scoring_periods: Iterable[int]) -> dict[str, dict[int, str]]:
    """NBA team -> {scoring period: opponent} over the given scoring periods."""
    rows = session.execute(
        select(m.ProGame.pro_team, m.ProGame.scoring_period, m.ProGame.opponent).where(
            m.ProGame.season == season, m.ProGame.scoring_period.in_(list(scoring_periods))
        )
    )
    out: dict[str, dict[int, str]] = {}
    for pro_team, sp, opponent in rows:
        out.setdefault(pro_team, {})[sp] = opponent
    return out


def load_opponent_stats(session: Session, season: int) -> list[OpponentStats]:
    rows = session.scalars(select(m.OpponentStat).where(m.OpponentStat.season == season))
    return [
        OpponentStats(r.pro_team, r.gp, {key: getattr(r, col) for col, key in _STAT_KEYS_BY_COLUMN.items()})
        for r in rows
    ]


def load_game_stat_days(session: Session, season: int) -> set[int]:
    """Scoring periods already in the box-score history."""
    rows = session.scalars(select(m.PlayerGameStat.scoring_period).where(m.PlayerGameStat.season == season).distinct())
    return set(rows)


def load_game_stats(
    session: Session, season: int, team_id: int | None = None, scoring_periods: Iterable[int] | None = None
) -> list[PlayerGameStat]:
    """Box scores for the season, optionally only one fantasy team's and only some days."""
    query = select(m.PlayerGameStat).where(m.PlayerGameStat.season == season)
    if team_id is not None:
        query = query.where(m.PlayerGameStat.team_id == team_id)
    if scoring_periods is not None:
        query = query.where(m.PlayerGameStat.scoring_period.in_(list(scoring_periods)))
    rows = session.scalars(query)
    return [
        PlayerGameStat(
            player_id=r.player_id,
            scoring_period=r.scoring_period,
            pro_team=r.pro_team,
            minutes=r.minutes,
            stats={key: getattr(r, col) for col, key in _STAT_KEYS_BY_COLUMN.items()},
            team_id=r.team_id,
            lineup_slot=r.lineup_slot,
        )
        for r in rows
    ]
