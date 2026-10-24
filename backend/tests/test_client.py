from fantasy_helper.espn.client import days_to_fetch


def test_days_to_fetch_backfills_gaps_and_refetches_latest():
    assert days_to_fetch(1, set()) == []  # opening day: nothing completed yet
    assert days_to_fetch(5, set()) == [1, 2, 3, 4]
    assert days_to_fetch(5, {1, 2, 3, 4}) == [4]  # latest again, for stat corrections
    assert days_to_fetch(6, {1, 3, 4}) == [2, 5]
