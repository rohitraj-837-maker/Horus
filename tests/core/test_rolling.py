import numpy as np
import pandas as pd
import pytest

from turbcast.core import rolling


@pytest.mark.parametrize(
    "w_s,step,expected", [(15, 1, 15), (31, 1, 31), (120, 20, 7), (15, 20, 1), (16, 1, 17)]
)
def test_window_samples_odd(w_s, step, expected):
    assert rolling.window_samples(w_s, step) == expected


def test_weighted_var_unit_weights_matches_pandas():
    x = np.random.default_rng(0).normal(size=200)
    got = rolling.weighted_var(x, np.ones_like(x), 15, 15)
    want = pd.Series(x).rolling(15, center=True).var(ddof=0).to_numpy()
    np.testing.assert_allclose(got, want, equal_nan=True, atol=1e-12)


def test_weighted_var_formula():
    x = np.array([0.0, 64.0, 0.0])
    w = np.array([1.0, 0.5, 1.0])
    got = rolling.weighted_var(x, w, 3, 3)[1]
    mean = 32.0 / 2.5
    want = (0.5 * (64 - mean) ** 2 + 2 * mean**2) / 2.5
    assert got == pytest.approx(want)


def test_min_count_gives_nan():
    x = np.array([1.0, np.nan, np.nan, np.nan, 2.0])
    assert np.isnan(rolling.weighted_var(x, np.ones(5), 3, 2)[2])
    assert rolling.count(x, 5)[2] == 2
