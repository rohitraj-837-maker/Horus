"""PIREP windows and window scoring (SPEC §6.1).

A *window* is a report point (time, lat, lon, altitude range). An aircraft-minute
row (eval/scores.py) is inside a window when
    its minute [m, m+60) overlaps [t - window_s, t + window_s],
    its position is within dist_km (great circle), and
    its altitude lies in [alt_lo - alt_ft, alt_hi + alt_ft].
A window's score for a detector is the max of that detector's per-minute score over
the rows inside it (event-level scoring: any detection in the window counts). Rows
whose detector has no value (e.g. B2 outside level flight) count as 0, so a detector
that cannot see an event is charged with the miss. `n_passes` is the number of
distinct aircraft inside the window.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from horus.config import Config
from horus.core.geo import EARTH_RADIUS_KM, haversine_km

MINUTE_S = 60


@dataclass(frozen=True)
class WindowSpec:
    window_s: int
    dist_km: float
    alt_ft: float

    @classmethod
    def from_config(cls, cfg: Config) -> WindowSpec:
        m = cfg.match
        return cls(m.window_s, m.dist_km, m.alt_ft)


def pirep_windows(pireps: pd.DataFrame, cfg: Config, min_level: int) -> pd.DataFrame:
    """Report windows for PIREPs with tb >= `min_level` inside the region whose
    altitude range reaches the detector scope (bench.min_fl)."""
    r = cfg.region
    p = pireps
    m = (
        (p["tb_max"] >= min_level)
        & p["lat"].between(r.lat_min, r.lat_max)
        & p["lon"].between(r.lon_min, r.lon_max)
        & (p["fl_hi"] >= cfg.bench.min_fl)
    )
    w = p.loc[m, ["time", "lat", "lon", "fl_lo", "fl_hi", "tb_max", "tb_min"]].copy()
    w["alt_lo_ft"] = w.pop("fl_lo").astype(float) * 100.0
    w["alt_hi_ft"] = w.pop("fl_hi").astype(float) * 100.0
    return w.reset_index(drop=True)


def unit_xyz(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    la, lo = np.radians(lat), np.radians(lon)
    return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])


def chord(dist_km: float) -> float:
    return float(2.0 * np.sin(dist_km / (2.0 * EARTH_RADIUS_KM)))


def near_any_event(
    minute: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    ev_time: np.ndarray,
    ev_lat: np.ndarray,
    ev_lon: np.ndarray,
    time_s: float,
    dist_km: float,
) -> np.ndarray:
    """True for each minute row with an event within `time_s` (minute overlap rule)
    and `dist_km`. Exact: one KD-tree per distinct minute over the events in range."""
    minute = np.asarray(minute, dtype=np.int64)
    out = np.zeros(len(minute), dtype=bool)
    if len(minute) == 0 or len(ev_time) == 0:
        return out
    order = np.argsort(ev_time, kind="stable")
    et = np.asarray(ev_time, dtype=float)[order]
    exyz = unit_xyz(np.asarray(ev_lat)[order], np.asarray(ev_lon)[order])
    oxyz = unit_xyz(np.asarray(lat), np.asarray(lon))
    r = chord(dist_km)
    rows_by_minute = pd.Series(np.arange(len(minute))).groupby(minute).indices
    for m, rows in rows_by_minute.items():
        # overlap of [m, m+60) with [t - T, t + T]  <=>  t > m - 60 - T and t <= m + T
        a = np.searchsorted(et, m - MINUTE_S - time_s, side="right")
        b = np.searchsorted(et, m + time_s, side="right")
        if a == b:
            continue
        d, _ = cKDTree(exyz[a:b]).query(oxyz[rows], k=1, distance_upper_bound=r * (1 + 1e-12))
        out[rows] = np.isfinite(d)
    return out


def window_scores(
    windows: pd.DataFrame,
    obs: pd.DataFrame,
    spec: WindowSpec,
    score_cols: list[str] | tuple[str, ...],
) -> pd.DataFrame:
    """Max score per window per detector, plus `n_passes` (distinct aircraft).

    `obs` needs: minute, lat, lon, alt_ft, icao24, and the score columns."""
    o = obs.sort_values("minute", kind="stable")
    om = o["minute"].to_numpy(dtype=np.int64)
    olat, olon = o["lat"].to_numpy(float), o["lon"].to_numpy(float)
    oalt = o["alt_ft"].to_numpy(float)
    oac = o["icao24"].astype(str).to_numpy()
    sc = {c: np.nan_to_num(o[c].to_numpy(float), nan=0.0) for c in score_cols}

    res: dict[str, list[float]] = {c: [] for c in score_cols}
    passes: list[int] = []
    for t, la, lo, ah, al in zip(
        windows["time"].to_numpy(float),
        windows["lat"].to_numpy(float),
        windows["lon"].to_numpy(float),
        windows["alt_hi_ft"].to_numpy(float),
        windows["alt_lo_ft"].to_numpy(float),
        strict=True,
    ):
        a = np.searchsorted(om, t - MINUTE_S - spec.window_s, side="right")
        b = np.searchsorted(om, t + spec.window_s, side="right")
        sl = slice(a, b)
        ok = (oalt[sl] >= al - spec.alt_ft) & (oalt[sl] <= ah + spec.alt_ft)
        if ok.any():
            ok &= haversine_km(la, lo, olat[sl], olon[sl]) <= spec.dist_km
        n = len(np.unique(oac[sl][ok])) if ok.any() else 0
        passes.append(n)
        for c in score_cols:
            res[c].append(float(sc[c][sl][ok].max()) if n else 0.0)
    out = pd.DataFrame({"n_passes": passes, **res}, index=windows.index)
    return out
