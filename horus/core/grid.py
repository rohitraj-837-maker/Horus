"""Nowcast grid (SPEC §5.5): log-odds occupancy over H3 cells x flight-level bins.
Pure, vectorised, no I/O; time is always passed in.

Each cell holds log-odds L that turbulence is present and the time it was last
updated. Rules:
  decay       L <- L0 + (L - L0) * exp(-ln2 * dt / t_half)   (lazily, on touch/read)
  positive    L += w * l_pos(T),  l_pos(T) = clip(a * (T - tau), 0, l_pos_max)
  negative    L -= w * l_neg      (a confident smooth pass, M5)
  clamp       L in [l_min, l_max];  p = sigmoid(L)
N0 (persistence baseline): a cell takes the verdict of its last pass and keeps
it forever: L = l_hit after a detection, L0 after a non-detection, no decay.
"""

from __future__ import annotations

from dataclasses import dataclass

import h3
import numpy as np
from numpy.typing import NDArray

LN2 = float(np.log(2.0))
F32 = NDArray[np.float32]


def logit(p: float | NDArray[np.floating]) -> NDArray[np.float64]:
    p = np.asarray(p, dtype=float)
    return np.asarray(np.log(p / (1.0 - p)))


def sigmoid(x: NDArray[np.floating]) -> NDArray[np.float64]:
    return np.asarray(1.0 / (1.0 + np.exp(-np.asarray(x, dtype=float))))


def region_cells(
    lat_min: float, lat_max: float, lon_min: float, lon_max: float, res: int
) -> NDArray[np.int64]:
    """Sorted int64 ids of every H3 cell whose centre lies in the bounding box."""
    poly = h3.LatLngPoly(
        [(lat_min, lon_min), (lat_min, lon_max), (lat_max, lon_max), (lat_max, lon_min)]
    )
    ids = [h3.str_to_int(c) for c in h3.polygon_to_cells(poly, res)]
    return np.sort(np.asarray(ids, dtype=np.int64))


def cell_index(cell_ids: NDArray[np.int64], lattice: NDArray[np.int64]) -> NDArray[np.int64]:
    """Row in `lattice` for each cell id; -1 if the cell is not in the lattice."""
    c = np.asarray(cell_ids, dtype=np.int64)
    i = np.searchsorted(lattice, c)
    i = np.clip(i, 0, len(lattice) - 1)
    return np.where(lattice[i] == c, i, -1).astype(np.int64)


@dataclass(frozen=True)
class GridParams:
    l0: float  # prior log-odds (base rate)
    half_life_s: NDArray[np.float64]  # per FL bin
    l_min: float = -6.0
    l_max: float = 6.0
    a: float = 1.0  # positive evidence slope per unit of T above tau
    l_pos_max: float = 4.0
    tau: float = 1.0
    l_neg: float = 0.0  # 0 = no negative evidence (N1); M5 sets it (N3)


@dataclass
class GridState:
    L: F32  # (n_cells, n_fl) log-odds as of t
    t: NDArray[np.float64]  # (n_cells, n_fl) last update, epoch s

    @classmethod
    def empty(cls, n_cells: int, n_fl: int, l0: float, t0: float) -> GridState:
        return cls(
            np.full((n_cells, n_fl), l0, dtype=np.float32),
            np.full((n_cells, n_fl), float(t0)),
        )


def decayed(
    L: NDArray[np.floating],
    t_last: NDArray[np.floating],
    now: float,
    l0: float,
    half_life_s: NDArray[np.floating],
) -> NDArray[np.float64]:
    """L brought forward to `now` (no mutation). `half_life_s` broadcasts over FL bins."""
    dt = np.maximum(now - np.asarray(t_last, dtype=float), 0.0)
    k = np.exp(-LN2 * dt / np.asarray(half_life_s, dtype=float))
    return np.asarray(l0 + (np.asarray(L, dtype=float) - l0) * k)


