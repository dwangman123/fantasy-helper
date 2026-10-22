"""Per-category player z-scores over one stat window.

Counting cats are z-scored on per-game averages. Percentage cats are z-scored on
volume-weighted impact, makes - league% x attempts, so a 60% shooter on 2 attempts
is worth less than a 50% shooter on 20. Reversed cats (TO) are negated so a higher
z is always better.

Means and standard deviations come from a baseline pool (usually rostered players),
not every player passed in: the free-agent pool would otherwise drag the mean down
and inflate everyone's z. Standard deviation is the population sd.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from statistics import fmean, pstdev

from fantasy_helper.categories import Category
from fantasy_helper.domain import StatLine


@dataclass(frozen=True)
class Baseline:
    mean: float
    sd: float
    league_pct: float | None = None  # percentage cats only: baseline makes / attempts


@dataclass
class PlayerZ:
    player_id: int
    by_cat: dict[str, float]  # category key -> z (higher is better)

    @property
    def total(self) -> float:
        return sum(self.by_cat.values())


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
) -> dict[int, PlayerZ]:
    """Z-score every line against the baseline pool (all lines when baseline_ids is None).

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
        out[s.player_id] = PlayerZ(s.player_id, by_cat)
    return out
