"""Empirical vertical-rate quantisation (SPEC open question 1)."""

from __future__ import annotations

import numpy as np
import pandas as pd

GRID_TOL_FPM = 2.0  # OpenSky stores m/s to 2 dp = up to ~1 ft/min rounding


def on_grid_fraction(vr: np.ndarray, step: float, tol: float = GRID_TOL_FPM) -> float:
    vr = vr[np.isfinite(vr)]
    if not len(vr):
        return float("nan")
    return float(np.mean(np.abs(vr - step * np.round(vr / step)) <= tol))


def estimate_step(vr: np.ndarray, candidates: range = range(8, 257), min_frac: float = 0.95) -> int:
    """Largest step whose grid holds >= `min_frac` of non-zero values (0 if none).
    Multiples of 64 also lie on the 32 and 16 grids, so the largest passing step wins."""
    v = vr[np.isfinite(vr) & (vr != 0)]
    best = 0
    for s in candidates:
        if on_grid_fraction(v, float(s)) >= min_frac:
            best = s
    return best


def consecutive_deltas(df: pd.DataFrame) -> np.ndarray:
    """|delta vr| between consecutive samples of the same aircraft (non-zero only)."""
    d = df.sort_values(["icao24", "time"])
    dv = d.groupby("icao24")["vr_fpm"].diff().abs().to_numpy(dtype=float)
    return dv[np.isfinite(dv) & (dv > 0)]


def summarise(df: pd.DataFrame, group: str = "msg_type") -> pd.DataFrame:
    rows = []
    for key, g in df.groupby(df[group].fillna("unknown")):
        vr = g["vr_fpm"].to_numpy(dtype=float)
        vr = vr[np.isfinite(vr)]
        dv = consecutive_deltas(g)
        rows.append(
            {
                group: key,
                "n": len(vr),
                "aircraft": g["icao24"].nunique(),
                "zero_frac": float(np.mean(vr == 0)) if len(vr) else np.nan,
                "on_64_grid": on_grid_fraction(vr, 64.0),
                "est_step_fpm": estimate_step(vr),
                "min_delta_mode": float(pd.Series(np.round(dv)).mode().min())
                if len(dv)
                else np.nan,
            }
        )
    return pd.DataFrame(rows).sort_values("n", ascending=False).reset_index(drop=True)
