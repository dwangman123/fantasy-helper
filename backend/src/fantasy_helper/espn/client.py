"""Network layer: pulls one consistent LeagueSnapshot from ESPN via espn_api."""

from datetime import UTC, datetime

from espn_api.basketball import League

from fantasy_helper.calendar import load_calendar
from fantasy_helper.categories import categories_from_scoring_items
from fantasy_helper.config import Settings
from fantasy_helper.domain import LeagueSnapshot
from fantasy_helper.espn import parse


def fetch_snapshot(settings: Settings) -> LeagueSnapshot:
    league = League(
        league_id=settings.league_id,
        year=settings.season,
        espn_s2=settings.espn_s2,
        swid=settings.swid,
    )
    calendar = load_calendar(settings.season)
    categories = categories_from_scoring_items(league.settings._raw_scoring_settings["scoringItems"])

    rostered = [p for t in league.teams for p in t.roster]
    free_agents = league.free_agents(size=settings.free_agent_pool_size)

    # Dedupe by player id: a player can appear in both lists mid-transaction.
    all_players = {int(p.playerId): p for p in [*free_agents, *rostered]}

    pro_games = {}
    for p in all_players.values():
        for g in parse.parse_pro_games(p, calendar):
            pro_games[(g.pro_team, g.scoring_period)] = g

    # mMatchup, not mMatchupScore: only mMatchup includes per-category totals (scoreByStat).
    schedule = league.espn_request.league_get(params={"view": "mMatchup"})["schedule"]

    return LeagueSnapshot(
        league_id=settings.league_id,
        season=settings.season,
        name=league.settings.name,
        categories=categories,
        current_matchup_period=int(league.currentMatchupPeriod),
        current_scoring_period=int(league.scoringPeriodId),
        teams=[parse.parse_team(t) for t in league.teams],
        players=[parse.parse_player(p) for p in all_players.values()],
        rosters=[e for t in league.teams for e in parse.parse_roster(t)],
        stat_lines=[s for p in all_players.values() for s in parse.parse_stat_lines(p, settings.season)],
        pro_games=list(pro_games.values()),
        matchups=parse.parse_matchups(schedule, categories),
        matchup_ids={int(k): v for k, v in league.matchup_ids.items()},
        fetched_at=datetime.now(UTC),
    )