def read(state: GridState, now: float, p: GridParams) -> NDArray[np.float64]:
    """Probability for every cell at `now` (lazy decay; the state is not changed)."""
    return sigmoid(decayed(state.L, state.t, now, p.l0, p.half_life_s[None, :]))


def l_pos(T: NDArray[np.floating], p: GridParams) -> NDArray[np.float64]:
    t = np.nan_to_num(np.asarray(T, dtype=float), nan=-np.inf)
    return np.asarray(np.clip(p.a * (t - p.tau), 0.0, p.l_pos_max))


def update(
    state: GridState,
    cell: NDArray[np.int64],
    fl: NDArray[np.int64],
    T: NDArray[np.floating],
    w: NDArray[np.floating],
    now: float,
    p: GridParams,
    smooth: NDArray[np.bool_] | None = None,
) -> None:
    """Apply one batch of observations at time `now` (in place). Observations
    outside the lattice or FL range (index -1) are ignored. Several observations
    in one cell add up."""
    ok = (cell >= 0) & (fl >= 0)
    c, f = cell[ok], fl[ok]
    if len(c) == 0:
        return
    hl = p.half_life_s[f]
    state.L[c, f] = decayed(state.L[c, f], state.t[c, f], now, p.l0, hl).astype(np.float32)
    state.t[c, f] = now
    delta = np.asarray(w, dtype=float)[ok] * l_pos(np.asarray(T)[ok], p)
    if smooth is not None and p.l_neg > 0:
        delta -= np.asarray(w, dtype=float)[ok] * p.l_neg * np.asarray(smooth)[ok]
    acc = np.zeros(state.L.shape, dtype=np.float64)
    np.add.at(acc, (c, f), delta)
    touched = acc[c, f]
    state.L[c, f] = np.clip(state.L[c, f] + touched.astype(np.float32), p.l_min, p.l_max)
    # np.add.at already summed duplicates; writing the same value twice is harmless.


def update_persistence(
    state: GridState,
    cell: NDArray[np.int64],
    fl: NDArray[np.int64],
    detected: NDArray[np.bool_],
    now: float,
    l0: float,
    l_hit: float,
) -> None:
    """N0: each cell takes its latest pass's verdict and keeps it (no decay).
    Within one batch, a detection wins over a non-detection in the same cell."""
    ok = (cell >= 0) & (fl >= 0)
    c, f, d = cell[ok], fl[ok], np.asarray(detected, dtype=bool)[ok]
    state.L[c[~d], f[~d]] = l0
    state.L[c[d], f[d]] = l_hit
    state.t[c, f] = now


M_PER_DEG = 111_195.0


@dataclass(frozen=True)
class Lattice:
    """Cell geometry for advection: centres, ring-1 neighbours sorted by bearing
    (row indices, -1 if outside the lattice), their local offsets in metres, and for
    each pair of consecutive neighbours the inverse of [d_i d_i+1] (barycentric)."""

    lat: NDArray[np.float64]
    lon: NDArray[np.float64]
    nbr: NDArray[np.int64]  # (n, 6) sorted by bearing
    ang: NDArray[np.float64]  # (n, 6) bearing of each neighbour (rad, atan2(dy, dx))
    inv: NDArray[np.float64]  # (n, 6, 2, 2) inverse of [d_i, d_{i+1}]

    @classmethod
    def build(cls, ids: NDArray[np.int64]) -> Lattice:
        cells = [h3.int_to_str(int(i)) for i in ids]
        ll = np.array([h3.cell_to_latlng(c) for c in cells])
        lat, lon = ll[:, 0], ll[:, 1]
        n = len(ids)
        nbr = np.full((n, 6), -1, dtype=np.int64)
        off = np.zeros((n, 6, 2))
        for k, c in enumerate(cells):
            ring = [h3.cell_to_latlng(x) for x in h3.grid_ring(c, 1)][:6]
            rid = cell_index(
                np.asarray([h3.str_to_int(x) for x in h3.grid_ring(c, 1)][:6], np.int64), ids
            )
            d = np.array(
                [
                    [
                        (b - lon[k]) * M_PER_DEG * np.cos(np.radians(lat[k])),
                        (a - lat[k]) * M_PER_DEG,
                    ]
                    for a, b in ring
                ]
            )
            order = np.argsort(np.arctan2(d[:, 1], d[:, 0]))
            nbr[k, : len(order)] = rid[order]
            off[k, : len(order)] = d[order]
        ang = np.arctan2(off[:, :, 1], off[:, :, 0])
        m = np.stack([off, np.roll(off, -1, axis=1)], axis=3)  # (n, 6, 2, 2): columns d_i, d_i+1
        det = m[..., 0, 0] * m[..., 1, 1] - m[..., 0, 1] * m[..., 1, 0]
        det = np.where(np.abs(det) > 1e-9, det, np.nan)
        inv = np.empty_like(m)
        inv[..., 0, 0] = m[..., 1, 1] / det
        inv[..., 0, 1] = -m[..., 0, 1] / det
        inv[..., 1, 0] = -m[..., 1, 0] / det
        inv[..., 1, 1] = m[..., 0, 0] / det
        return cls(lat, lon, nbr, ang, inv)


