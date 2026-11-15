"""Half-life fit (eval/halflife.py): synthetic pass pairs with known t_half."""

import numpy as np
import pandas as pd
import pytest

from horus.eval import halflife


def test_recovers_known_half_life() -> None:
    rng = np.random.default_rng(0)
    n, base, a, th = 40_000, 0.02, 0.3, 720.0
    dt = rng.uniform(0, 7200, n)
    y = rng.random(n) < halflife.model(dt, a, th, base)
    pr = pd.DataFrame({"dt_s": dt, "y": y, "fl": 0, "day": rng.integers(0, 20, n)})
    f = halflife.fit(pr, base, 300.0, 7200.0, 50, np.random.default_rng(1))
    assert f.t_half_s == pytest.approx(720.0, abs=90.0)  # 12 +- 1.5 min
    assert f.a == pytest.approx(0.3, abs=0.05)
    assert f.ci_low_s < 720.0 < f.ci_high_s


def test_pairs_same_cell_and_bin_other_aircraft_within_window() -> None:
    p = pd.DataFrame(
        {
            "icao24": ["a", "b", "a", "c", "d", "e"],
            "cell": [1, 1, 1, 1, 2, 1],
            "fl": [0, 0, 0, 0, 0, 1],
            "t0": [0, 120, 300, 9000, 60, 60],
            "t1": [60, 180, 360, 9060, 120, 120],
            "day": 0,
        }
    )
    det = np.array([True, False, False, True, False, False])
    pr = halflife.pairs(p, det, 7200.0)
    # from pass a@0: b (dt 60) counts; a again is the same aircraft; c is too late;
    # d is another cell; e is another FL bin
    assert pr["dt_s"].tolist() == [60.0]
    assert pr["y"].tolist() == [False]
