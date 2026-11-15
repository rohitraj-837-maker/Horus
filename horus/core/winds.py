"""Upper-air wind lookup on a regular lat/lon lattice. Pure, vectorised, no I/O.

Field arrays are (time, level, lat, lon) with u, v in m/s (u > 0 = toward east,
v > 0 = toward north). Lookup is linear in time, bilinear in space and linear in
log-pressure between levels; points outside the lattice or level range are
clamped to the nearest edge (FL200 sits near 465 hPa, just below 400 hPa).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from horus.core.geo import pressure_hpa

F = NDArray[np.float64]


def uv_from_speed_dir(speed: NDArray[np.floating], from_deg: NDArray[np.floating]) -> tuple[F, F]:
    """Meteorological convention: direction is where the wind blows FROM."""
    s = np.asarray(speed, dtype=float)
    d = np.radians(np.asarray(from_deg, dtype=float))
    return -s * np.sin(d), -s * np.cos(d)


@dataclass(frozen=True)
class WindField:
    time: F  # (nt,) epoch s, increasing
    levels_hpa: F  # (nl,) decreasing pressure (increasing altitude)
    lat: F  # (ny,) increasing
    lon: F  # (nx,) increasing
    u: NDArray[np.float32]  # (nt, nl, ny, nx)
    v: NDArray[np.float32]


def _frac(grid: F, x: NDArray[np.floating]) -> tuple[NDArray[np.int64], F]:
    """Lower index and fraction for linear interpolation, clamped to the grid."""
    x = np.clip(x, grid[0], grid[-1])
    i = np.clip(np.searchsorted(grid, x, side="right") - 1, 0, max(len(grid) - 2, 0))
    if len(grid) == 1:
        return np.zeros(len(x), np.int64), np.zeros(len(x))
    f = (x - grid[i]) / (grid[i + 1] - grid[i])
    return i.astype(np.int64), np.clip(f, 0.0, 1.0)


def wind_at(
    w: WindField,
    t: NDArray[np.floating],
    lat: NDArray[np.floating],
    lon: NDArray[np.floating],
    alt_ft: NDArray[np.floating],
) -> tuple[F, F]:
    """(u, v) in m/s at each point."""
    t = np.asarray(t, dtype=float)
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    lp = np.log(pressure_hpa(np.asarray(alt_ft, dtype=float)))
    it, ft = _frac(w.time, t)
    # log p decreases with altitude; interpolate on -log p so the axis increases
    il, fl = _frac(-np.log(w.levels_hpa), -lp)
    iy, fy = _frac(w.lat, lat)
    ix, fx = _frac(w.lon, lon)
    out = []
    for comp in (w.u, w.v):
        acc = np.zeros(len(t))
        for dt_, wt in ((0, 1 - ft), (1, ft)):
            ti = np.minimum(it + dt_, len(w.time) - 1)
            for dl, wl in ((0, 1 - fl), (1, fl)):
                li = np.minimum(il + dl, len(w.levels_hpa) - 1)
                for dy, wy in ((0, 1 - fy), (1, fy)):
                    yi = np.minimum(iy + dy, len(w.lat) - 1)
                    for dx, wx in ((0, 1 - fx), (1, fx)):
                        xi = np.minimum(ix + dx, len(w.lon) - 1)
                        acc += wt * wl * wy * wx * comp[ti, li, yi, xi]
        out.append(acc)
    return out[0], out[1]
