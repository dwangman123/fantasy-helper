import pytest

from fantasy_helper.analytics.opponent import MAX_FACTOR, MIN_FACTOR, opponent_factors
from fantasy_helper.domain import STAT_KEYS, OpponentStats


def team(code, gp=10, **allowed):
    return OpponentStats(code, gp, {k: 10.0 for k in STAT_KEYS} | allowed)


def test_factor_is_allowed_over_league_average_shrunk_by_games():
    # League PTS average 110; BOS allows 121 (+10%) over 10 games, so w = 0.5 -> +5%.
    f = opponent_factors([team("BOS", PTS=121), team("NYK", PTS=99)], k=10)
    assert f["BOS"]["PTS"] == pytest.approx(1.05)
    assert f["NYK"]["PTS"] == pytest.approx(0.95)
    assert f["BOS"]["REB"] == 1.0  # allowed exactly the league average


def test_one_game_barely_moves_the_factor():
    f = opponent_factors([team("BOS", gp=1, PTS=150), team("NYK", gp=1, PTS=70)], k=10)
    assert f["BOS"]["PTS"] == pytest.approx(1 + (150 / 110 - 1) / 11)


def test_factors_are_clamped():
    f = opponent_factors([team("BOS", gp=1000, STL=30), team("NYK", gp=1000, STL=2)], k=10)
    assert f["BOS"]["STL"] == MAX_FACTOR and f["NYK"]["STL"] == MIN_FACTOR


def test_no_stats_means_no_adjustment():
    assert opponent_factors([]) == {}
