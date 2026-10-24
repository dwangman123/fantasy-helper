import pytest

from fantasy_helper.analytics.variance import MAX_PHI, fit_dispersion
from fantasy_helper.domain import STAT_KEYS, PlayerGameStat


def game(player_id, sp, **stats):
    return PlayerGameStat(player_id, sp, "BOS", 30.0, {k: 0.0 for k in STAT_KEYS} | stats)


def test_pooled_pearson_dispersion():
    # Two players: PTS 10/20/30 (mean 20, sum sq 200) and 4/6/8 (mean 6, sum sq 8).
    # phi = (200 + 8) / (2 * 20 + 2 * 6) = 4.0
    games = [game(1, i, PTS=v) for i, v in enumerate([10, 20, 30])] + [
        game(2, i, PTS=v) for i, v in enumerate([4, 6, 8])
    ]
    assert fit_dispersion(games, ["PTS"], min_players=2) == {"PTS": pytest.approx(4.0)}


def test_not_enough_data_stays_poisson():
    games = [game(1, i, PTS=10 + i) for i in range(5)]
    assert fit_dispersion(games, ["PTS"], min_players=2) == {}
    two_games_each = [game(p, i, PTS=10 + i) for p in (1, 2) for i in range(2)]
    assert fit_dispersion(two_games_each, ["PTS"], min_players=2) == {}


def test_capped_and_zero_mean_stats_skipped():
    games = [game(p, i, PTS=[0, 0, 60][i]) for p in (1, 2) for i in range(3)]
    fitted = fit_dispersion(games, ["PTS", "BLK"], min_players=2)
    assert fitted == {"PTS": MAX_PHI}  # BLK is always 0: no estimate
