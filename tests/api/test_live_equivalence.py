"""Live loop == offline (SPEC §9): a recorded 30-min replay fed as live snapshots
(two different chunkings) ends in exactly the grid state of the offline run."""

import numpy as np
import pandas as pd
import pytest

from horus import config
from horus.api.live_loop import TICK_S, LiveLoop
from horus.core import geo, grid
from horus.core import normalise as nz
from horus.eval import passes, scores
from tests.conftest import FIXTURES

CFG = config.load()


@pytest.fixture(scope="module")
def setup():
    rows = pd.read_parquet(FIXTURES / "replay_30min.parquet")
    ids = grid.region_cells(24.0, 50.0, -125.0, -66.0, CFG.grid.h3_res)
    lat = grid.Lattice.build(ids)
    norm = nz.TypeNorm(
        nz.NormParams("quantile_scale", 0.99, 0),
        pd.DataFrame([{"level": "global", "key": "ALL", "centre": 0.0, "scale": 100.0}]),
        {},
    )
    n_fl = (CFG.grid.fl_max - CFG.grid.fl_min) // CFG.grid.fl_bin
    params = grid.GridParams(
        l0=-5.0,
        half_life_s=np.full(n_fl, 1800.0),
        a=1e6,
        l_pos_max=3.0,
        tau=1.0,
        l_min=-8,
        l_max=-2,
    )
    u = np.full((len(ids), n_fl), 40.0)
    v = np.full((len(ids), n_fl), -10.0)
    return rows, ids, lat, norm, params, (lambda t: (u, v))


def _loop(setup) -> LiveLoop:
    rows, ids, lat, norm, params, wind = setup
    t0 = int(rows["time"].min())
    return LiveLoop(CFG, norm, {}, params, ids, wind, lat, t0=t0)


def _feed(loop: LiveLoop, rows: pd.DataFrame, every: int) -> int:
    t_end = int(rows["time"].max())
    for now in range(int(rows["time"].min()) + every, t_end + every, every):
        loop.ingest(rows[(rows["time"] > now - every) & (rows["time"] <= now)])
        loop.step(min(now, t_end))
    return t_end


def _offline(setup, t_end: int, settle: int) -> grid.GridState:
    rows, ids, lat, norm, params, wind = setup
    r = rows.sort_values(["icao24", "time"])
    codes, uniq = pd.factorize(r["icao24"], sort=True)
    cols = {c: r[c].to_numpy() for c in ["lat", "lon", "baro_alt_ft", "vr_fpm", "on_ground"]}
    cols["vr_fpm"] = cols["vr_fpm"].astype(float)
    cols["on_ground"] = cols["on_ground"].astype(bool)
    out = scores.score_arrays(codes, r["time"].to_numpy(np.int64), cols, CFG)
    out["icao24"] = np.asarray(uniq)[out.pop("key").to_numpy()]
    out["T"] = nz.index(
        norm,
        out["b2"].to_numpy(float),
        np.full(len(out), None, object),
        np.full(len(out), "UNK", object),
    )
    out = out[out["minute"] + TICK_S <= t_end - settle]
    t0 = int(rows["time"].min())
    n_fl = (CFG.grid.fl_max - CFG.grid.fl_min) // CFG.grid.fl_bin
    st = grid.GridState.empty(len(ids), n_fl, params.l0, t0)
    tick = t0 - t0 % TICK_S
    while tick + TICK_S <= t_end - settle:
        tick += TICK_S
        u, v = wind(float(tick))
        grid.advect(st, lat, u, v, TICK_S, float(tick), params)
        ev = out[out["minute"] + TICK_S == tick].sort_values(["icao24", "minute"])
        if len(ev):
            c = grid.cell_index(
                passes.cell_ids(ev["lat"].to_numpy(), ev["lon"].to_numpy(), CFG.grid.h3_res), ids
            )
            f = geo.fl_bin(
                ev["alt_ft"].to_numpy(), CFG.grid.fl_min, CFG.grid.fl_max, CFG.grid.fl_bin
            )
            grid.update(st, c, f, ev["T"].to_numpy(float), np.ones(len(ev)), float(tick), params)
    return st


def test_live_equals_offline_for_two_chunkings(setup) -> None:
    rows = setup[0]
    a, b = _loop(setup), _loop(setup)
    t_end = _feed(a, rows, 90)
    _feed(b, rows, 7)
    off = _offline(setup, t_end, a.settle_s)
    assert a.tick == b.tick
    np.testing.assert_array_equal(a.state.L, b.state.L)
    np.testing.assert_array_equal(a.state.L, off.L)
    assert (a.state.L > setup[4].l0 + 1e-3).sum() > 0  # evidence was actually applied
