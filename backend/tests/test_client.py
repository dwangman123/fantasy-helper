import io
import json

import pytest

from fantasy_helper.espn import client
from fantasy_helper.espn.client import days_to_fetch, move_days


def test_days_to_fetch_backfills_gaps_and_refetches_latest():
    assert days_to_fetch(1, set()) == []  # opening day: nothing completed yet
    assert days_to_fetch(5, set()) == [1, 2, 3, 4]
    assert days_to_fetch(5, {1, 2, 3, 4}) == [4]  # latest again, for stat corrections
    assert days_to_fetch(6, {1, 3, 4}) == [2, 5]


def test_days_to_fetch_refetches_this_weeks_completed_days():
    # Week of days 4-10 on day 7: days 4-6 again, so stat corrections reach this week's totals.
    assert days_to_fetch(7, {1, 2, 3, 4, 5, 6}, range(4, 11)) == [4, 5, 6]


def test_move_days_resume_from_the_last_stored_move_within_two_weeks():
    assert move_days(5, None) == [1, 2, 3, 4, 5]
    assert move_days(40, None) == list(range(26, 41))  # first run: the last two weeks
    assert move_days(40, 38) == [37, 38, 39, 40]
    assert move_days(40, 3) == list(range(26, 41))


def _feed(n: int) -> dict:
    links = [[{"href": f"https://www.espn.com/nba/player/_/id/{i}/x"}] for i in range(n)]
    return {"injuries": [{"injuries": [{"athlete": {"links": link}, "details": {}} for link in links]}]}


def test_a_short_injury_report_is_a_failed_fetch(monkeypatch):
    """A broken response must not replace the stored return dates and mark everyone healthy."""
    served = {}
    monkeypatch.setattr(client.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(json.dumps(served).encode()))
    served.update(_feed(client.MIN_INJURIES))
    assert len(client.fetch_injuries()) == client.MIN_INJURIES
    served.update(_feed(3))
    with pytest.raises(ValueError, match="at least"):
        client.fetch_injuries()
