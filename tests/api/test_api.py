"""API (horus/api/main.py) against a hub built from the recorded 30-min replay."""

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from horus import config
from horus.api.live_loop import LiveLoop
from horus.api.main import Feed, Hub, create_app
from horus.core import grid
from horus.core import normalise as nz
from tests.conftest import FIXTURES

CFG = config.load()


def _hub() -> Hub:
    rows = pd.read_parquet(FIXTURES / "replay_30min.parquet").sort_values("time")
    ids = grid.region_cells(24.0, 50.0, -125.0, -66.0, CFG.grid.h3_res)
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
    loop = LiveLoop(CFG, norm, {}, params, ids, t0=int(rows["time"].min()))
    pir = pd.DataFrame(
        {
            "time": [int(rows["time"].min()) + 600],
            "lat": [40.0],
            "lon": [-100.0],
            "fl_lo": [330.0],
            "fl_hi": [350.0],
            "tb_max": [4],
        }
    )
    return Hub(loop, Feed(rows.reset_index(drop=True), snapshot_s=60), CFG, pir, speed=1e6)


def test_endpoints_and_websocket() -> None:
    hub = _hub()
    with TestClient(create_app(hub)) as c:
        with c.websocket_connect("/ws/live") as ws:
            msgs = [ws.receive_json() for _ in range(12)]
        assert msgs[0]["type"] == "snapshot"
        assert any(m["aircraft"] for m in msgs)
        assert msgs[-1]["tick"] > msgs[0]["tick"] or msgs[-1]["tick"] >= msgs[0]["tick"]
        h = c.get("/api/health").json()
        assert h["ok"] and "not for operational use" in h["disclaimer"].lower()
        g = c.get("/api/grid", params={"fl": 350}).json()
        assert g["fl_bin"] == [340, 360] and isinstance(g["cells"], list)
        assert c.get("/api/grid", params={"fl": 100}).status_code == 400
        assert isinstance(c.get("/api/aircraft").json()["aircraft"], list)
        assert c.get("/api/metrics").json()["n"] > 0
        assert c.get("/api/replay/..%2Fsecrets").status_code == 404
        assert isinstance(c.get("/api/pireps", params={"since": 7200}).json()["pireps"], list)
