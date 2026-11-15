"""Grid passes: aircraft-minutes collapsed per (aircraft, H3 cell, FL bin) visit.

A pass is a run of consecutive minutes of one aircraft in the same cell and FL bin.
It carries the max turbulence index T of the run, its start/end minute and the
aircraft position at the start (for advection). Passes are the observations the
nowcast grid (core/grid.py) consumes and the targets the nowcast bench scores.
"""

from __future__ import annotations

import h3
import numpy as np
import pandas as pd

from horus.config import Config
from horus.core import geo, grid


def cell_ids(lat: np.ndarray, lon: np.ndarray, res: int) -> np.ndarray:
    return np.fromiter(
        (h3.str_to_int(h3.latlng_to_cell(a, b, res)) for a, b in zip(lat, lon, strict=True)),
        dtype=np.int64,
        count=len(lat),
    )


def build(obs: pd.DataFrame, t_col: str, cfg: Config, lattice: np.ndarray) -> pd.DataFrame:
    """Passes from per-minute rows sorted by (icao24, minute). `t_col` is the index
    column (e.g. B3). Rows outside the lattice or FL range are dropped."""
    g = cfg.grid
    c = grid.cell_index(cell_ids(obs["lat"].to_numpy(), obs["lon"].to_numpy(), g.h3_res), lattice)
    f = geo.fl_bin(obs["alt_ft"].to_numpy(), g.fl_min, g.fl_max, g.fl_bin)
    ok = (c >= 0) & (f >= 0)
    o = obs.loc[ok, ["icao24", "minute", "lat", "lon", t_col]].assign(cell=c[ok], fl=f[ok])
    if o.empty:
        return pd.DataFrame(columns=["icao24", "cell", "fl", "t0", "t1", "T", "lat", "lon"])
    ac, m = o["icao24"].to_numpy(), o["minute"].to_numpy(np.int64)
    cc, ff = o["cell"].to_numpy(), o["fl"].to_numpy()
    new = np.ones(len(o), dtype=bool)
    new[1:] = (
        (ac[1:] != ac[:-1]) | (m[1:] - m[:-1] != 60) | (cc[1:] != cc[:-1]) | (ff[1:] != ff[:-1])
    )
    pid = np.cumsum(new) - 1
    agg = (
        o.assign(pid=pid)
        .groupby("pid", sort=True)
        .agg(
            icao24=("icao24", "first"),
            cell=("cell", "first"),
            fl=("fl", "first"),
            t0=("minute", "first"),
            t1=("minute", "last"),
            T=(t_col, "max"),
            lat=("lat", "first"),
            lon=("lon", "first"),
        )
        .reset_index(drop=True)
    )
    agg["t1"] = agg["t1"] + 60
    return agg
