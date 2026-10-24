"""Head-to-head matchup prediction: actual totals so far plus a Monte Carlo of the
days left in the week.

Each draw walks the remaining days. A player with a game that day plays with
probability `p_play`, and the league's starting slots then decide who starts
(`lineup.assign_starters`, highest priority first), since benched games don't
count. A started game adds the player's per-game rate times an optional per-stat
factor for that game (e.g. an opponent adjustment).

Team totals are sampled per draw rather than player by player: a sum of Poissons
is Poisson, and a sum of negative binomials with variance = phi * mean is again
negative binomial, so only the summed mean is needed. Makes are binomial given
attempts at the team's expected percentage for that draw. Stats are drawn
independently of each other (PTS and FGM are not correlated).

Common random numbers: comparing two rosters (a waiver swap against the current
one) only works if both runs share their randomness; otherwise a 1-2 point change
in P(win) is simulation noise. So nothing is drawn from one shared stream. A
player's chance of playing on a day uses uniforms seeded by (seed, side, player,
day), so adding or removing someone leaves everyone else's availability alone.
Team totals are inverse-CDF draws from uniforms seeded by (seed, side, stat), so
a small change to a team's expected total moves each draw a little instead of
redrawing it.

H2H most-categories: a side wins the matchup by winning more categories; equal
counts are a tie. An exactly equal category total is a category tie.
"""

from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from fantasy_helper.analytics.discrete import binom_ppf, nbinom_ppf, poisson_ppf
from fantasy_helper.analytics.lineup import assign_starters
from fantasy_helper.categories import Category
from fantasy_helper.domain import STAT_KEYS

SIDES = ("home", "away")
# Stream tags, so availability and stat-total draws never share uniforms.
_AVAILABILITY, _TOTALS = 0, 1
_EPS = 1e-12  # keeps uniforms off 0 and 1, where inverse CDFs are degenerate


@dataclass
class SimPlayer:
    player_id: int
    eligible_slots: list[str]
    per_game: dict[str, float]  # keyed by STAT_KEYS
    # Remaining scoring periods with a game -> per-stat multiplier for that game (missing = 1.0)
    games: dict[int, dict[str, float]] = field(default_factory=dict)
    p_play: float = 1.0
    priority: float = 0.0  # higher starts first on crowded days


@dataclass
class SideDraws:
    """One side's simulated week: final value per draw for each category key, plus
    makes and attempts for percentage cats, and started games over the days left."""

    finals: dict[str, np.ndarray]
    games: np.ndarray


@dataclass
class MatchupPrediction:
    p_home: float
    p_tie: float
    p_away: float
    cat_probs: dict[str, dict[str, float]]  # category -> {"home", "tie", "away"}
    expected_score: dict[str, float]  # mean categories won: {"home", "away", "ties"}
    most_likely_score: tuple[int, int, int]  # (home wins, away wins, ties)
    projected_totals: dict[str, dict[str, float]]  # side -> category -> mean final value
    expected_games: dict[str, float]  # side -> mean remaining started games
    n_sims: int


def _uniforms(seed: int, n: int, *key: int) -> np.ndarray:
    return np.clip(np.random.default_rng([seed, *key]).random(n), _EPS, 1.0 - _EPS)


def _available(p: SimPlayer, day: int, n_sims: int, seed: int, side: int) -> np.ndarray:
    if p.p_play >= 1.0:
        return np.ones(n_sims, dtype=bool)
    if p.p_play <= 0.0:
        return np.zeros(n_sims, dtype=bool)
    return _uniforms(seed, n_sims, _AVAILABILITY, side, p.player_id, day) < p.p_play


def _remaining_means(
    players: list[SimPlayer], days: list[int], slot_counts: dict[str, int], n_sims: int, seed: int, side: int
) -> tuple[np.ndarray, np.ndarray]:
    """Per draw: expected remaining totals by stat (n_sims, len(STAT_KEYS)) and started games (n_sims,)."""
    rates = np.array([[p.per_game[k] for k in STAT_KEYS] for p in players]).reshape(len(players), len(STAT_KEYS))
    order = sorted(range(len(players)), key=lambda i: -players[i].priority)
    means = np.zeros((n_sims, len(STAT_KEYS)))
    games = np.zeros(n_sims)
    starters_cache: dict[tuple[int, ...], set[int]] = {}
    for day in days:
        playing = [i for i in order if day in players[i].games]
        if not playing:
            continue
        available = np.column_stack([_available(players[i], day, n_sims, seed, side) for i in playing])
        factors = np.array([[players[i].games[day].get(k, 1.0) for k in STAT_KEYS] for i in playing])
        contrib = rates[playing] * factors
        # Only day-to-day players vary between draws, so there are few distinct patterns.
        # Each row is packed into one integer code to find them quickly.
        codes = available.astype(np.int64) @ (1 << np.arange(len(playing), dtype=np.int64))
        patterns, inverse = np.unique(codes, return_inverse=True)
        for j, code in enumerate(patterns.tolist()):
            avail_ids = tuple(i for bit, i in enumerate(playing) if code >> bit & 1)
            if avail_ids not in starters_cache:
                candidates = [(i, players[i].eligible_slots) for i in avail_ids]
                starters_cache[avail_ids] = set(assign_starters(candidates, slot_counts))
            started = np.array([i in starters_cache[avail_ids] for i in playing])
            rows = inverse == j
            means[rows] += contrib[started].sum(axis=0)
            games[rows] += started.sum()
    return means, games


