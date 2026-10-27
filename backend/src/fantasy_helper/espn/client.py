"""Network layer: pulls one consistent LeagueSnapshot from ESPN via espn_api,
plus NBA opponent stats from ESPN's public site API."""

import json
import urllib.request
from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

from espn_api.basketball import League

from fantasy_helper.calendar import load_calendar
from fantasy_helper.categories import categories_from_scoring_items
from fantasy_helper.config import Settings
from fantasy_helper.domain import DailySlot, LeagueSnapshot, LiveGame, Move, OpponentStats, PlayerGameStat
from fantasy_helper.espn import parse


def days_to_fetch(
    current_scoring_period: int, stored_days: Collection[int], week_days: Collection[int] = ()
) -> list[int]:
    """Completed days missing from the box-score history, plus this week's completed days
    and the latest completed day again, so ESPN's stat corrections are picked up: this
    week's "now" totals are summed from these box scores."""
    completed = set(range(1, current_scoring_period))
    again = ({current_scoring_period - 1} | set(week_days)) & completed
    return sorted((completed - set(stored_days)) | again)


def fetch_game_stats(league: League, scoring_period: int, free_agent_pool_size: int) -> list[PlayerGameStat]:
    """Every rostered player's box score for one past day, plus the top free agents'."""
    rosters = league.espn_request.league_get(params={"view": "mRoster", "scoringPeriodId": scoring_period})
    games = {g.player_id: g for g in parse.parse_day_stats(rosters["teams"], scoring_period)}
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


@dataclass
class RosterDays:
    slots: list[DailySlot]  # each team's set lineup per day
    live: list[PlayerGameStat]  # today's box scores so far
    acquired_at: dict[int, datetime]  # player -> when his team got him


def fetch_daily_lineups(league: League, scoring_periods: Collection[int], today: int) -> RosterDays:
    """Every team's set lineup on each of these days (ESPN answers for future days too),
    plus today's box scores so far and acquisition dates, which come in the same responses."""
    out = RosterDays([], [], {})
    for day in scoring_periods:
        teams = league.espn_request.league_get(params={"view": "mRoster", "scoringPeriodId": day})["teams"]
        out.slots += parse.parse_daily_lineups(teams, day)
        if day == today:
            out.live = parse.parse_day_stats(teams, day)
            out.acquired_at = parse.parse_acquired_at(teams)
    return out


# Days of adds and drops re-read on each ingest when none are stored yet, and at most.
MOVE_DAYS = 14


def move_days(today: int, last_stored: int | None) -> list[int]:
    """From the day before the latest stored move (it may have had more), within MOVE_DAYS."""
    first = max(1, today - MOVE_DAYS, (last_stored or 0) - 1)
    return list(range(first, today + 1))


def fetch_moves(league: League, scoring_periods: Collection[int]) -> list[Move]:
    names = {k: v for k, v in league.player_map.items() if isinstance(k, int)}
    return [
        move
        for day in scoring_periods
        for move in parse.parse_moves(
            league.espn_request.league_get(params={"view": "mTransactions2", "scoringPeriodId": day}).get(
                "transactions"
            )
            or [],
            names,
        )
    ]


# ESPN's public (undocumented) NBA APIs; may change without notice.
OPPONENT_STATS_URL = (
    "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/statistics/byteam?category=opponent&limit=30"
)
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={day:%Y%m%d}"


def fetch_live_games(day: date) -> list[LiveGame]:
    """Each NBA game's state on one day: not started, live (with the clock) or final."""
    request = urllib.request.Request(SCOREBOARD_URL.format(day=day), headers={"User-Agent": "fantasy-helper"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return parse.parse_live_games(json.load(response))


def fetch_opponent_stats() -> list[OpponentStats]:
    request = urllib.request.Request(OPPONENT_STATS_URL, headers={"User-Agent": "fantasy-helper"})
    with urllib.request.urlopen(request, timeout=30) as response:
        stats = parse.parse_opponent_stats(json.load(response))
    if len(stats) != 30:
        raise ValueError(f"Expected 30 NBA teams of opponent stats, got {len(stats)}")
    return stats


def fetch_snapshot(
    settings: Settings, stored_game_days: Collection[int] = (), last_move_day: int | None = None
) -> LeagueSnapshot:
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
    today = int(league.scoringPeriodId)
    week = calendar.week(int(league.currentMatchupPeriod))
    game_stats = [
        g
        for day in days_to_fetch(today, stored_game_days, week.scoring_periods)
        for g in fetch_game_stats(league, day, settings.free_agent_pool_size)
    ]
    roster_days = fetch_daily_lineups(league, [d for d in week.scoring_periods if d >= today], today)
    warnings = []
    try:
        opponent_stats = fetch_opponent_stats()
    except Exception as exc:  # optional input: predictions fall back to no opponent adjustment
        opponent_stats = []
        warnings.append(f"Opponent stats unavailable, keeping the last stored ones: {exc!r}")
    try:
        live_games = fetch_live_games(calendar.date_for(today))
    except Exception as exc:  # estimate from tip times rather than double-count or drop today's games
        live_games = parse.estimate_live_games(parse.tips(list(all_players.values()), today), datetime.now(UTC))
        warnings.append(f"NBA scoreboard unavailable, estimating today's games from tip times: {exc!r}")

    return LeagueSnapshot(
        league_id=settings.league_id,
        season=settings.season,
        name=league.settings.name,
        categories=categories,
        current_matchup_period=int(league.currentMatchupPeriod),
        current_scoring_period=int(league.scoringPeriodId),
        teams=[parse.parse_team(t) for t in league.teams],
        players=[parse.parse_player(p) for p in all_players.values()],
        rosters=[
            replace(e, acquired_at=roster_days.acquired_at.get(e.player_id))
            for t in league.teams
            for e in parse.parse_roster(t)
        ],
        stat_lines=[s for p in all_players.values() for s in parse.parse_stat_lines(p, settings.season)],
        pro_games=list(pro_games.values()),
        matchups=parse.parse_matchups(schedule, categories),
        matchup_ids={int(k): v for k, v in league.matchup_ids.items()},
        fetched_at=datetime.now(UTC),
        lineup_slot_counts=parse.parse_lineup_slot_counts(raw_settings["rosterSettings"]),
        scoring_type=raw_settings["scoringSettings"].get("scoringType"),
        game_stats=game_stats,
        opponent_stats=opponent_stats,
        daily_lineups=roster_days.slots,
        live_stats=roster_days.live,
        live_games=live_games,
        moves=fetch_moves(league, move_days(today, last_move_day)),
        warnings=warnings,
    )
