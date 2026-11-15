"""Level-flight segmentation (SPEC §5.1). Pure, vectorised, no I/O.

Input is one aircraft on a uniform time grid (NaN where not observed). A grid point
is "level" when the centred `window_s` window around it has enough observations and:
altitude >= min_alt_ft, altitude range <= max_alt_range_ft, |median vr| <=
max_abs_median_vr_fpm, and no on-ground sample. Contiguous level runs are trimmed by
`trim_s` at both ends (step-climb transitions) and dropped if shorter than
`min_segment_s`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from horus.config import Config
from horus.core import rolling


@dataclass(frozen=True)
class SegmentParams:
    min_alt_ft: float = 20000.0
    window_s: float = 120.0
    max_alt_range_ft: float = 300.0
    max_abs_median_vr_fpm: float = 200.0
    trim_s: float = 30.0
    min_segment_s: float = 46.0  # W_trend + W_stat: room for one full statistic
    min_window_samples: int = 4

    @classmethod
    def from_config(cls, cfg: Config) -> SegmentParams:
        s, r = cfg.segment, cfg.residual
        return cls(
            min_alt_ft=s.min_alt_ft,
            window_s=s.window_s,
            max_alt_range_ft=s.max_alt_range_ft,
            max_abs_median_vr_fpm=s.max_abs_median_vr_fpm,
            trim_s=s.trim_s,
            min_segment_s=float(r.w_trend_s + r.w_stat_s),
            min_window_samples=s.min_window_samples,
        )


@dataclass(frozen=True)
class Segments:
    seg_id: NDArray[np.int64]  # per grid point; -1 = not in a level segment
    bounds: NDArray[np.int64]  # (n_segments, 2) inclusive start, exclusive end indices
    level_share: float  # observed airborne samples that fall in segments


def _runs(mask: NDArray[np.bool_]) -> NDArray[np.int64]:
    """(start, end_exclusive) of each True run."""
    m = np.concatenate([[False], mask, [False]]).astype(np.int8)
    d = np.diff(m)
    return np.stack([np.flatnonzero(d == 1), np.flatnonzero(d == -1)], axis=1).astype(np.int64)


def level_flight(
    alt_ft: NDArray[np.floating],
    vr_fpm: NDArray[np.floating],
    on_ground: NDArray[np.bool_],
    step_s: float,
    p: SegmentParams,
) -> Segments:
    alt = np.asarray(alt_ft, dtype=float)
    vr = np.asarray(vr_fpm, dtype=float)
    ground = np.asarray(on_ground, dtype=bool)
    n = len(alt)
    if n == 0:
        return Segments(np.empty(0, np.int64), np.empty((0, 2), np.int64), float("nan"))

    w = rolling.window_samples(p.window_s, step_s)
    k = p.min_window_samples
    n_obs = rolling.count(alt, w)
    a_min = rolling.minimum(alt, w, 1)
    a_max = rolling.maximum(alt, w, 1)
    v_med = rolling.median(vr, w, 1)
    any_ground = rolling.maximum(ground.astype(float), w, 1)
    with np.errstate(invalid="ignore"):
        ok = (
            (n_obs >= k)
            & (a_min >= p.min_alt_ft)
            & ((a_max - a_min) <= p.max_alt_range_ft)
            & (np.abs(v_med) <= p.max_abs_median_vr_fpm)
            & (any_ground == 0)
        )
    ok = np.nan_to_num(ok, nan=0).astype(bool)

    trim = int(round(p.trim_s / step_s))
    min_len = int(np.ceil(p.min_segment_s / step_s))
    runs = _runs(ok)
    runs[:, 0] += trim
    runs[:, 1] -= trim
    runs = runs[(runs[:, 1] - runs[:, 0]) >= max(min_len, 1)]

    seg_id = np.full(n, -1, dtype=np.int64)
    for i, (s, e) in enumerate(runs):
        seg_id[s:e] = i

    airborne_obs = np.isfinite(alt) & ~ground
    share = (
        float((airborne_obs & (seg_id >= 0)).sum() / airborne_obs.sum())
        if airborne_obs.any()
        else float("nan")
    )
    return Segments(seg_id, runs, share)


def mask_outside(x: NDArray[np.floating], seg_id: NDArray[np.int64]) -> NDArray[np.float64]:
    """NaN everywhere outside level segments (so windows never straddle a climb)."""
    return np.where(seg_id >= 0, np.asarray(x, dtype=float), np.nan)
