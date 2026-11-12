"""Per-category player z-scores over one stat window, and the G-score value built from them.

Counting cats are z-scored on per-game averages. Percentage cats are z-scored on
volume-weighted impact, makes - league% x attempts, so a 60% shooter on 2 attempts
is worth less than a 50% shooter on 20. Reversed cats (TO) are negated so a higher
z is always better.

Means and standard deviations come from a baseline pool (usually rostered players),
not every player passed in: the free-agent pool would otherwise drag the mean down
and inflate everyone's z. Standard deviation is the population sd.

A player's value is his G-score (Rosenof, arXiv:2307.02188): each category's z times
1 / sqrt(1 + tau^2 / sigma^2), where sigma is the spread between players (the sd z
divides by) and tau^2 is the weekly bounce, how much a player's per-game average moves
from week to week. A head-to-head week is partly luck in a category that bounces a lot,
so an edge there counts for less. The bounce is fitted from last season's weeks
(`fit_bounce.py`); the weights are scaled to average 1. With no fitted bounce the value
is the plain z-sum.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from math import sqrt
from pathlib import Path
from statistics import fmean, pstdev

from fantasy_helper.calendar import DATA_DIR
from fantasy_helper.categories import Category
from fantasy_helper.domain import StatLine


@dataclass(frozen=True)
class Baseline:
    mean: float
    sd: float
    league_pct: float | None = None  # percentage cats only: baseline makes / attempts


@dataclass(frozen=True)
class WeeklyBounce:
    var: float  # tau^2: pool average of a player's variance of weekly per-game value
    pool_mean: float  # the fitted season's pool per-game mean, to carry a counting cat over


@dataclass
class PlayerZ:
    player_id: int
    by_cat: dict[str, float]  # category key -> z (higher is better)
    weights: dict[str, float] = field(default_factory=dict)  # category key -> G-score weight; missing = 1

    @property
    def value(self) -> float:
        """G-score: the sum of each category's z times its weight."""
        return sum(z * self.weights.get(k, 1.0) for k, z in self.by_cat.items())


def raw_value(per_game: dict[str, float], cat: Category, league_pct: float | None = None) -> float:
    """The per-game number a category's z is computed on (impact for percentage cats)."""
    if cat.is_percentage:
        return per_game[cat.makes] - (league_pct or 0.0) * per_game[cat.attempts]
    return per_game[cat.key]


def pct(makes: float, attempts: float) -> float:
    return makes / attempts if attempts else 0.0


def baselines(lines: list[StatLine], categories: list[Category]) -> dict[str, Baseline]:
    out = {}
    for cat in categories:
        league_pct = None
        if cat.is_percentage:
            league_pct = pct(sum(s.per_game[cat.makes] for s in lines), sum(s.per_game[cat.attempts] for s in lines))
        values = [raw_value(s.per_game, cat, league_pct) for s in lines]
        out[cat.key] = Baseline(fmean(values), pstdev(values), league_pct) if values else Baseline(0.0, 0.0, league_pct)
    return out


def player_zscores(
    lines: list[StatLine],
    categories: list[Category],
    baseline_ids: Iterable[int] | None = None,
    weights: dict[str, float] | None = None,
) -> dict[int, PlayerZ]:
    """Z-score every line against the baseline pool (all lines when baseline_ids is None).
    `weights` (from `gscore_weights`) turn each player's value into a G-score.

    All lines must come from the same window.
    """
    windows = {s.window for s in lines}
    if len(windows) > 1:
        raise ValueError(f"Stat lines mix windows {sorted(windows)}; filter to one first")
    if baseline_ids is None:
        pool = lines
    else:
        ids = set(baseline_ids)
        pool = [s for s in lines if s.player_id in ids]
    base = baselines(pool, categories)
    out = {}
    for s in lines:
        by_cat = {}
        for cat in categories:
            b = base[cat.key]
            z = (raw_value(s.per_game, cat, b.league_pct) - b.mean) / b.sd if b.sd else 0.0
            by_cat[cat.key] = -z if cat.reverse else z
        out[s.player_id] = PlayerZ(s.player_id, by_cat, weights or {})
    return out


def gscore_weights(
    base: dict[str, Baseline], bounce: dict[str, WeeklyBounce], categories: list[Category]
) -> dict[str, float]:
    """Each category's G-score weight, 1 / sqrt(1 + tau^2 / sigma^2), scaled so the weights
    average 1. sigma is this pool's sd (`base`, the one z divides by); tau^2 is the fitted
    weekly bounce, which for a counting cat grows with its average, so it is scaled by this
    pool's per-game mean over the fitted season's. A category without a fitted bounce or
    with no spread starts at 1. No bounce at all gives {} (plain z-sum)."""
    if not bounce:
        return {}
    raw = {}
    for cat in categories:
        b, fitted = base.get(cat.key), bounce.get(cat.key)
        if b is None or fitted is None or not b.sd:
            raw[cat.key] = 1.0
            continue
        tau2 = fitted.var
        if not cat.is_percentage and fitted.pool_mean > 0 and b.mean > 0:
            tau2 *= b.mean / fitted.pool_mean
        raw[cat.key] = 1 / sqrt(1 + tau2 / b.sd**2)
    scale = fmean(raw.values())
    return {k: w / scale for k, w in raw.items()}


def bounce_path(season: int, data_dir: Path = DATA_DIR) -> Path:
    return data_dir / f"weekly_bounce_{season}.json"


def load_weekly_bounce(season: int, data_dir: Path = DATA_DIR) -> dict[str, WeeklyBounce]:
    """The weekly bounce fitted on the latest season before `season`; {} if none is checked in."""
    fitted = []
    for path in data_dir.glob("weekly_bounce_*.json"):
        suffix = path.stem.removeprefix("weekly_bounce_")
        if suffix.isdigit() and int(suffix) < season:
            fitted.append((int(suffix), path))
    if not fitted:
        return {}
    data = json.loads(max(fitted)[1].read_text())
    return {k: WeeklyBounce(c["weekly_var"], c["pool_mean"]) for k, c in data["categories"].items()}


# A player is a specialist in a category he's at least this many standard deviations above
# the rostered average in; a few elite players clear it in four or five, so show the top two.
SPECIALTY_Z, SPECIALTIES_SHOWN = 1.0, 2


def specialties(by_cat: dict[str, float]) -> list[str]:
    """His standout categories, strongest first."""
    strong = sorted((k for k, z in by_cat.items() if z >= SPECIALTY_Z), key=lambda k: -by_cat[k])
    return strong[:SPECIALTIES_SHOWN]
