from fantasy_helper.espn.client import days_to_fetch


def test_days_to_fetch_backfills_gaps_and_refetches_latest():
    assert days_to_fetch(1, set()) == []  # opening day: nothing completed yet
    assert days_to_fetch(5, set()) == [1, 2, 3, 4]
    assert days_to_fetch(5, {1, 2, 3, 4}) == [4]  # latest again, for stat corrections
    assert days_to_fetch(6, {1, 3, 4}) == [2, 5]


def test_days_to_fetch_refetches_this_weeks_completed_days():
    # Week of days 4-10 on day 7: days 4-6 again, so stat corrections reach this week's totals.
    assert days_to_fetch(7, {1, 2, 3, 4, 5, 6}, range(4, 11)) == [4, 5, 6]
