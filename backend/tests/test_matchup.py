import pytest

from fantasy_helper.analytics.matchup import SimPlayer, simulate_matchup
from fantasy_helper.categories import KNOWN_CATEGORIES
from fantasy_helper.domain import STAT_KEYS

CATS = list(KNOWN_CATEGORIES.values())
PTS, TO, FG = KNOWN_CATEGORIES[0], KNOWN_CATEGORIES[11], KNOWN_CATEGORIES[19]
SLOTS = {"PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1, "G": 1, "F": 1, "UT": 3, "BE": 3, "IR": 1}
ANY = ["PG", "SG", "SF", "PF", "C", "G", "F", "UT"]
RATE = {"PTS": 20, "REB": 6, "AST": 4, "STL": 1, "BLK": 0.6, "3PM": 2, "TO": 2, "FGM": 7, "FGA": 15, "FTM": 4, "FTA": 5}


def player(pid, days=(1, 2, 3), p_play=1.0, priority=0.0, **rate):
    return SimPlayer(pid, ANY, {k: 0.0 for k in STAT_KEYS} | (rate or RATE), {d: {} for d in days}, p_play, priority)


def test_identical_teams_are_a_coin_flip_with_ties_possible():
    team = [player(i) for i in range(10)]
    pred = simulate_matchup(CATS, {"home": {}, "away": {}}, {"home": team, "away": team}, [1, 2, 3], SLOTS, 4000)
    assert pred.p_home == pytest.approx(pred.p_away, abs=0.04)
    assert pred.p_tie > 0
    assert pred.p_home + pred.p_tie + pred.p_away == pytest.approx(1.0)
    assert pred.expected_score["home"] == pytest.approx(pred.expected_score["away"], abs=0.15)


def test_leading_every_category_with_no_games_left_is_certain():
    home = {"PTS": 200, "TO": 10, "FGM": 50, "FGA": 100}
    away = {"PTS": 150, "TO": 20, "FGM": 40, "FGA": 100}
    pred = simulate_matchup([PTS, TO, FG], {"home": home, "away": away}, {"home": [], "away": []}, [], SLOTS, 100)
    assert pred.p_home == 1.0 and pred.most_likely_score == (3, 0, 0)
    assert pred.cat_probs["TO"]["home"] == 1.0  # fewer turnovers wins


def test_live_percentage_combines_actual_and_projected_makes():
    # Actual 10/20 so far; one remaining game expecting 30/40: about 40/60, not the 62.5% average.
    actual = {"home": {"FGM": 10, "FGA": 20}, "away": {}}
    shooter = player(1, days=(1,), FGM=30, FGA=40)
    pred = simulate_matchup([FG], actual, {"home": [shooter], "away": []}, [1], SLOTS, 20000)
    assert pred.projected_totals["home"]["FG%"] == pytest.approx(40 / 60, abs=0.005)


def test_lineup_cap_limits_started_games():
    eleven = [player(i, days=(1,), priority=-i) for i in range(11)]
    pred = simulate_matchup([PTS], {"home": {}, "away": {}}, {"home": eleven, "away": []}, [1], SLOTS, 50)
    assert pred.expected_games["home"] == 10


def test_out_players_add_nothing_and_day_to_day_about_half():
    out = [player(1, days=(1,), p_play=0.0)]
    dtd = [player(2, days=(1,), p_play=0.5)]
    pred = simulate_matchup([PTS], {"home": {}, "away": {}}, {"home": out, "away": dtd}, [1], SLOTS, 4000)
    assert pred.expected_games["home"] == 0 and pred.projected_totals["home"]["PTS"] == 0
    assert pred.expected_games["away"] == pytest.approx(0.5, abs=0.03)


def test_fixed_seed_is_reproducible():
    args = (CATS, {"home": {}, "away": {}}, {"home": [player(1)], "away": [player(2)]}, [1, 2, 3], SLOTS, 500)
    assert simulate_matchup(*args, seed=7) == simulate_matchup(*args, seed=7)


def test_per_game_factors_scale_expected_stats():
    boosted = SimPlayer(1, ANY, {k: 0.0 for k in STAT_KEYS} | {"PTS": 20.0}, {1: {"PTS": 1.1}, 2: {}})
    pred = simulate_matchup([PTS], {"home": {}, "away": {}}, {"home": [boosted], "away": []}, [1, 2], SLOTS, 20000)
    assert pred.projected_totals["home"]["PTS"] == pytest.approx(42.0, abs=0.3)  # 22 + 20


def test_adding_a_player_who_adds_nothing_leaves_every_draw_alone():
    # Common random numbers: availability is seeded per player-game, not from one stream,
    # so a new day-to-day player doesn't reshuffle the others' coin flips.
    team = [player(i, p_play=0.5) for i in range(3)]
    idle = SimPlayer(99, ANY, {k: 0.0 for k in STAT_KEYS}, {d: {} for d in (1, 2, 3)}, 0.5)
    opp = [player(10 + i) for i in range(3)]
    args = (CATS, {"home": {}, "away": {}})
    base = simulate_matchup(*args, {"home": team, "away": opp}, [1, 2, 3], SLOTS, 2000)
    more = simulate_matchup(*args, {"home": [*team, idle], "away": opp}, [1, 2, 3], SLOTS, 2000)
    assert more.cat_probs == base.cat_probs and more.p_home == base.p_home


def test_small_roster_change_moves_odds_a_little_not_by_noise():
    team = [player(i) for i in range(8)]
    opp = [player(10 + i) for i in range(8)]
    tiny = player(99, PTS=0.5)
    args = (CATS, {"home": {}, "away": {}})
    base = simulate_matchup(*args, {"home": team, "away": opp}, [1, 2, 3], SLOTS, 2000)
    more = simulate_matchup(*args, {"home": [*team, tiny], "away": opp}, [1, 2, 3], SLOTS, 2000)
    assert 0 <= more.cat_probs["PTS"]["home"] - base.cat_probs["PTS"]["home"] < 0.02
    assert {k: v for k, v in more.cat_probs.items() if k != "PTS"} == {
        k: v for k, v in base.cat_probs.items() if k != "PTS"
    }
