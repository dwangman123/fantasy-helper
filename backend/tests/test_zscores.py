import pytest

from fantasy_helper.analytics.zscores import player_zscores
from fantasy_helper.categories import KNOWN_CATEGORIES
from fantasy_helper.domain import STAT_KEYS, StatLine

PTS, TO, FG = KNOWN_CATEGORIES[0], KNOWN_CATEGORIES[11], KNOWN_CATEGORIES[19]


def line(player_id, window="projected", **per_game):
    return StatLine(player_id, window, gp=10, mpg=30, per_game={k: 0.0 for k in STAT_KEYS} | per_game)


def test_counting_cat_is_standard_z():
    # PTS 10, 20, 30: mean 20, population sd sqrt(200/3)
    z = player_zscores([line(1, PTS=10), line(2, PTS=20), line(3, PTS=30)], [PTS])
    assert z[2].by_cat["PTS"] == 0.0
    assert z[3].by_cat["PTS"] == pytest.approx(10 / (200 / 3) ** 0.5)
    assert z[1].by_cat["PTS"] == pytest.approx(-z[3].by_cat["PTS"])


def test_reversed_cat_is_negated():
    z = player_zscores([line(1, TO=1), line(2, TO=3)], [TO])
    assert z[1].by_cat["TO"] > 0 > z[2].by_cat["TO"]


def test_percentage_cat_weights_by_volume():
    # League FG% = 25/50 = 50%. Player 1 shoots 3/5 (60%, impact +0.5);
    # player 2 shoots 11/20 (55%, impact +1.0); player 3 shoots 11/25 (44%, impact -1.5).
    lines = [line(1, FGM=3, FGA=5), line(2, FGM=11, FGA=20), line(3, FGM=11, FGA=25)]
    z = player_zscores(lines, [FG])
    assert z[2].by_cat["FG%"] > z[1].by_cat["FG%"] > 0 > z[3].by_cat["FG%"]


def test_baseline_pool_sets_mean_but_everyone_is_scored():
    lines = [line(1, PTS=20), line(2, PTS=30), line(3, PTS=5)]  # 3 is a free agent
    z = player_zscores(lines, [PTS], baseline_ids=[1, 2])
    assert z[1].by_cat["PTS"] == pytest.approx(-1.0)  # mean 25, sd 5
    assert z[3].by_cat["PTS"] == pytest.approx(-4.0)


def test_zero_spread_gives_zero_instead_of_dividing_by_zero():
    z = player_zscores([line(1, PTS=10), line(2, PTS=10)], [PTS, FG])
    assert z[1].by_cat == {"PTS": 0.0, "FG%": 0.0}


def test_total_sums_categories():
    z = player_zscores([line(1, PTS=10, TO=1), line(2, PTS=20, TO=3)], [PTS, TO])
    assert z[1].total == pytest.approx(z[1].by_cat["PTS"] + z[1].by_cat["TO"]) == pytest.approx(0.0)


def test_mixed_windows_are_rejected():
    with pytest.raises(ValueError, match="mix windows"):
        player_zscores([line(1), line(2, window="season")], [PTS])
