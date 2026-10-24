import pytest

from fantasy_helper.analytics.rates import blend_rates
from fantasy_helper.analytics.zscores import pct
from fantasy_helper.domain import STAT_KEYS, StatLine


def line(player_id, window, gp=10, mpg=30, **per_game):
    return StatLine(player_id, window, gp=gp, mpg=mpg, per_game={k: 0.0 for k in STAT_KEYS} | per_game)


@pytest.mark.parametrize(("gp", "expected"), [(0, 10.0), (10, 15.0), (990, 19.9)])
def test_weight_moves_from_projection_to_actual_with_games(gp, expected):
    [b] = blend_rates([line(1, "season", gp=gp, PTS=20)], [line(1, "projected", gp=70, PTS=10)], k=10)
    assert b.per_game["PTS"] == pytest.approx(expected)
    assert b.window == "blended" and b.gp == gp


def test_makes_and_attempts_blend_separately():
    # Half and half: FGM (2+8)/2 = 5 on FGA (5+15)/2 = 10 is 50%,
    # not 46.7%, the average of 40% and 53.3%.
    season = line(1, "season", gp=10, FGM=2, FGA=5)
    projected = line(1, "projected", FGM=8, FGA=15)
    [b] = blend_rates([season], [projected], k=10)
    assert pct(b.per_game["FGM"], b.per_game["FGA"]) == pytest.approx(0.5)


def test_players_with_one_window_pass_through():
    lines = blend_rates([line(1, "season", gp=3, PTS=30, mpg=20)], [line(2, "projected", PTS=12)])
    by_id = {b.player_id: b for b in lines}
    assert by_id[1].per_game["PTS"] == 30 and by_id[1].mpg == 20 and by_id[1].gp == 3
    assert by_id[2].per_game["PTS"] == 12 and by_id[2].gp == 0


def test_wrong_window_is_rejected():
    with pytest.raises(ValueError, match="Expected season"):
        blend_rates([line(1, "last_7")], [])
