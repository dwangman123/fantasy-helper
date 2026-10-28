import numpy as np
import pytest

from fantasy_helper.analytics.power import Record, all_play_records, matchup_outcome, power_rankings
from fantasy_helper.categories import KNOWN_CATEGORIES
from fantasy_helper.domain import STAT_KEYS, Matchup

PTS, STL, TO = KNOWN_CATEGORIES[0], KNOWN_CATEGORIES[2], KNOWN_CATEGORIES[11]
CATS = [PTS, STL, TO]


def test_outcome_counts_categories_and_reverses_turnovers():
    a, b = {"PTS": 500, "STL": 40, "TO": 60}, {"PTS": 450, "STL": 45, "TO": 70}
    assert matchup_outcome(a, b, CATS) == 1  # PTS and fewer TO
    assert matchup_outcome(b, a, CATS) == -1
    assert matchup_outcome(a, {"PTS": 500, "STL": 40, "TO": 60}, CATS) == 0  # every category tied
    assert matchup_outcome({"PTS": 500, "STL": 40, "TO": 60}, {"PTS": 400, "STL": 50, "TO": 60}, CATS) == 0


def test_all_play_compares_every_team_each_week():
    strong, middle, weak = ({"PTS": p, "STL": s, "TO": 50} for p, s in [(500, 50), (450, 45), (400, 40)])
    results = [
        Matchup(1, 1, 1, 2, "HOME", "NONE", strong, middle),
        Matchup(2, 1, 3, None, "UNDECIDED", "NONE", weak, {}),  # a bye: still compared with the others
        Matchup(3, 2, 1, 3, "HOME", "NONE", strong, weak),
        Matchup(4, 2, 2, 4, "UNDECIDED", "NONE", middle, {}),  # team 4 hasn't played: left out
    ]
    records = all_play_records(results, CATS)
    assert records[1] == Record(4, 0, 0) and records[2] == Record(2, 2, 0) and records[3] == Record(0, 4, 0)
    assert 4 not in records


def weeks(pts):
    week = np.zeros((2, len(STAT_KEYS)))
    week[:, STAT_KEYS.index("PTS")] = pts
    week[:, STAT_KEYS.index("STL")] = 40
    return week


def test_stronger_rosters_rank_higher_and_equal_ones_split():
    rows = power_rankings([PTS, STL], {1: weeks(400), 2: weeks(500), 3: weeks(400)}, {}, {2: Record(1, 1, 0)})
    assert [r.team_id for r in rows] == [2, 1, 3] and [r.rank for r in rows] == [1, 2, 3]
    best, *others = rows
    assert best.all_play == Record(1, 1, 0) and others[0].all_play == Record()
    # STL is a coin flip for everyone, so with two categories a week is won only with both, tied with one.
    # Team 2 always takes PTS: 0.5 win + 0.5 tie against each. Teams 1 and 3 split with each other.
    assert best.strength == pytest.approx(0.75, abs=0.01)
    assert others[0].strength == pytest.approx(others[1].strength) == pytest.approx((0.5 + 0.25) / 2, abs=0.01)
    assert best.cat_p_win["STL"] == pytest.approx(0.5) and 1.4 < best.cats_per_week < 1.6
