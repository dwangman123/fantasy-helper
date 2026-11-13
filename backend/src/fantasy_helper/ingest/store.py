"""Persist a LeagueSnapshot. Season-scoped tables are replaced in one transaction,
so readers never see a half-written snapshot; game logs accumulate instead."""

from sqlalchemy import delete
from sqlalchemy.orm import Session

from fantasy_helper.db import models as m
from fantasy_helper.domain import LeagueSnapshot

_SEASON_TABLES = [m.RosterEntry, m.DailyLineup, m.StatLine, m.ProGame, m.Matchup, m.Team, m.LiveGameStat, m.LiveGame]


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
            lineup_slot_counts=snap.lineup_slot_counts,
            scoring_type=snap.scoring_type,
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
            season=season,
            team_id=t.team_id,
            name=t.name,
            abbrev=t.abbrev,
            wins=t.wins,
            losses=t.losses,
            ties=t.ties,
            owner=t.owner or None,
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
            acquired_at=r.acquired_at,
        )
        for r in snap.rosters
    )
    session.add_all(
        m.DailyLineup(
            season=season,
            scoring_period=d.scoring_period,
            player_id=d.player_id,
            team_id=d.team_id,
            lineup_slot=d.lineup_slot,
        )
        for d in snap.daily_lineups
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
    session.add_all(
        m.LiveGameStat(
            season=season,
            player_id=g.player_id,
            scoring_period=g.scoring_period,
            pro_team=g.pro_team,
            team_id=g.team_id,
            lineup_slot=g.lineup_slot,
            minutes=g.minutes,
            **{col: g.stats[key] for key, col in m.STAT_COLUMNS.items()},
        )
        for g in snap.live_stats
    )
    session.add_all(
        m.LiveGame(
            season=season,
            pro_team=g.pro_team,
            tip_at=g.tip_at,
            state=g.state,
            period=g.period,
            clock=g.clock,
            remaining=g.remaining,
        )
        for g in {g.pro_team: g for g in snap.live_games}.values()  # one row per team, even if listed twice
    )
    # Replace opponent stats only when this run fetched them, so a failed fetch keeps the last good set.
    if snap.opponent_stats:
        session.execute(delete(m.OpponentStat).where(m.OpponentStat.season == season))
        session.add_all(
            m.OpponentStat(
                season=season,
                pro_team=o.pro_team,
                gp=o.gp,
                **{col: o.per_game[key] for key, col in m.STAT_COLUMNS.items()},
            )
            for o in snap.opponent_stats
        )
    # Same for return dates: a failed fetch keeps the last good set rather than marking everyone healthy.
    if snap.injuries:
        session.execute(delete(m.PlayerInjury).where(m.PlayerInjury.season == season))
        # Dedupe by player: a player traded mid-injury could be listed under two teams.
        latest = {i.player_id: i for i in snap.injuries}
        session.add_all(
            m.PlayerInjury(
                season=season,
                player_id=i.player_id,
                return_date=i.return_date,
                out_for_season=i.out_for_season,
                note=i.note[:100],
            )
            for i in latest.values()
        )
    # Game logs are history: upserted (stat corrections overwrite), never deleted.
    for g in snap.game_stats:
        session.merge(
            m.PlayerGameStat(
                season=season,
                player_id=g.player_id,
                scoring_period=g.scoring_period,
                pro_team=g.pro_team,
                team_id=g.team_id,
                lineup_slot=g.lineup_slot,
                minutes=g.minutes,
                **{col: g.stats[key] for key, col in m.STAT_COLUMNS.items()},
            )
        )
    for x in snap.moves:  # history, like game logs
        session.merge(
            m.Move(
                season=season,
                transaction_id=x.transaction_id,
                player_id=x.player_id,
                player_name=x.player_name,
                team_id=x.team_id,
                type=x.type,
                kind=x.kind,
                scoring_period=x.scoring_period,
                at=x.at,
            )
        )
    return {
        "teams": len(snap.teams),
        "players": len(snap.players),
        "rostered": len(snap.rosters),
        "daily_lineups": len(snap.daily_lineups),
        "stat_lines": len(snap.stat_lines),
        "pro_games": len(snap.pro_games),
        "matchups": len(snap.matchups),
        "game_stats": len(snap.game_stats),
        "opponent_stats": len(snap.opponent_stats),
        "injuries": len(snap.injuries),
        "live_stats": len(snap.live_stats),
        "live_games": len(snap.live_games),
        "moves": len(snap.moves),
    }
