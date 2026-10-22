"""Matchup calendar: which days (ESPN scoring periods) belong to which matchup week.

ESPN's API does not expose matchup date ranges: settings.matchupPeriods lists
week 18 as a single unit even though it spans the 14-day All-Star break, and
league.matchup_ids only fills in after games are played. So the calendar is a
checked-in data file per season, cross-checked against completed weeks by
`validate_against_espn`.

A scoring period is one day; scoring period 1 is opening night.
"""

import json
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from fantasy_helper.config import BACKEND_DIR

DATA_DIR = BACKEND_DIR / "data"


@dataclass(frozen=True)
class MatchupWeek:
    matchup_period: int
    start: date
    end: date  # inclusive
    playoff: bool
    first_scoring_period: int
    last_scoring_period: int  # inclusive

    @property
    def scoring_periods(self) -> range:
        return range(self.first_scoring_period, self.last_scoring_period + 1)

    @property
    def days(self) -> int:
        return self.last_scoring_period - self.first_scoring_period + 1


class MatchupCalendar:
    def __init__(self, first_day: date, weeks: list[MatchupWeek]):
        self.first_day = first_day
        self.weeks = sorted(weeks, key=lambda w: w.matchup_period)
        self._by_period = {w.matchup_period: w for w in self.weeks}

    @classmethod
    def from_dict(cls, data: dict) -> "MatchupCalendar":
        first_day = date.fromisoformat(data["first_scoring_period_date"])
        weeks = []
        for m in data["matchups"]:
            start, end = date.fromisoformat(m["start"]), date.fromisoformat(m["end"])
            weeks.append(
                MatchupWeek(
                    matchup_period=m["matchup_period"],
                    start=start,
                    end=end,
                    playoff=m.get("playoff", False),
                    first_scoring_period=(start - first_day).days + 1,
                    last_scoring_period=(end - first_day).days + 1,
                )
            )
        cal = cls(first_day, weeks)
        cal._check_contiguous()
        return cal

    @classmethod
    def load(cls, season: int, data_dir: Path = DATA_DIR) -> "MatchupCalendar":
        path = data_dir / f"matchup_calendar_{season}.json"
        if not path.exists():
            raise FileNotFoundError(f"No matchup calendar for season {season}: expected {path}")
        return cls.from_dict(json.loads(path.read_text()))

    def _check_contiguous(self) -> None:
        expected = 1
        for w in self.weeks:
            if w.first_scoring_period != expected or w.last_scoring_period < w.first_scoring_period:
                raise ValueError(f"Matchup calendar gap or overlap at matchup period {w.matchup_period}")
            expected = w.last_scoring_period + 1

    def week(self, matchup_period: int) -> MatchupWeek:
        return self._by_period[matchup_period]

    def date_for(self, scoring_period: int) -> date:
        return self.first_day + timedelta(days=scoring_period - 1)

    def scoring_period_for(self, day: date) -> int:
        return (day - self.first_day).days + 1

    def week_for_scoring_period(self, scoring_period: int) -> MatchupWeek | None:
        for w in self.weeks:
            if scoring_period in w.scoring_periods:
                return w
        return None

    def validate_against_espn(self, matchup_ids: dict[int, list[str]]) -> list[str]:
        """Compare against ESPN's played scoring periods per matchup (league.matchup_ids).

        Returns a list of problems; empty means the calendar agrees with every
        scoring period ESPN has recorded so far.
        """
        problems = []
        for period, sps in matchup_ids.items():
            week = self._by_period.get(int(period))
            if week is None:
                problems.append(f"ESPN has matchup period {period} but the calendar does not")
                continue
            outside = [int(sp) for sp in sps if int(sp) not in week.scoring_periods]
            if outside:
                problems.append(f"Matchup {period}: scoring periods {outside} fall outside {week.start}..{week.end}")
        return problems


@lru_cache
def load_calendar(season: int) -> MatchupCalendar:
    return MatchupCalendar.load(season)
