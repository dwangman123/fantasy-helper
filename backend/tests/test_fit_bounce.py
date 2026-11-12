from datetime import date, timedelta

import pytest

from fantasy_helper.categories import KNOWN_CATEGORIES
from fantasy_helper.domain import STAT_KEYS
from fantasy_helper.fit_bounce import (
    Game,
    fit_bounce,
    full_weeks,
    parse_gamelog,
    parse_scoreboard,
    season_lines,
    select_pool,
)

PTS, FG = KNOWN_CATEGORIES[0], KNOWN_CATEGORIES[19]
MONDAY = date(2025, 10, 27)


def game(day, **stats):
    return Game(day, dict.fromkeys(STAT_KEYS, 0.0) | stats)


def week(n, *games):
    """Games on consecutive days from the Monday of week n; each game is a dict of stats."""
    start = MONDAY + timedelta(weeks=n)
    return [game(start + timedelta(days=i), **stats) for i, stats in enumerate(games)]


def pts_week(n, *values):
    return week(n, *({"PTS": v} for v in values))


def fit(logs, categories, **kw):
    pool = season_lines(logs, min_games=1)
    return fit_bounce(logs, pool, categories, **kw)


def test_parse_gamelog_reads_regular_season_games_played():
    names = [
        "minutes",
        "fieldGoalsMade-fieldGoalsAttempted",
        "fieldGoalPct",
        "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
        "threePointPct",
        "freeThrowsMade-freeThrowsAttempted",
        "freeThrowPct",
        "totalRebounds",
        "assists",
        "blocks",
        "steals",
        "fouls",
        "turnovers",
        "points",
    ]
    raw = {
        "names": names,
        "events": {
            "1": {"gameDate": "2025-11-02T00:00:00.000+00:00"},
            "2": {"gameDate": "2025-11-01T00:00:00.000+00:00"},
            "3": {"gameDate": "2025-11-03T00:00:00.000+00:00"},
            "4": {"gameDate": "2026-04-25T00:00:00.000+00:00"},
        },
        "seasonTypes": [
            {
                "displayName": "2025-26 Postseason",
                "categories": [{"events": [{"eventId": "4", "stats": ["30"] + ["1-1"] * 13}]}],
            },
            {
                "displayName": "2025-26 Regular Season",
                "categories": [
                    {
                        "events": [
                            {
                                "eventId": "1",
                                "stats": [
                                    "29",
                                    "6-13",
                                    "46.2",
                                    "2-6",
                                    "33.3",
                                    "3-5",
                                    "60.0",
                                    "11",
                                    "3",
                                    "0",
                                    "1",
                                    "1",
                                    "3",
                                    "17",
                                ],
                            },
                            {
                                "eventId": "3",
                                "stats": ["0", "0-0", "0", "0-0", "0", "0-0", "0", "0", "0", "0", "0", "0", "0", "0"],
                            },
                        ]
                    },
                    {
                        "events": [
                            {
                                "eventId": "2",
                                "stats": ["30", "1-2", "50", "0-0", "0", "0-0", "0", "1", "1", "1", "1", "1", "1", "2"],
                            }
                        ]
                    },
                ],
            },
        ],
    }
    games = parse_gamelog(raw)
    assert [g.day for g in games] == [date(2025, 11, 1), date(2025, 11, 2)]  # no DNP, no playoffs, oldest first
    assert games[1].stats == {
        "PTS": 17,
        "REB": 11,
        "AST": 3,
        "STL": 1,
        "BLK": 0,
        "3PM": 2,
        "TO": 3,
        "FGM": 6,
        "FGA": 13,
        "FTM": 3,
        "FTA": 5,
    }


def test_scoreboard_day_replaces_the_logs_utc_date():
    regular = {"eventId": "7", "stats": ["30", "10"]}, {"eventId": "8", "stats": ["30", "12"]}
    raw = {
        "names": ["minutes", "points"],
        "events": {
            "7": {"gameDate": "2025-11-03T00:00:00.000+00:00"},
            "8": {"gameDate": "2025-11-05T00:00:00.000+00:00"},
        },
        "seasonTypes": [{"displayName": "2025-26 Regular Season", "categories": [{"events": list(regular)}]}],
    }
    # Event 7 tipped off Sunday night US time, Monday in UTC; event 8 isn't on a fetched scoreboard.
    game_days = parse_scoreboard({"events": [{"id": "7"}, {"id": "9"}]}, date(2025, 11, 2))
    assert game_days == {"7": date(2025, 11, 2), "9": date(2025, 11, 2)}
    assert [g.day for g in parse_gamelog(raw, game_days)] == [date(2025, 11, 2), date(2025, 11, 5)]


def test_weeks_run_monday_to_sunday_and_short_weeks_are_dropped():
    sunday, monday = MONDAY + timedelta(days=6), MONDAY + timedelta(days=7)
    games = [game(MONDAY), game(MONDAY + timedelta(days=2)), game(sunday), game(monday), game(monday + timedelta(1))]
    weeks = full_weeks(games, min_week_games=3)
    assert [[g.day for g in w] for w in weeks] == [[MONDAY, MONDAY + timedelta(days=2), sunday]]


def test_weekly_bounce_is_the_pool_average_of_each_players_weekly_variance():
    logs = {
        1: pts_week(0, 10, 10, 10) + pts_week(1, 20, 20, 20),  # weekly averages 10, 20: variance 50
        2: pts_week(0, 15, 15, 15) + pts_week(1, 15, 15, 15) + pts_week(2, 100),  # 1-game week left out
    }
    bounce = fit(logs, [PTS], min_weeks=2)["PTS"]
    assert bounce.weekly_var == pytest.approx(25)
    # Player 1's games: variance 30, a third of it over 3-game weeks; player 2's: 0 (his 100 left out).
    assert bounce.single_game_var == pytest.approx(5)


def test_percentage_bounce_is_on_impact_against_the_pool_rate():
    logs = {
        1: week(0, *[{"FGM": 5, "FGA": 10}] * 3) + week(1, *[{"FGM": 3, "FGA": 10}] * 3),
        2: week(0, *[{"FGM": 4, "FGA": 10}] * 3) + week(1, *[{"FGM": 4, "FGA": 10}] * 3),
    }
    # Pool rate 40%: player 1's weekly impact is +1 then -1 (variance 2), player 2's 0.
    assert fit(logs, [FG], min_weeks=2)["FG%"].weekly_var == pytest.approx(1.0)


def test_players_with_too_few_full_weeks_are_left_out():
    logs = {
        1: pts_week(0, 10, 10, 10) + pts_week(1, 20, 20, 20),
        2: pts_week(0, 0, 0, 0) + pts_week(1, 50, 50),  # one full week only
    }
    assert fit(logs, [PTS], min_weeks=2)["PTS"].weekly_var == pytest.approx(50)
    assert fit(logs, [PTS], min_weeks=3) == {}


def test_pool_is_the_top_z_sum_among_players_with_enough_games():
    logs = {1: pts_week(0, 30, 30), 2: pts_week(0, 20, 20), 3: pts_week(0, 10, 10), 4: pts_week(0, 99)}
    lines = season_lines(logs, min_games=2)
    assert {s.player_id for s in lines} == {1, 2, 3}
    assert [s.player_id for s in select_pool(lines, [PTS], 2)] == [1, 2]