def advect(
    state: GridState,
    lat_c: Lattice,
    u: NDArray[np.floating],
    v: NDArray[np.floating],
    dt_s: float,
    now: float,
    p: GridParams,
    locked: NDArray[np.bool_] | None = None,
) -> None:
    """Semi-Lagrangian step (in place): L(x) <- L(x - (u, v) dt), linear
    (barycentric) interpolation in the triangle formed by the cell and the two
    neighbours bracketing the departure point. A convex combination, so the max
    never increases and zero wind is the identity. Missing neighbours (lattice
    edge) take the cell's own value. Displacement per step must stay below one
    cell (60 s at <= 150 m/s is 9 km vs ~26 km edges). `u`, `v` are
    (n_cells, n_fl) m/s at the cell centres; `locked` cells keep their value."""
    cur = decayed(state.L, state.t, now, p.l0, p.half_life_s[None, :])
    n, n_fl = cur.shape
    ex = -np.asarray(u, dtype=float) * dt_s  # departure offset east (m)
    ny = -np.asarray(v, dtype=float) * dt_s
    theta = np.arctan2(ny, ex)  # (n, n_fl)
    # bracketing pair: last neighbour with bearing <= theta (cyclic)
    rel = (theta[:, None, :] - lat_c.ang[:, :, None]) % (2 * np.pi)  # (n, 6, n_fl)
    i = np.argmin(rel, axis=1)  # neighbour just clockwise of the departure direction
    i2 = (i + 1) % 6
    rows = np.arange(n)[:, None]
    inv = lat_c.inv[rows, i]  # (n, n_fl, 2, 2)
    lam1 = inv[..., 0, 0] * ex + inv[..., 0, 1] * ny
    lam2 = inv[..., 1, 0] * ex + inv[..., 1, 1] * ny
    lam1 = np.nan_to_num(np.clip(lam1, 0.0, 1.0))
    lam2 = np.nan_to_num(np.clip(lam2, 0.0, 1.0))
    s = lam1 + lam2
    over = s > 1.0
    s_safe = np.where(over, s, 1.0)
    lam1 = lam1 / s_safe
    lam2 = lam2 / s_safe
    lam0 = 1.0 - lam1 - lam2
    j1, j2 = lat_c.nbr[rows, i], lat_c.nbr[rows, i2]
    fl = np.arange(n_fl)[None, :]
    v1 = np.where(j1 >= 0, cur[np.maximum(j1, 0), fl], cur)
    v2 = np.where(j2 >= 0, cur[np.maximum(j2, 0), fl], cur)
    new = lam0 * cur + lam1 * v1 + lam2 * v2
    if locked is not None:
        new[locked] = cur[locked]
    state.L[:] = np.clip(new, p.l_min, p.l_max).astype(np.float32)
    state.t[:] = now
