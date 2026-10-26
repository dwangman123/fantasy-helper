"""Network layer: pulls one consistent LeagueSnapshot from ESPN via espn_api,
plus NBA opponent stats from ESPN's public site API."""

import json
import urllib.request
from collections.abc import Collection
from datetime import UTC, datetime

from espn_api.basketball import League

from fantasy_helper.calendar import load_calendar
from fantasy_helper.categories import categories_from_scoring_items
from fantasy_helper.config import Settings
from fantasy_helper.domain import DailySlot, LeagueSnapshot, OpponentStats, PlayerGameStat
from fantasy_helper.espn import parse


def days_to_fetch(current_scoring_period: int, stored_days: Collection[int]) -> list[int]:
    """Completed days missing from the box-score history, plus the latest completed day
    again so ESPN's overnight stat corrections are picked up."""
    completed = set(range(1, current_scoring_period))
    latest = {current_scoring_period - 1} & completed
    return sorted((completed - set(stored_days)) | latest)


def fetch_game_stats(league: League, scoring_period: int, free_agent_pool_size: int) -> list[PlayerGameStat]:
    """Every rostered player's box score for one past day, plus the top free agents'."""
    games: dict[int, PlayerGameStat] = {}
    rosters = league.espn_request.league_get(params={"view": "mRoster", "scoringPeriodId": scoring_period})
    for team in rosters["teams"]:
        for entry in team["roster"]["entries"]:
            player = entry["playerPoolEntry"]["player"]
            g = parse.parse_game_stat(player, scoring_period, int(team["id"]), entry.get("lineupSlotId"))
            if g:
                games[g.player_id] = g
    fa_filter = {
        "players": {
            "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
            "limit": free_agent_pool_size,
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
        }
    }
    free_agents = league.espn_request.league_get(
        params={"view": "kona_player_info", "scoringPeriodId": scoring_period},
        headers={"x-fantasy-filter": json.dumps(fa_filter)},
    )
    for entry in free_agents["players"]:
        g = parse.parse_game_stat(entry["player"], scoring_period)
        if g and g.player_id not in games:
            games[g.player_id] = g
    return list(games.values())


def fetch_daily_lineups(league: League, scoring_periods: Collection[int]) -> list[DailySlot]:
    """Every team's set lineup on each of these days; ESPN answers for future days too."""
    return [
        slot
        for day in scoring_periods
        for slot in parse.parse_daily_lineups(
            league.espn_request.league_get(params={"view": "mRoster", "scoringPeriodId": day})["teams"], day
        )
    ]


# ESPN's public (undocumented) NBA stats API; may change without notice.
OPPONENT_STATS_URL = (
    "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/statistics/byteam?category=opponent&limit=30"
)


def fetch_opponent_stats() -> list[OpponentStats]:
    request = urllib.request.Request(OPPONENT_STATS_URL, headers={"User-Agent": "fantasy-helper"})
    with urllib.request.urlopen(request, timeout=30) as response:
        stats = parse.parse_opponent_stats(json.load(response))
    if len(stats) != 30:
        raise ValueError(f"Expected 30 NBA teams of opponent stats, got {len(stats)}")
    return stats


def fetch_snapshot(settings: Settings, stored_game_days: Collection[int] = ()) -> LeagueSnapshot:
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
    raw_settings = league.espn_request.league_get(params={"view": "mSettings"})["settings"]
    game_stats = [
        g
        for day in days_to_fetch(int(league.scoringPeriodId), stored_game_days)
        for g in fetch_game_stats(league, day, settings.free_agent_pool_size)
    ]
    week = calendar.week(int(league.currentMatchupPeriod))
    lineup_days = [d for d in week.scoring_periods if d >= int(league.scoringPeriodId)]
    warnings = []
    try:
        opponent_stats = fetch_opponent_stats()
    except Exception as exc:  # optional input: predictions fall back to no opponent adjustment
        opponent_stats = []
        warnings.append(f"Opponent stats unavailable, keeping the last stored ones: {exc!r}")

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
        lineup_slot_counts=parse.parse_lineup_slot_counts(raw_settings["rosterSettings"]),
        scoring_type=raw_settings["scoringSettings"].get("scoringType"),
        first_tip_at=parse.first_tip(list(all_players.values()), int(league.scoringPeriodId)),
        game_stats=game_stats,
        opponent_stats=opponent_stats,
        daily_lineups=fetch_daily_lineups(league, lineup_days),
        warnings=warnings,
    )
