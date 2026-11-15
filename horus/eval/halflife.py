"""Half-life of turbulence evidence (SPEC §5.5 rule 1), fitted from pass pairs.

After a detected pass through a cell, take each later pass by another aircraft
through the same cell and FL bin within `max_dt_s`. The chance that it detects too
decays toward the base rate:

    P(detect_2 | detect_1, dt) = base + A * exp(-ln2 * dt / t_half)

Fitted by weighted least squares on dt bins (scipy curve_fit), with a day-block
bootstrap for the 95% interval. `base` is fixed to the unconditional pass
detection rate, so only A and t_half are free.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

LN2 = float(np.log(2.0))


def pairs(p: pd.DataFrame, detected: np.ndarray, max_dt_s: float) -> pd.DataFrame:
    """(dt_s, y, fl, day) for every later pass after each detected pass, same cell
    and FL bin, different aircraft. `p` needs icao24, cell, fl, t0, t1, day."""
    q = p.assign(det=np.asarray(detected, dtype=bool)).sort_values(
        ["cell", "fl", "t0"], ignore_index=True
    )
    key = q["cell"].to_numpy(np.int64) * 64 + q["fl"].to_numpy(np.int64)
    t0, t1 = q["t0"].to_numpy(float), q["t1"].to_numpy(float)
    ac, det, fl, day = (
        q["icao24"].to_numpy(),
        q["det"].to_numpy(),
        q["fl"].to_numpy(),
        q["day"].to_numpy(),
    )
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:], len(q)]
    grp_end = np.repeat(ends, ends - starts)
    out = []
    for i in np.flatnonzero(det):
        j0 = i + 1
        j1 = np.searchsorted(t0[j0 : grp_end[i]], t1[i] + max_dt_s, side="right") + j0
        js = np.arange(j0, j1)
        js = js[(ac[js] != ac[i]) & (t0[js] >= t1[i])]
        if len(js):
            out.append(
                pd.DataFrame({"dt_s": t0[js] - t1[i], "y": det[js], "fl": fl[i], "day": day[i]})
            )
    if not out:
        return pd.DataFrame(columns=["dt_s", "y", "fl", "day"])
    return pd.concat(out, ignore_index=True)


def model(dt: np.ndarray, a: float, t_half: float, base: float) -> np.ndarray:
    return base + a * np.exp(-LN2 * dt / t_half)


@dataclass(frozen=True)
class HalfLifeFit:
    t_half_s: float
    a: float
    base: float
    n_pairs: int
    ci_low_s: float
    ci_high_s: float


def _fit(
    dt: np.ndarray, y: np.ndarray, base: float, bin_s: float, max_dt_s: float
) -> tuple[float, float]:
    edges = np.arange(0.0, max_dt_s + bin_s, bin_s)
    b = np.clip(np.digitize(dt, edges) - 1, 0, len(edges) - 2)
    n = np.bincount(b, minlength=len(edges) - 1).astype(float)
    k = np.bincount(b, weights=y.astype(float), minlength=len(edges) - 1)
    ok = n > 0
    sx = np.bincount(b, weights=dt, minlength=len(edges) - 1)
    x = sx[ok] / n[ok]  # mean dt per bin: bin centres bias a convex curve
    rate = k[ok] / n[ok]
    sigma = np.sqrt(np.maximum(np.maximum(rate * (1 - rate), base * (1 - base)), 1e-6) / n[ok])
    (a, th), _ = curve_fit(
        lambda d, a, th: model(d, a, th, base),
        x,
        rate,
        p0=[max(rate[0] - base, 1e-3), 720.0],
        sigma=sigma,
        bounds=([0.0, 30.0], [1.0, 6 * 3600.0]),
        maxfev=20000,
    )
    return float(a), float(th)


def fit(
    pr: pd.DataFrame,
    base: float,
    bin_s: float,
    max_dt_s: float,
    n_boot: int,
    rng: np.random.Generator,
) -> HalfLifeFit:
    dt, y = pr["dt_s"].to_numpy(float), pr["y"].to_numpy(bool)
    a, th = _fit(dt, y, base, bin_s, max_dt_s)
    days = pr["day"].to_numpy()
    uniq = np.unique(days)
    by_day = {d: np.flatnonzero(days == d) for d in uniq}
    boots = []
    for _ in range(n_boot):
        idx = np.concatenate([by_day[d] for d in rng.choice(uniq, len(uniq), replace=True)])
        try:
            boots.append(_fit(dt[idx], y[idx], base, bin_s, max_dt_s)[1])
        except RuntimeError:
            continue
    lo, hi = np.quantile(boots, [0.025, 0.975]) if boots else (np.nan, np.nan)
    return HalfLifeFit(th, a, base, len(pr), float(lo), float(hi))
