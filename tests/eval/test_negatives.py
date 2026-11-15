"""No-PIREP window sampling (eval/negatives.py)."""

import numpy as np
import pandas as pd

from horus.eval import negatives


def test_samples_only_clear_rows_with_pirep_window_shape() -> None:
    obs = pd.DataFrame(
        {
            "minute": np.arange(100) * 60,
            "lat": np.linspace(30, 40, 100),
            "lon": np.full(100, -100.0),
            "alt_ft": np.full(100, 35000.0),
        }
    )
    clear = np.arange(100) % 2 == 0
    w = negatives.sample_negative_windows(obs, clear, 20, np.random.default_rng(0))
    assert len(w) == 20
    assert set(w.columns) == {"time", "lat", "lon", "alt_lo_ft", "alt_hi_ft"}
    assert ((w["time"] - 30) // 60 % 2 == 0).all()  # only clear (even) minutes
    assert (w["alt_lo_ft"] == w["alt_hi_ft"]).all()
    again = negatives.sample_negative_windows(obs, clear, 20, np.random.default_rng(0))
    assert w.equals(again)  # deterministic given the RNG
    assert len(negatives.sample_negative_windows(obs, clear, 500, np.random.default_rng(0))) == 50
