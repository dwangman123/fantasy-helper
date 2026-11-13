"""Power rankings: every team against every other over the remaining weeks (all-play),
next to how each has done so far.

Strength is the expected share of weekly matchups a team would win if it played all
nine other teams every remaining week: each pair's per-category P(win) from the
same normal approximation as Hold (`waivers.weekly_cat_p_win`), turned into
P(win the week) by `trades.week_odds`. A tie counts half. Rosters are as they are
now, with injured players counted from their expected return (`LeagueContext.return_period`).

The all-play record so far compares each completed week's category totals with
every other team's that week, so it shows how a team's results hold up against
the whole league rather than one schedule.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import permutations

import numpy as np

from fantasy_helper.analytics.actuals import category_values
from fantasy_helper.analytics.trades import week_odds
from fantasy_helper.analytics.waivers import weekly_cat_p_win
from fantasy_helper.categories import Category
from fantasy_helper.domain import Matchup


@dataclass
class Record:
    wins: int = 0
    losses: int = 0
    ties: int = 0


def matchup_outcome(a: dict[str, float | None], b: dict[str, float | None], categories: list[Category]) -> int:
    """1 if `a` wins more categories than `b`, -1 if fewer, 0 for a tie. An equal or
    missing category value is a category tie."""
    margin = 0
    for cat in categories:
        x, y = a.get(cat.key), b.get(cat.key)
        if x is not None and y is not None and x != y:
            margin += 1 if (x < y) == cat.reverse else -1
    return (margin > 0) - (margin < 0)


def all_play_records(results: list[Matchup], categories: list[Category]) -> dict[int, Record]:
    """Team -> all-play record over the weeks in `results` (completed matchups only)."""
    weeks: dict[int, dict[int, dict[str, float | None]]] = defaultdict(dict)
    for mu in results:
        sides = [(mu.home_team_id, mu.home_cats), (mu.away_team_id, mu.away_cats)]
        for team_id, cats in sides:
            if team_id is not None and cats:
                weeks[mu.matchup_period][team_id] = category_values(cats, categories)
    records: dict[int, Record] = defaultdict(Record)
    for teams in weeks.values():
        for a, b in permutations(teams, 2):
            outcome = matchup_outcome(teams[a], teams[b], categories)
            r = records[a]
            if outcome > 0:
                r.wins += 1
            elif outcome < 0:
                r.losses += 1
            else:
                r.ties += 1
    return dict(records)


@dataclass
class PowerRow:
    team_id: int
    rank: int
    strength: float  # expected share of weekly matchups won against every other team (ties half)
    cats_per_week: float  # expected categories won per week, averaged over the other teams
    cat_p_win: dict[str, float]  # mean P(win) per category against the other teams
    all_play: Record = field(default_factory=Record)  # so far, over completed weeks


def scheduled_odds(
    home: np.ndarray, away: np.ndarray, categories: list[Category], dispersion: dict[str, float]
) -> tuple[float, float]:
    """The home team's P(win) and P(tie) in one week, from both teams' expected totals
    that week (1 x STAT_KEYS), the same way strength is computed."""
    win, tie = week_odds(weekly_cat_p_win(home, away, categories, dispersion))
    return float(win[0]), float(tie[0])


@dataclass
class Projection:
    wins: float
    losses: float
    ties: float


def projected_records(so_far: dict[int, Record], games: list[tuple[int, int, float, float]]) -> dict[int, Projection]:
    """Each team's record so far plus its expected wins, losses and ties over `games`, the
    matchups still to play: (home, away, P(home wins), P(tie))."""
    out = {t: Projection(float(r.wins), float(r.losses), float(r.ties)) for t, r in so_far.items()}
    for home, away, p_home, p_tie in games:
        p_away = 1.0 - p_home - p_tie
        for team, win, loss in ((home, p_home, p_away), (away, p_away, p_home)):
            proj = out.setdefault(team, Projection(0.0, 0.0, 0.0))
            proj.wins += win
            proj.losses += loss
            proj.ties += p_tie
    return out


def power_rankings(
    categories: list[Category],
    weekly: dict[int, np.ndarray],
    dispersion: dict[str, float],
    so_far: dict[int, Record],
) -> list[PowerRow]:
    """`weekly`: each team's expected totals per remaining week (weeks x STAT_KEYS)."""
    rows = []
    for a in weekly:
        share, cats, by_cat = [], [], defaultdict(list)
        for b in weekly:
            if b == a:
                continue
            p = weekly_cat_p_win(weekly[a], weekly[b], categories, dispersion)
            win, tie = week_odds(p)
            share.append(float((win + tie / 2).mean()))
            cats.append(float(sum(p.values()).mean()))
            for k, v in p.items():
                by_cat[k].append(float(v.mean()))
        rows.append(
            PowerRow(
                team_id=a,
                rank=0,
                strength=float(np.mean(share)),
                cats_per_week=float(np.mean(cats)),
                cat_p_win={k: float(np.mean(v)) for k, v in by_cat.items()},
                all_play=so_far.get(a, Record()),
            )
        )
    rows.sort(key=lambda r: r.strength, reverse=True)
    for i, r in enumerate(rows, 1):
        r.rank = i
    return rows
