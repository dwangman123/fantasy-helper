"""Persist a LeagueSnapshot. Season-scoped tables are replaced in one transaction,
so readers never see a half-written snapshot."""

from sqlalchemy import delete
from sqlalchemy.orm import Session

from fantasy_helper.db import models as m
from fantasy_helper.domain import LeagueSnapshot

_SEASON_TABLES = [m.RosterEntry, m.StatLine, m.ProGame, m.Matchup, m.Team]


def store_snapshot(session: Session, snap: LeagueSnapshot) -> dict[str, int]:
    season = snap.season
    session.merge(
        m.League(
            season=season,
            league_id=snap.league_id,
            name=snap.name,
            categories=[c.key for c in snap.categories],
            current_matchup_period=snap.current_matchup_period,
            current_scoring_period=snap.current_scoring_period,
            updated_at=snap.fetched_at,
        )
    )
    for p in snap.players:
        session.merge(
            m.Player(
                player_id=p.player_id,
                name=p.name,
                pro_team=p.pro_team,
                position=p.position,
                eligible_slots=p.eligible_slots,
                injury_status=p.injury_status,
                updated_at=snap.fetched_at,
            )
        )
    session.flush()

    for table in _SEASON_TABLES:
        session.execute(delete(table).where(table.season == season))

    session.add_all(
        m.Team(
            season=season, team_id=t.team_id, name=t.name, abbrev=t.abbrev, wins=t.wins, losses=t.losses, ties=t.ties
        )
        for t in snap.teams
    )
    session.add_all(
        m.RosterEntry(
            season=season,
            player_id=r.player_id,
            team_id=r.team_id,
            lineup_slot=r.lineup_slot,
            acquisition_type=r.acquisition_type,
        )
        for r in snap.rosters
    )
    session.add_all(
        m.StatLine(
            season=season,
            player_id=s.player_id,
            window=s.window,
            gp=s.gp,
            mpg=s.mpg,
            **{col: s.per_game[key] for key, col in m.STAT_COLUMNS.items()},
        )
        for s in snap.stat_lines
    )
    session.add_all(
        m.ProGame(
            season=season,
            pro_team=g.pro_team,
            scoring_period=g.scoring_period,
            opponent=g.opponent,
            game_date=g.game_date,
        )
        for g in snap.pro_games
    )
    session.add_all(
        m.Matchup(
            season=season,
            espn_id=x.espn_id,
            matchup_period=x.matchup_period,
            home_team_id=x.home_team_id,
            away_team_id=x.away_team_id,
            winner=x.winner,
            playoff_tier=x.playoff_tier,
            home_cats=x.home_cats,
            away_cats=x.away_cats,
        )
        for x in snap.matchups
    )
    return {
        "teams": len(snap.teams),
        "players": len(snap.players),
        "rostered": len(snap.rosters),
        "stat_lines": len(snap.stat_lines),
        "pro_games": len(snap.pro_games),
        "matchups": len(snap.matchups),
    }
