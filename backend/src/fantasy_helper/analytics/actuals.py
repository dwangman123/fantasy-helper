"""A matchup's totals so far, summed from box scores rather than taken from ESPN.

ESPN's matchup totals may or may not include games in progress, so adding a
projection of today on top of them could count today twice. Summing our own box
scores (completed days, plus today's lines so far) avoids the question: a game
counts only when its player was in a starting slot that day, the same rule ESPN
scores by.
"""

from collections.abc import Iterable

from fantasy_helper.analytics.lineup import NON_STARTING_SLOTS
from fantasy_helper.analytics.zscores import pct
from fantasy_helper.categories import Category
from fantasy_helper.domain import STAT_KEYS, Matchup, PlayerGameStat


def week_actuals(games: Iterable[PlayerGameStat]) -> dict[int, dict[str, float]]:
    """Fantasy team -> summed STAT_KEYS over its started games."""
    totals: dict[int, dict[str, float]] = {}
    for g in games:
        if g.team_id is None or g.lineup_slot is None or g.lineup_slot in NON_STARTING_SLOTS:
            continue
        team = totals.setdefault(g.team_id, dict.fromkeys(STAT_KEYS, 0.0))
        for k in STAT_KEYS:
            team[k] += g.stats.get(k, 0.0)
    return totals


def category_values(totals: dict[str, float], categories: list[Category]) -> dict[str, float | None]:
    """Category key -> value; a percentage is None until there's an attempt."""
    out: dict[str, float | None] = {}
    for cat in categories:
        if cat.is_percentage:
            attempts = totals.get(cat.attempts, 0.0)
            out[cat.key] = pct(totals.get(cat.makes, 0.0), attempts) if attempts else None
        else:
            out[cat.key] = totals.get(cat.key, 0.0)
    return out


def category_results(mine: dict[str, float], theirs: dict[str, float], categories: list[Category]) -> dict[str, str]:
    """A finished week's categories from one side: win, tie or loss (fewer wins a reversed
    category), compared at full precision. Over weeks 1-2 of 2026 this matched ESPN's own
    result in all 90 matchup categories."""
    out = {}
    for cat in categories:
        a, b = mine.get(cat.key, 0.0), theirs.get(cat.key, 0.0)
        out[cat.key] = "tie" if a == b else "win" if (a < b) == cat.reverse else "loss"
    return out


def espn_mismatches(matchups: list[Matchup], completed: list[PlayerGameStat], live: list[PlayerGameStat]) -> list[str]:
    """Teams whose ESPN matchup totals equal neither our completed days nor completed
    days plus today so far. Skipped while ESPN shows nothing yet (a week's first day)."""
    before = week_actuals(completed)
    with_today = week_actuals([*completed, *live])
    out = []
    for mu in matchups:
        for team_id, espn in ((mu.home_team_id, mu.home_cats), (mu.away_team_id, mu.away_cats)):
            keys = [k for k in STAT_KEYS if k in espn]
            if team_id is None or not any(espn[k] for k in keys):
                continue
            ours = [before.get(team_id, {}), with_today.get(team_id, {})]
            if not any(all(abs(espn[k] - t.get(k, 0.0)) < 1e-6 for k in keys) for t in ours):
                diff = {k: round(espn[k] - ours[0].get(k, 0.0), 3) for k in keys if abs(espn[k] - ours[0].get(k, 0.0))}
                out.append(f"Team {team_id}: ESPN's matchup totals differ from its box scores by {diff}")
    return out
