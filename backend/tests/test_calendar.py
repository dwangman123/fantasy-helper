from datetime import date

import pytest

from fantasy_helper.calendar import MatchupCalendar


@pytest.fixture
def cal():
    return MatchupCalendar.load(2027)


def test_scoring_period_date_roundtrip(cal):
    assert cal.date_for(1) == date(2026, 10, 20)
    # Verified against ESPN: scoring period 130 = Feb 26, final period 167 = Apr 4
    assert cal.date_for(130) == date(2027, 2, 26)
    assert cal.scoring_period_for(date(2027, 4, 4)) == 167


def test_irregular_weeks(cal):
    assert cal.week(1).days == 6  # short opening week, Tue-Sun
    assert cal.week(2).days == 7
    assert cal.week(18).days == 14  # All-Star break double week
    assert cal.week(20).playoff and cal.week(20).days == 14
    assert cal.weeks[-1].last_scoring_period == 167  # matches ESPN finalScoringPeriod


def test_week_lookup(cal):
    assert cal.week_for_scoring_period(6).matchup_period == 1
    assert cal.week_for_scoring_period(7).matchup_period == 2
    assert cal.week_for_scoring_period(500) is None


def test_validate_against_espn(cal):
    assert cal.validate_against_espn({1: ["1", "2", "6"], 2: ["7"]}) == []
    problems = cal.validate_against_espn({1: ["7"], 30: ["1"]})
    assert len(problems) == 2


def test_gap_is_rejected():
    data = {
        "first_scoring_period_date": "2026-10-20",
        "matchups": [
            {"matchup_period": 1, "start": "2026-10-20", "end": "2026-10-25"},
            {"matchup_period": 2, "start": "2026-10-27", "end": "2026-11-01"},
        ],
    }
    with pytest.raises(ValueError, match="gap"):
        MatchupCalendar.from_dict(data)
