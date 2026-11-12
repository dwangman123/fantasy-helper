import json

import pytest

from fantasy_helper.analytics.zscores import Baseline, WeeklyBounce, gscore_weights, load_weekly_bounce, player_zscores
from fantasy_helper.categories import KNOWN_CATEGORIES
from fantasy_helper.domain import STAT_KEYS, StatLine

PTS, STL, TO, FG = KNOWN_CATEGORIES[0], KNOWN_CATEGORIES[2], KNOWN_CATEGORIES[11], KNOWN_CATEGORIES[19]


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


def test_value_without_weights_is_the_plain_z_sum():
    z = player_zscores([line(1, PTS=10, TO=1), line(2, PTS=20, TO=3)], [PTS, TO])
    assert z[1].value == pytest.approx(z[1].by_cat["PTS"] + z[1].by_cat["TO"]) == pytest.approx(0.0)


def test_value_weights_each_category_and_leaves_z_alone():
    lines = [line(1, PTS=10, TO=3), line(2, PTS=20, TO=1)]
    plain = player_zscores(lines, [PTS, TO])
    weighted = player_zscores(lines, [PTS, TO], weights={"PTS": 1.5, "TO": 0.5})
    assert weighted[2].by_cat == plain[2].by_cat
    assert weighted[2].value == pytest.approx(1.5 * plain[2].by_cat["PTS"] + 0.5 * plain[2].by_cat["TO"])


def test_g_weight_shrinks_with_bounce_and_weights_average_one():
    base = {"PTS": Baseline(20.0, 5.0), "STL": Baseline(1.0, 0.5)}
    # PTS: no bounce, raw weight 1. STL: tau^2 / sigma^2 = 0.75 / 0.25 = 3, raw 1/2. Mean 0.75.
    w = gscore_weights(base, {"PTS": WeeklyBounce(0.0, 20.0), "STL": WeeklyBounce(0.75, 1.0)}, [PTS, STL])
    assert w == pytest.approx({"PTS": 1 / 0.75, "STL": 0.5 / 0.75})


def test_counting_bounce_scales_with_this_pools_mean():
    # Fitted at a pool mean of 1 steal; this pool averages 2, so tau^2 doubles: 0.375 x 2 / 0.25 = 3.
    base = {"PTS": Baseline(20.0, 5.0), "STL": Baseline(2.0, 0.5)}
    w = gscore_weights(base, {"PTS": WeeklyBounce(0.0, 20.0), "STL": WeeklyBounce(0.375, 1.0)}, [PTS, STL])
    assert w["STL"] / w["PTS"] == pytest.approx(0.5)


def test_percentage_bounce_is_used_as_fitted():
    # Impact is centered on the pool, so its mean says nothing about volume; no scaling (3 / 1 = 3).
    base = {"PTS": Baseline(20.0, 5.0), "FG%": Baseline(0.5, 1.0, league_pct=0.47)}
    w = gscore_weights(base, {"PTS": WeeklyBounce(0.0, 20.0), "FG%": WeeklyBounce(3.0, 0.1)}, [PTS, FG])
    assert w["FG%"] / w["PTS"] == pytest.approx(0.5)


def test_unfitted_or_flat_category_starts_at_one_and_no_bounce_means_plain_z():
    base = {"PTS": Baseline(20.0, 5.0), "STL": Baseline(1.0, 0.0), "TO": Baseline(2.0, 1.0)}
    w = gscore_weights(base, {"PTS": WeeklyBounce(75.0, 20.0), "STL": WeeklyBounce(1.0, 1.0)}, [PTS, STL, TO])
    # PTS: 75 / 25 = 3, raw 1/2; STL has no spread and TO no bounce, raw 1 each. Mean 2.5 / 3.
    assert w == pytest.approx({"PTS": 0.5 * 3 / 2.5, "STL": 3 / 2.5, "TO": 3 / 2.5})
    assert gscore_weights(base, {}, [PTS, STL, TO]) == {}


def test_bounce_comes_from_the_latest_earlier_season(tmp_path):
    for season, var in [(2025, 1.0), (2026, 2.0), (2027, 3.0)]:
        fitted = {"categories": {"PTS": {"weekly_var": var, "pool_mean": 20.0, "single_game_var": 0.5}}}
        (tmp_path / f"weekly_bounce_{season}.json").write_text(json.dumps(fitted))
    assert load_weekly_bounce(2027, tmp_path) == {"PTS": WeeklyBounce(2.0, 20.0)}
    assert load_weekly_bounce(2025, tmp_path) == {}


def test_mixed_windows_are_rejected():
    with pytest.raises(ValueError, match="mix windows"):
        player_zscores([line(1), line(2, window="season")], [PTS])
