"""Exact inverse CDFs (quantile functions) for Poisson, negative binomial and binomial
draws, vectorized over parameters.

The matchup simulation draws every team total by inverse CDF so that two runs can
share their uniforms (common random numbers). scipy.stats' ppf is exact but slow
for thousands of draws with different means, so the search starts from a
Cornish-Fisher guess (mean + sd * (z + (z^2 - 1) * skew / 6)) and steps one count
at a time until it holds the smallest k with CDF(k) >= u. The guess is usually
within a count or two, so this takes a few vectorized CDF evaluations.
"""

from collections.abc import Callable

import numpy as np
from scipy.special import betainc, ndtri, pdtr

# cdf(k, rows): the CDF at k for the given rows of the parameter arrays
_Cdf = Callable[[np.ndarray, np.ndarray], np.ndarray]


def _search(u: np.ndarray, mean: np.ndarray, var: np.ndarray, skew: np.ndarray, cdf: _Cdf) -> np.ndarray:
    z = ndtri(u)
    guess = mean + np.sqrt(var) * (z + (z * z - 1.0) * skew / 6.0)
    k = np.maximum(np.floor(np.nan_to_num(guess, nan=0.0)), 0.0)
    rows = np.arange(len(u))
    short = cdf(k, rows) < u
    up = rows[short]
    while up.size:
        k[up] += 1.0
        up = up[cdf(k[up], up) < u[up]]
    # Rows that stepped up already know CDF(k - 1) < u; only the others may be too high.
    down = rows[~short & (k > 0)]
    down = down[cdf(k[down] - 1.0, down) >= u[down]]
    while down.size:
        k[down] -= 1.0
        down = down[k[down] > 0]
        down = down[cdf(k[down] - 1.0, down) >= u[down]]
    return k


def _skew(numerator: np.ndarray, var: np.ndarray) -> np.ndarray:
    return np.divide(numerator, np.sqrt(var), out=np.zeros_like(var), where=var > 0)


def poisson_ppf(u: np.ndarray, mean: np.ndarray) -> np.ndarray:
    return _search(u, mean, mean, _skew(np.ones_like(mean), mean), lambda k, r: pdtr(k, mean[r]))


def nbinom_ppf(u: np.ndarray, mean: np.ndarray, phi: float) -> np.ndarray:
    """Negative binomial with variance phi * mean (phi > 1): n = mean / (phi - 1), p = 1 / phi."""
    n, p = mean / (phi - 1.0), 1.0 / phi
    var = phi * mean
    return _search(u, mean, var, _skew(np.full_like(mean, 2.0 * phi - 1.0), var), lambda k, r: betainc(n[r], k + 1, p))


def _binom_cdf(k: np.ndarray, n: np.ndarray, p: np.ndarray) -> np.ndarray:
    """P(X <= k) = I_{1-p}(n - k, k + 1) for k < n, and 1 from n on."""
    out = np.ones_like(k)
    below = k < n
    out[below] = betainc(n[below] - k[below], k[below] + 1.0, 1.0 - p[below])
    return out


def binom_ppf(u: np.ndarray, n: np.ndarray, p: np.ndarray) -> np.ndarray:
    mean, var = n * p, n * p * (1.0 - p)
    return _search(u, mean, var, _skew(1.0 - 2.0 * p, var), lambda k, r: _binom_cdf(k, n[r], p[r]))
