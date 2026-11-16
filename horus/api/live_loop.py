"""Live loop (SPEC §7, M6): snapshots in, grid out, using the offline code paths.

Each snapshot appends track rows to per-aircraft buffers (last `buffer_s`). On
every step the buffered tracks are re-scored with the same function the offline
bench uses (`eval.scores.score_arrays`), turned into the B3 index with the same
per-type normalisation, and every minute that is *settled* (its scores can no
longer change: no window or trim reaches past the data seen so far) is applied
to the grid. The grid advances on fixed 60 s ticks aligned to epoch time:
advect to the tick, then add the evidence of the minutes that ended at it. That
order depends only on the data, never on when snapshots arrive, so a replay fed
in any chunking ends in exactly the state of the offline run (tested in
tests/api/test_live_equivalence.py).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from horus.config import Config
from horus.core import geo, grid
from horus.core import normalise as nz
from horus.eval import passes, scores

TICK_S = 60
COLS = ["lat", "lon", "baro_alt_ft", "vr_fpm", "on_ground"]


@dataclass
class Evidence:
    """Settled aircraft-minutes waiting for their tick (minute end)."""

    rows: list[pd.DataFrame] = field(default_factory=list)

    def take(self, tick: int) -> pd.DataFrame:
        if not self.rows:
            return pd.DataFrame()
        df = pd.concat(self.rows, ignore_index=True)
        due = df["minute"] + TICK_S <= tick
        self.rows = [df[~due]] if (~due).any() else []
        return df[due]


@dataclass
class LiveLoop:
    cfg: Config
    norm: nz.TypeNorm
    sizes: dict[str, str]
    params: grid.GridParams
    lattice_ids: np.ndarray
    wind_at: Callable[[float], tuple[np.ndarray, np.ndarray]] | None = None
    lattice: grid.Lattice | None = None
    t0: int = 0
    settle_s: int = 240  # > segment half-window (60) + trim (30) + stat/trend reach
    buffer_s: int = 900
    tracks: pd.DataFrame = field(default_factory=pd.DataFrame)
    types: dict[str, str | None] = field(default_factory=dict)
    emitted: set[tuple[str, int]] = field(default_factory=set)
    pending: Evidence = field(default_factory=Evidence)
    tick: int = 0
    latest: pd.DataFrame = field(default_factory=pd.DataFrame)  # last minute row per aircraft
    fresh: pd.DataFrame = field(default_factory=pd.DataFrame)  # minutes settled by the last step

    def __post_init__(self) -> None:
        g = self.cfg.grid
        n_fl = (g.fl_max - g.fl_min) // g.fl_bin
        self.state = grid.GridState.empty(len(self.lattice_ids), n_fl, self.params.l0, self.t0)
        self.tick = self.t0 - self.t0 % TICK_S

    def ingest(self, rows: pd.DataFrame) -> None:
        """Rows in the track schema (icao24, time, lat, lon, baro_alt_ft, vr_fpm,
        on_ground, typecode). Later rows for the same (aircraft, second) win."""
        if rows.empty:
            return
        for a, t in zip(rows["icao24"], rows["typecode"], strict=True):
            if t is not None and t == t:
                self.types[str(a)] = str(t)
        keep = ["icao24", "time", *COLS]
        self.tracks = pd.concat([self.tracks, rows[keep]], ignore_index=True)

    def _score(self, now: int) -> pd.DataFrame:
        tr = self.tracks[self.tracks["time"] > now - self.buffer_s]
        tr = tr.sort_values(["icao24", "time"], kind="stable")
        tr = tr.drop_duplicates(["icao24", "time"], keep="last")
        self.tracks = tr
        if tr.empty:
            return pd.DataFrame()
        codes, uniq = pd.factorize(tr["icao24"], sort=True)
        cols = {c: tr[c].to_numpy() for c in COLS}
        cols["vr_fpm"] = cols["vr_fpm"].astype(float)
        cols["on_ground"] = cols["on_ground"].astype(bool)
        out = scores.score_arrays(codes, tr["time"].to_numpy(np.int64), cols, self.cfg)
        if out.empty:
            return out
        out["icao24"] = np.asarray(uniq)[out.pop("key").to_numpy()]
        tc = out["icao24"].map(self.types).astype(object)
        out["T"] = nz.index(
            self.norm,
            out["b2"].to_numpy(float),
            tc.to_numpy(object),
            tc.map(self.sizes).astype(object).fillna("UNK").to_numpy(object),
        )
        return out

    def step(self, now: int) -> dict[str, object]:
        """Advance to `now`: score buffers, queue newly settled minutes, then run
        every 60 s tick up to the settled horizon. Returns a summary for clients."""
        out = self._score(now)
        self.fresh = pd.DataFrame()
        if not out.empty:
            self.latest = out.sort_values("minute").groupby("icao24").tail(1)
            settled = out[out["minute"] + TICK_S <= now - self.settle_s]
            # first minute of the buffer may be cut by the buffer edge: skip it
            settled = settled[settled["minute"] >= now - self.buffer_s + self.settle_s]
            key = list(zip(settled["icao24"], settled["minute"], strict=True))
            new = [k not in self.emitted for k in key]
            fresh = settled[new]
            self.fresh = fresh
            self.emitted.update(k for k, n in zip(key, new, strict=True) if n)
            if len(fresh):
                self.pending.rows.append(fresh)
        horizon = now - self.settle_s
        changed = 0
        while self.tick + TICK_S <= horizon:
            self.tick += TICK_S
            changed += self.apply_tick(self.tick)
        self.emitted = {k for k in self.emitted if k[1] > now - 2 * self.buffer_s}
        return {"t": now, "tick": self.tick, "aircraft": len(self.latest), "updated": changed}

    def apply_tick(self, tick: int) -> int:
        g = self.cfg.grid
        if self.wind_at is not None and self.lattice is not None and g.advection:
            u, v = self.wind_at(float(tick))
            grid.advect(self.state, self.lattice, u, v, TICK_S, float(tick), self.params)
        ev = self.pending.take(tick)
        if ev.empty:
            return 0
        ev = ev.sort_values(["icao24", "minute"], kind="stable")  # order-independent sums
        c = grid.cell_index(
            passes.cell_ids(ev["lat"].to_numpy(), ev["lon"].to_numpy(), g.h3_res), self.lattice_ids
        )
        f = geo.fl_bin(ev["alt_ft"].to_numpy(), g.fl_min, g.fl_max, g.fl_bin)
        T = ev["T"].to_numpy(float)
        grid.update(self.state, c, f, T, np.ones(len(ev)), float(tick), self.params)
        return int(((c >= 0) & (f >= 0)).sum())

    def probabilities(self, now: float) -> np.ndarray:
        return grid.read(self.state, now, self.params)