def _draw_counts(mean: np.ndarray, phi: float, u: np.ndarray) -> np.ndarray:
    """Inverse CDF at `u`: Poisson, or negative binomial with variance phi * mean when phi > 1."""
    mean = np.maximum(mean, 0.0)
    out = np.zeros_like(mean)
    positive = mean > 0
    if phi <= 1.0:
        out[positive] = poisson_ppf(u[positive], mean[positive])
    else:
        out[positive] = nbinom_ppf(u[positive], mean[positive], phi)
    return out


def _final_totals(
    actual: dict[str, float],
    remaining: np.ndarray,
    categories: list[Category],
    dispersion: dict[str, float],
    seed: int,
    side: int,
) -> dict[str, np.ndarray]:
    """Final value per category per draw, plus makes/attempts for percentage cats."""
    col = {k: i for i, k in enumerate(STAT_KEYS)}
    n_sims = remaining.shape[0]

    def u(key: str) -> np.ndarray:
        return _uniforms(seed, n_sims, _TOTALS, side, col[key])

    out = {}
    for cat in categories:
        if cat.is_percentage:
            mean_att, mean_made = remaining[:, col[cat.attempts]], remaining[:, col[cat.makes]]
            attempts = _draw_counts(mean_att, dispersion.get(cat.attempts, 1.0), u(cat.attempts))
            pct = np.divide(mean_made, mean_att, out=np.zeros_like(mean_made), where=mean_att > 0)
            makes = binom_ppf(u(cat.makes), attempts, np.clip(pct, 0.0, 1.0))
            makes += actual.get(cat.makes, 0.0)
            attempts += actual.get(cat.attempts, 0.0)
            out[cat.makes], out[cat.attempts] = makes, attempts
            out[cat.key] = np.divide(makes, attempts, out=np.zeros_like(makes), where=attempts > 0)
        else:
            drawn = _draw_counts(remaining[:, col[cat.key]], dispersion.get(cat.key, 1.0), u(cat.key))
            out[cat.key] = actual.get(cat.key, 0.0) + drawn
    return out


def simulate_side(
    categories: list[Category],
    actual: dict[str, float],
    players: list[SimPlayer],
    days: list[int],
    slot_counts: dict[str, int],
    side: str,
    n_sims: int = 5000,
    seed: int = 0,
    dispersion: dict[str, float] | None = None,
) -> SideDraws:
    """One side of a matchup. `side` ("home" / "away") is part of the random-number key,
    so a roster change is compared against the same side and seed."""
    side_key = SIDES.index(side)
    remaining, games = _remaining_means(players, days, slot_counts, n_sims, seed, side_key)
    return SideDraws(_final_totals(actual, remaining, categories, dispersion or {}, seed, side_key), games)


def score_matchup(categories: list[Category], home: SideDraws, away: SideDraws) -> MatchupPrediction:
    finals = {"home": home.finals, "away": away.finals}
    n_sims = len(home.games)
    wins = {"home": np.zeros(n_sims, dtype=int), "away": np.zeros(n_sims, dtype=int)}
    ties = np.zeros(n_sims, dtype=int)
    cat_probs = {}
    for cat in categories:
        h, a = finals["home"][cat.key], finals["away"][cat.key]
        home_wins = h < a if cat.reverse else h > a
        tied = h == a
        away_wins = ~home_wins & ~tied
        wins["home"] += home_wins
        wins["away"] += away_wins
        ties += tied
        cat_probs[cat.key] = {"home": home_wins.mean(), "tie": tied.mean(), "away": away_wins.mean()}

    scores = Counter(zip(wins["home"].tolist(), wins["away"].tolist(), ties.tolist(), strict=True))
    projected = {}
    for side in SIDES:
        f = finals[side]
        projected[side] = {
            cat.key: (f[cat.makes].mean() / f[cat.attempts].mean() if f[cat.attempts].mean() else 0.0)
            if cat.is_percentage
            else f[cat.key].mean()
            for cat in categories
        }
    return MatchupPrediction(
        p_home=float((wins["home"] > wins["away"]).mean()),
        p_tie=float((wins["home"] == wins["away"]).mean()),
        p_away=float((wins["away"] > wins["home"]).mean()),
        cat_probs={k: {s: float(v) for s, v in d.items()} for k, d in cat_probs.items()},
        expected_score={
            "home": float(wins["home"].mean()),
            "away": float(wins["away"].mean()),
            "ties": float(ties.mean()),
        },
        most_likely_score=scores.most_common(1)[0][0],
        projected_totals={s: {k: float(v) for k, v in d.items()} for s, d in projected.items()},
        expected_games={"home": float(home.games.mean()), "away": float(away.games.mean())},
        n_sims=n_sims,
    )


def simulate_matchup(
    categories: list[Category],
    actual: dict[str, dict[str, float]],
    players: dict[str, list[SimPlayer]],
    days: list[int],
    slot_counts: dict[str, int],
    n_sims: int = 5000,
    seed: int = 0,
    dispersion: dict[str, float] | None = None,
) -> MatchupPrediction:
    """`actual` and `players` are keyed by "home" / "away". `actual` holds totals so far,
    including the FGM/FGA/FTM/FTA components; `days` are the scoring periods still to play.
    `dispersion` is phi (variance / mean) per stat key; missing means Poisson."""
    draws = {
        side: simulate_side(categories, actual[side], players[side], days, slot_counts, side, n_sims, seed, dispersion)
        for side in SIDES
    }
    return score_matchup(categories, draws["home"], draws["away"])
