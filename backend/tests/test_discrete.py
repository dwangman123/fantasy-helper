import numpy as np
import pytest
from scipy.stats import binom, nbinom, poisson

from fantasy_helper.analytics.discrete import binom_ppf, nbinom_ppf, poisson_ppf

RNG = np.random.default_rng(0)
N = 20000
U = np.clip(RNG.random(N), 1e-12, 1 - 1e-12)
MEAN = RNG.choice([0.02, 0.5, 2.0, 8.0, 35.0, 160.0, 900.0], N) * RNG.uniform(0.5, 1.5, N)


def test_poisson_matches_scipy():
    assert (poisson_ppf(U, MEAN) == poisson.ppf(U, MEAN)).all()


@pytest.mark.parametrize("phi", [1.05, 1.6, 4.0])
def test_negative_binomial_matches_scipy(phi):
    assert (nbinom_ppf(U, MEAN, phi) == nbinom.ppf(U, MEAN / (phi - 1), 1 / phi)).all()


def test_binomial_matches_scipy_including_certain_outcomes():
    n = poisson.ppf(U, MEAN)
    p = RNG.choice([0.0, 0.15, 0.47, 0.8, 1.0], N)
    u = np.clip(RNG.random(N), 1e-12, 1 - 1e-12)
    assert (binom_ppf(u, n, p) == binom.ppf(u, n, p)).all()
