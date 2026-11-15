"""Turbulence statistics from vertical rate (SPEC §5.2). Pure, vectorised, no I/O.

Residual path (B1/B2):
    trend(t) = rolling median of vr over W_trend (the intended flight path)
    r(t)     = vr(t) - trend(t)
    var_r    = (flicker-weighted) variance of r over W_stat
    Sheppard: var_corr = max(var_r - D^2/12, 0), D = 64 ft/min quantisation step.

Sheppard's correction assumes the quantisation error is independent of the signal,
which holds once sigma >~ 0.6 D (~40 ft/min). Below that it over-corrects (measured:
sigma=20 -> -52%), so small values are not meaningful, only "near zero".

Flicker: a single-sample +-D toggle that returns to the previous value
(0, 64, 0) is usually quantisation, not air motion; it is down-weighted.

Textbook baseline (B0/B1): vertical acceleration a = d(vr)/dt in g, RMS over W_stat.
For B1 the quantisation noise of a difference of two quantised samples,
Var = 2 D^2 / 12 / dt^2, is subtracted from the mean square.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from horus.config import Config
from horus.core import rolling

G_FT_S2 = 32.174
FPM_S_PER_G = 60.0 * G_FT_S2  # (ft/min)/s in one g
STEP_TOL_FPM = 2.0  # OpenSky m/s rounding leaves <= ~1 ft/min off-grid


@dataclass(frozen=True)
class ResidualParams:
    w_trend_s: float = 31.0
    w_stat_s: float = 15.0
    min_samples_stat: int = 6
    step_fpm: float = 64.0
    sheppard: bool = True
    flicker_weight: float = 0.5

    @classmethod
    def from_config(cls, cfg: Config) -> ResidualParams:
        r = cfg.residual
        return cls(
            w_trend_s=r.w_trend_s,
            w_stat_s=r.w_stat_s,
            min_samples_stat=r.min_samples_stat,
            step_fpm=cfg.units.vr_step_fpm,
            sheppard=r.sheppard,
            flicker_weight=r.flicker_weight,
        )


def _observed(x: NDArray[np.floating]) -> NDArray[np.int64]:
    return np.flatnonzero(np.isfinite(x))


def flicker_mask(
    vr_fpm: NDArray[np.floating], step_fpm: float = 64.0, max_gap: int | None = None
) -> NDArray[np.bool_]:
    """True at single-sample +-step toggles between consecutive observations.
    With `max_gap`, observations more than `max_gap` samples apart are not
    neighbours (e.g. the last sample of one held piece and the first of the next)."""
    vr = np.asarray(vr_fpm, dtype=float)
    idx = _observed(vr)
    out = np.zeros(len(vr), dtype=bool)
    if len(idx) < 3:
        return out
    o = vr[idx]
    jump = np.abs(np.abs(o[1:-1] - o[:-2]) - step_fpm) <= STEP_TOL_FPM
    back = np.abs(o[2:] - o[:-2]) <= STEP_TOL_FPM
    hit = jump & back
    if max_gap is not None:
        hit &= (idx[1:-1] - idx[:-2] <= max_gap) & (idx[2:] - idx[1:-1] <= max_gap)
    out[idx[1:-1][hit]] = True
    return out


def sheppard(var: NDArray[np.floating], step_fpm: float) -> NDArray[np.float64]:
    return np.maximum(np.asarray(var, dtype=float) - step_fpm**2 / 12.0, 0.0)


@dataclass(frozen=True)
class ResidualStats:
    trend: NDArray[np.float64]
    resid: NDArray[np.float64]
    var_raw: NDArray[np.float64]  # weighted variance of residual, before Sheppard
    var: NDArray[np.float64]  # after Sheppard (if enabled)
    flicker: NDArray[np.bool_]

    @property
    def sigma(self) -> NDArray[np.float64]:
        return np.sqrt(self.var)

    @property
    def n_flicker(self) -> int:
        return int(self.flicker.sum())


def residual_stats(vr_fpm: NDArray[np.floating], step_s: float, p: ResidualParams) -> ResidualStats:
    """Residual turbulence statistic on a uniform grid (NaN = not observed).

    Callers mask vr outside level segments first (`segment.mask_outside`)."""
    vr = np.asarray(vr_fpm, dtype=float)
    w_tr = rolling.window_samples(p.w_trend_s, step_s)
    w_st = rolling.window_samples(p.w_stat_s, step_s)
    trend = rolling.median(vr, w_tr, p.min_samples_stat)
    resid = vr - trend
    flick = flicker_mask(vr, p.step_fpm, max_gap=w_tr)
    weights = np.where(flick, p.flicker_weight, 1.0)
    var_raw = rolling.weighted_var(resid, weights, w_st, p.min_samples_stat)
    var = sheppard(var_raw, p.step_fpm) if p.sheppard else var_raw.copy()
    var = np.where(np.isfinite(var_raw), var, np.nan)
    return ResidualStats(trend, resid, var_raw, var, flick)


def accel_g(
    time_s: NDArray[np.floating],
    vr_fpm: NDArray[np.floating],
    max_dt_s: float | None = None,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Vertical acceleration (g) between consecutive observations, placed at the
    later sample, plus the time step used (s). NaN where there is no previous obs,
    or where the previous obs is more than `max_dt_s` earlier (e.g. across the gap
    between two held pieces)."""
    t = np.asarray(time_s, dtype=float)
    vr = np.asarray(vr_fpm, dtype=float)
    idx = _observed(vr)
    a = np.full(len(vr), np.nan)
    dt = np.full(len(vr), np.nan)
    if len(idx) >= 2:
        d_t = t[idx[1:]] - t[idx[:-1]]
        acc = (vr[idx[1:]] - vr[idx[:-1]]) / d_t / FPM_S_PER_G
        if max_dt_s is not None:
            far = ~(d_t <= max_dt_s)
            acc[far] = np.nan
            d_t = np.where(far, np.nan, d_t)
        a[idx[1:]] = acc
        dt[idx[1:]] = d_t
    return a, dt


def accel_rms(
    time_s: NDArray[np.floating],
    vr_fpm: NDArray[np.floating],
    step_s: float,
    w_stat_s: float,
    min_samples: int,
    quant_step_fpm: float | None = None,
    max_dt_s: float | None = None,
) -> NDArray[np.float64]:
    """RMS vertical acceleration (g) over W_stat. With `quant_step_fpm` (B1),
    subtract the quantisation noise mean square 2 D^2/12/dt^2 (floor at 0)."""
    a, dt = accel_g(time_s, vr_fpm, max_dt_s)
    w = rolling.window_samples(w_stat_s, step_s)
    ms = rolling.mean_square(a, w, min_samples)
    if quant_step_fpm is not None:
        q = 2.0 * quant_step_fpm**2 / 12.0 / dt**2 / FPM_S_PER_G**2
        qm = np.nan_to_num(rolling.mean(q, w, 1))
        excess = ms - qm
        # Rolling means carry ~1e-12 relative round-off; a window whose mean square
        # equals the noise floor is exactly 0, not 1e-10 g of residue.
        excess[excess <= 1e-9 * np.fmax(qm, ms)] = 0.0
        ms = excess
    return np.sqrt(ms)
