"""Team category profiles: per-game totals, league ranks and inferred punts.

A team's strength in a category is the sum of its players' per-game averages; for
percentage cats it is total makes over total attempts, never an average of player
percentages. Bench players count (they rotate in on game days); IR slots do not.

Rank alone can't find punts, since some team is always last in a 10-team league.
Instead each category's team values are z-scored across the league, and a team
punts a category when its z falls below a threshold.
"""

from dataclasses import dataclass, field
from statistics import fmean, pstdev

from fantasy_helper.analytics.zscores import pct
from fantasy_helper.categories import Category
from fantasy_helper.domain import RosterEntry, StatLine

EXCLUDED_SLOTS = {"IR"}
# At -1.0 nearly every team "punts" 1-3 cats on real data; -1.5 isolates deliberate punts.
DEFAULT_PUNT_Z = -1.5


@dataclass
class TeamProfile:
    team_id: int
    totals: dict[str, float]  # category key -> per-game team value (a fraction for percentage cats)
    ranks: dict[str, int] = field(default_factory=dict)  # 1 = best, ties share the better rank
    league_z: dict[str, float] = field(default_factory=dict)  # vs. the other teams, higher is better
    punts: list[str] = field(default_factory=list)
    missing_player_ids: list[int] = field(default_factory=list)  # rostered, but no line in this window


def team_totals(lines: list[StatLine], categories: list[Category]) -> dict[str, float]:
    totals = {}
    for cat in categories:
        if cat.is_percentage:
            totals[cat.key] = pct(
                sum(s.per_game[cat.makes] for s in lines), sum(s.per_game[cat.attempts] for s in lines)
            )
        else:
            totals[cat.key] = sum(s.per_game[cat.key] for s in lines)
    return totals


def _ranks(values: dict[int, float], reverse: bool) -> dict[int, int]:
    """Competition ranking (1, 2, 2, 4); for reversed cats the lowest value ranks first."""
    ordered = sorted(values.values(), reverse=not reverse)
    return {team_id: ordered.index(v) + 1 for team_id, v in values.items()}


def team_profiles(
    rosters: list[RosterEntry],
    lines: list[StatLine],
    categories: list[Category],
    punt_z: float = DEFAULT_PUNT_Z,
) -> dict[int, TeamProfile]:
    """Profile every team with a roster. `lines` must all come from one window."""
    by_player = {s.player_id: s for s in lines}
    profiles = {}
    for team_id in sorted({r.team_id for r in rosters}):
        entries = [r for r in rosters if r.team_id == team_id and r.lineup_slot not in EXCLUDED_SLOTS]
        team_lines = [by_player[r.player_id] for r in entries if r.player_id in by_player]
        missing = sorted(r.player_id for r in entries if r.player_id not in by_player)
        profiles[team_id] = TeamProfile(team_id, team_totals(team_lines, categories), missing_player_ids=missing)

    for cat in categories:
        values = {t: p.totals[cat.key] for t, p in profiles.items()}
        ranks = _ranks(values, cat.reverse)
        mean, sd = fmean(values.values()), pstdev(values.values())
        for t, p in profiles.items():
            z = (values[t] - mean) / sd if sd else 0.0
            p.ranks[cat.key] = ranks[t]
            p.league_z[cat.key] = -z if cat.reverse else z
            if p.league_z[cat.key] < punt_z:
                p.punts.append(cat.key)
    return profiles
