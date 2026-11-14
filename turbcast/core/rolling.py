"""NaN-aware centred rolling statistics over a uniform time grid.

Windows are given in samples (callers convert seconds -> samples by the grid step).
A window's statistic is NaN unless it holds at least `min_count` observed values,
which is how irregular or sparse data (adsb.lol ~20 s, live ~90 s) is handled
without interpolation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.typing import NDArray

F = NDArray[np.float64]


def window_samples(window_s: float, step_s: float) -> int:
    """Odd sample count covering `window_s` at grid step `step_s` (>= 1)."""
    n = max(1, int(round(window_s / step_s)))
    return n if n % 2 else n + 1


def _roll(x: NDArray[np.floating], w: int, min_count: int) -> pd.api.typing.Rolling:
    return pd.Series(np.asarray(x, dtype=float)).rolling(
        w, center=True, min_periods=max(1, min_count)
    )


def count(x: NDArray[np.floating], w: int) -> F:
    return np.asarray(_roll(np.isfinite(x).astype(float), w, 1).sum().to_numpy())


def median(x: NDArray[np.floating], w: int, min_count: int) -> F:
    return np.asarray(_roll(x, w, min_count).median().to_numpy())


def minimum(x: NDArray[np.floating], w: int, min_count: int) -> F:
    return np.asarray(_roll(x, w, min_count).min().to_numpy())


def maximum(x: NDArray[np.floating], w: int, min_count: int) -> F:
    return np.asarray(_roll(x, w, min_count).max().to_numpy())


def weighted_var(
    x: NDArray[np.floating], weights: NDArray[np.floating], w: int, min_count: int
) -> F:
    """Population variance with per-sample weights: (S_wxx - S_wx^2 / S_w) / S_w."""
    x = np.asarray(x, dtype=float)
    obs = np.isfinite(x)
    wt = np.where(obs, np.asarray(weights, dtype=float), 0.0)
    xv = np.where(obs, x, 0.0)
    sw = pd.Series(wt).rolling(w, center=True, min_periods=1).sum().to_numpy()
    swx = pd.Series(wt * xv).rolling(w, center=True, min_periods=1).sum().to_numpy()
    swxx = pd.Series(wt * xv * xv).rolling(w, center=True, min_periods=1).sum().to_numpy()
    n = count(x, w)
    with np.errstate(invalid="ignore", divide="ignore"):
        var = (swxx - swx * swx / sw) / sw
    var = np.maximum(var, 0.0)  # floating-point round-off
    return np.where((n >= min_count) & (sw > 0), var, np.nan)


def mean(x: NDArray[np.floating], w: int, min_count: int) -> F:
    return np.asarray(_roll(x, w, min_count).mean().to_numpy())


def mean_square(x: NDArray[np.floating], w: int, min_count: int) -> F:
    x = np.asarray(x, dtype=float)
    return np.asarray(_roll(x * x, w, min_count).mean().to_numpy())
