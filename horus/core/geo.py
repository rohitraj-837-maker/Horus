"""Geometry and standard-atmosphere helpers. Pure functions, no I/O."""

from __future__ import annotations

import h3
import numpy as np
from numpy.typing import ArrayLike, NDArray

EARTH_RADIUS_KM = 6371.0088

# ICAO standard atmosphere (ISA), altitudes in feet, pressure in hPa.
P0_HPA = 1013.25
TROPOPAUSE_FT = 36089.24
P_TROPOPAUSE_HPA = 226.32
_TROPO_K = 6.8755856e-6  # 1/ft
_TROPO_EXP = 5.2558797
_STRATO_SCALE_FT = 20805.8


def haversine_km(
    lat1: ArrayLike, lon1: ArrayLike, lat2: ArrayLike, lon2: ArrayLike
) -> NDArray[np.float64]:
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlmb = np.radians(np.asarray(lon2, dtype=float) - np.asarray(lon1, dtype=float))
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return np.asarray(2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0))))


def pressure_hpa(alt_ft: ArrayLike) -> NDArray[np.float64]:
    """ISA pressure at pressure altitude `alt_ft` (valid to ~65,000 ft)."""
    h = np.asarray(alt_ft, dtype=float)
    tropo = P0_HPA * (1.0 - _TROPO_K * np.minimum(h, TROPOPAUSE_FT)) ** _TROPO_EXP
    strato = P_TROPOPAUSE_HPA * np.exp(-(h - TROPOPAUSE_FT) / _STRATO_SCALE_FT)
    return np.where(h <= TROPOPAUSE_FT, tropo, strato)


def altitude_ft(p_hpa: ArrayLike) -> NDArray[np.float64]:
    """Inverse of `pressure_hpa`."""
    p = np.asarray(p_hpa, dtype=float)
    tropo = (1.0 - (p / P0_HPA) ** (1.0 / _TROPO_EXP)) / _TROPO_K
    strato = TROPOPAUSE_FT - _STRATO_SCALE_FT * np.log(p / P_TROPOPAUSE_HPA)
    return np.where(p >= P_TROPOPAUSE_HPA, tropo, strato)


def fl_bin(alt_ft: ArrayLike, fl_min: int, fl_max: int, fl_step: int) -> NDArray[np.int64]:
    """Flight-level bin index; -1 outside [fl_min, fl_max)."""
    fl = np.asarray(alt_ft, dtype=float) / 100.0
    idx = np.floor((fl - fl_min) / fl_step)
    n = (fl_max - fl_min) // fl_step
    ok = np.isfinite(idx) & (idx >= 0) & (idx < n)
    return np.where(ok, np.nan_to_num(idx), -1).astype(np.int64)


def h3_cells(lat: ArrayLike, lon: ArrayLike, res: int) -> NDArray[np.object_]:
    """H3 cell id per point (None where lat/lon is NaN)."""
    la, lo = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    return np.array(
        [
            h3.latlng_to_cell(a, b, res) if np.isfinite(a) and np.isfinite(b) else None
            for a, b in zip(la, lo, strict=True)
        ],
        dtype=object,
    )
