"""Horus API (SPEC §7): FastAPI + WebSocket over the live loop.

    uv run uvicorn horus.api.main:app --port 8000

Endpoints
    GET  /api/health                 status, data time, aircraft count
    GET  /api/grid?fl=350&floor=     cells [h3, p] for one FL bin with p above floor
    GET  /api/aircraft               latest per-aircraft position and B3 index
    GET  /api/pireps?since=          turbulence PIREPs near the data time
    GET  /api/replays                curated replays (data/replays/*.json)
    GET  /api/replay/{id}            frames: aircraft + grid cells per minute
    GET  /api/metrics                end-to-end latency p50/p95 (snapshot -> WS send)
    WS   /ws/live                    one message per snapshot: aircraft + changed cells
The feed replays an archive day through the live loop at `speed`x real time (the
free live sources give ~90 s per aircraft, too sparse for the detector; see
reports/data_notes.md). Static map front end is served from web/.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import h3
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from horus import config
from horus.api.live_loop import LiveLoop
from horus.config import Config
from horus.core import grid
from horus.core.winds import WindField, wind_at
from horus.eval import detector_bench as db
from horus.eval import nowcast_bench as nb
from horus.eval import scores

DISCLAIMER = "Research prototype. Not for operational use."


@dataclass
class Feed:
    """Archive rows released snapshot by snapshot, as a live source would."""

    rows: pd.DataFrame
    snapshot_s: int = 30
    pos: int = 0

    @property
    def t_first(self) -> int:
        return int(self.rows["time"].min())

    @property
    def t_last(self) -> int:
        return int(self.rows["time"].max())

    def next(self, now: int) -> pd.DataFrame:
        t = self.rows["time"].to_numpy()
        end = int(np.searchsorted(t, now, side="right"))
        out = self.rows.iloc[self.pos : end]
        self.pos = end
        return out


@dataclass
class Hub:
    loop: LiveLoop
    feed: Feed | None
    cfg: Config
    pireps: pd.DataFrame
    speed: float = 30.0
    winds: WindField | None = None
    now: int = 0
    latency_ms: list[float] = field(default_factory=list)
    clients: set[WebSocket] = field(default_factory=set)
    last_p: np.ndarray | None = None
    _lattice_bytes: bytes | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def floor(self) -> float:
        """Display floor: twice the prior probability."""
        return float(grid.sigmoid(np.array(self.loop.params.l0))) * 2

    def cells(self, fl_idx: int, floor: float) -> list[list[Any]]:
        p = self.loop.probabilities(float(self.now))[:, fl_idx]
        idx = np.flatnonzero(p > floor)
        return [[h3.int_to_str(int(self.loop.lattice_ids[i])), round(float(p[i]), 5)] for i in idx]

    def lattice_json(self) -> bytes:
        if self._lattice_bytes is None:
            geo = {
                c: [[round(lo, 3), round(la, 3)] for la, lo in h3.cell_to_boundary(c)]
                for c in (h3.int_to_str(int(i)) for i in self.loop.lattice_ids)
            }
            g = self.cfg.grid
            body = {
                "cells": geo,
                "fl_bins": list(range(g.fl_min, g.fl_max, g.fl_bin)),
                "floor": self.floor,
            }
            self._lattice_bytes = json.dumps(body).encode()
        return self._lattice_bytes

    def aircraft(self) -> list[dict[str, Any]]:
        """Latest raw position per aircraft (updates every snapshot, so the map can
        glide between them) with its latest scored B3 index."""
        with self.lock:
            tr = self.loop.tracks
            lt = self.loop.latest
        if tr.empty:
            return []
        last = tr[tr["time"] > self.now - 120].groupby("icao24", sort=False).tail(1)
        last = last[last["baro_alt_ft"] >= self.cfg.segment.min_alt_ft]
        T = lt.set_index("icao24")["T"] if not lt.empty else pd.Series(dtype=float)
        tv = last["icao24"].map(T)
        return [
            {
                "icao24": r.icao24,
                "lat": round(float(r.lat), 4),
                "lon": round(float(r.lon), 4),
                "fl": int(round(float(r.baro_alt_ft) / 100)),
                "T": None if pd.isna(t) else round(float(t), 3),
            }
            for r, t in zip(last.itertuples(), tv, strict=True)
        ]

    def changed(self, floor: float) -> list[list[Any]]:
        p = self.loop.probabilities(float(self.now))
        prev = self.last_p if self.last_p is not None else np.full_like(p, -1.0)
        diff = np.argwhere(np.abs(p - prev) > 1e-6)
        self.last_p = p
        return [
            [h3.int_to_str(int(self.loop.lattice_ids[c])), int(f), round(float(p[c, f]), 5)]
            for c, f in diff
            if p[c, f] > floor or prev[c, f] > floor
        ]

    def restart(self) -> None:
        """Loop the archive window: fresh loop state, feed from the start."""
        assert self.feed is not None
        with self.lock:
            lp = self.loop
            self.loop = LiveLoop(
                lp.cfg,
                lp.norm,
                lp.sizes,
                lp.params,
                lp.lattice_ids,
                lp.wind_at,
                lp.lattice,
                t0=self.feed.t_first,
            )
            self.feed.pos = 0
            self.now = self.feed.t_first
            self.last_p = None

    def _advance(self, rows: pd.DataFrame, now: int) -> dict[str, Any]:
        with self.lock:
            self.loop.ingest(rows)
            return self.loop.step(now)

    async def run(self) -> None:
        if self.feed is None:
            return
        self.now = self.feed.t_first
        snap = self.feed.snapshot_s
        while True:
            self.now += snap
            if self.now > self.feed.t_last + snap:  # window played out: start it again
                self.restart()
            t_recv = time.perf_counter()
            rows = self.feed.next(self.now)
            # scoring takes ~1 s for CONUS: run it off the event loop so HTTP stays responsive
            summary = await asyncio.to_thread(self._advance, rows, self.now)
            floor = self.floor
            msg = json.dumps(
                {
                    "type": "snapshot",
                    **summary,
                    "aircraft": self.aircraft(),
                    "cells": self.changed(floor),
                }
            )
            for ws in list(self.clients):
                try:
                    await ws.send_text(msg)
                except Exception:
                    self.clients.discard(ws)
            self.latency_ms.append((time.perf_counter() - t_recv) * 1e3)
            self.latency_ms = self.latency_ms[-2000:]
            await asyncio.sleep(max(snap / self.speed - (time.perf_counter() - t_recv), 0.0))


def build_hub(cfg: Config, day: str | None = None, hour: int = 15, hours: float = 3.0) -> Hub:
    """Hub over the trained models and an archive window of the test split."""
    norm, sizes = nb.load_norm(), db.size_map()
    hl = json.loads(config.resolve("models/half_life.json").read_text())
    params, _ = nb.grid_params(cfg, hl)
    r = cfg.region
    ids = grid.region_cells(r.lat_min, r.lat_max, r.lon_min, r.lon_max, cfg.grid.h3_res)
    from horus.ingest import winds as wio

    wf = wio.load(
        wio.path(
            cfg, date.fromisoformat(cfg.splits.train_start), date.fromisoformat(cfg.splits.test_end)
        )
    )
    g = cfg.grid
    lat = grid.Lattice.build(ids)
    cw = nb.CellWinds(lat, wf, (np.arange(g.fl_min, g.fl_max, g.fl_bin) + g.fl_bin / 2) * 100.0)
    day = day or cfg.splits.test[0]
    t0 = int(pd.Timestamp(date.fromisoformat(day), tz="UTC").timestamp()) + hour * 3600
    tab = scores.load_tracks(date.fromisoformat(day), cfg).to_pandas()
    rows = tab[(tab["time"] >= t0) & (tab["time"] < t0 + hours * 3600)].sort_values("time")
    loop = LiveLoop(cfg, norm, sizes, params, ids, cw.at, lat, t0=t0)
    pir = db.load_pireps([day])
    pir = pir[pir["has_tb"] & pir["lat"].notna()]
    return Hub(loop, Feed(rows.reset_index(drop=True)), cfg, pir, winds=wf)


def create_app(hub: Hub | None = None, start_feed: bool = True) -> FastAPI:
    state: dict[str, Hub] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        state["hub"] = hub or build_hub(config.load())
        state["hub"].lattice_json()  # build before the feed starts competing for the GIL
        task = asyncio.create_task(state["hub"].run()) if start_feed else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Horus", lifespan=lifespan)

    def H() -> Hub:
        return state["hub"]

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        h = H()
        return {
            "ok": True,
            "time": h.now,
            "tick": h.loop.tick,
            "aircraft": len(h.aircraft()),
            "window": [h.feed.t_first, h.feed.t_last] if h.feed is not None else None,
            "disclaimer": DISCLAIMER,
        }

    @app.get("/api/grid")
    def grid_cells(fl: int = 350, floor: float = 0.0) -> dict[str, Any]:
        g = H().cfg.grid
        if not g.fl_min <= fl < g.fl_max:
            raise HTTPException(400, f"fl must be in [{g.fl_min}, {g.fl_max})")
        i = (fl - g.fl_min) // g.fl_bin
        f = floor or H().floor
        return {
            "time": H().now,
            "fl_bin": [g.fl_min + i * g.fl_bin, g.fl_min + (i + 1) * g.fl_bin],
            "cells": H().cells(i, f),
        }

    @app.get("/api/lattice")
    def lattice() -> Response:
        """Cell outlines (built once, cached as bytes; the client caches them too)."""
        return Response(H().lattice_json(), media_type="application/json")

    @app.get("/api/winds")
    def winds(fl: int = 340, t: int = 0) -> dict[str, Any]:
        """Upper wind (u, v m/s) on the GFS lattice at flight level `fl`, for the hour
        containing `t` (default: the feed time). Drives the animated wind layer."""
        h = H()
        if h.winds is None:
            raise HTTPException(404, "no wind field loaded")
        w = h.winds
        tt = float(t or h.now)
        hour = float(np.floor(tt / 3600) * 3600 + 1800)
        la, lo = np.meshgrid(w.lat, w.lon, indexing="ij")
        n = la.size
        u, v = wind_at(w, np.full(n, hour), la.ravel(), lo.ravel(), np.full(n, fl * 100.0))
        return {
            "t": int(hour - 1800),
            "fl": fl,
            "lat": [float(x) for x in w.lat],
            "lon": [float(x) for x in w.lon],
            "u": np.round(u.reshape(la.shape), 2).tolist(),
            "v": np.round(v.reshape(la.shape), 2).tolist(),
        }

    @app.get("/api/thresholds")
    def thresholds() -> dict[str, float]:
        return {"tau_b3": float(H().loop.params.tau), "floor": H().floor}

    @app.get("/api/basemap")
    def basemap() -> FileResponse:
        return FileResponse(config.resolve("demo/data/conus_states.json"))

    @app.get("/api/aircraft")
    def aircraft() -> dict[str, Any]:
        return {"time": H().now, "aircraft": H().aircraft()}

    @app.get("/api/pireps")
    def pireps(since: int = 3600) -> dict[str, Any]:
        h = H()
        p = h.pireps[(h.pireps["time"] <= h.now) & (h.pireps["time"] > h.now - since)]
        cols = ["time", "lat", "lon", "fl_lo", "fl_hi", "tb_max"]
        return {"time": h.now, "pireps": json.loads(p[cols].to_json(orient="records"))}

    @app.get("/api/metrics")
    def metrics() -> dict[str, Any]:
        lat = np.asarray(H().latency_ms)
        if len(lat) == 0:
            return {"n": 0}
        return {
            "n": int(len(lat)),
            "p50_ms": float(np.median(lat)),
            "p95_ms": float(np.quantile(lat, 0.95)),
        }

    @app.get("/api/replays")
    def replays() -> list[dict[str, Any]]:
        d = config.resolve("data/replays")
        return (
            [json.loads(p.read_text())["meta"] for p in sorted(d.glob("*.json"))]
            if d.exists()
            else []
        )

    @app.get("/api/replay/{rid}")
    def replay(rid: str) -> dict[str, Any]:
        p = config.resolve("data/replays") / f"{rid}.json"
        if not rid.replace("-", "").isalnum() or not p.exists():
            raise HTTPException(404, "no such replay")
        return json.loads(p.read_text())

    @app.websocket("/ws/live")
    async def live(ws: WebSocket) -> None:
        await ws.accept()
        H().clients.add(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            H().clients.discard(ws)

    glass = config.resolve("web-glass/dist")
    if glass.exists():  # liquid-glass / bento front end (built with `make glass`)
        app.mount("/glass", StaticFiles(directory=glass, html=True), name="glass")

    web = config.resolve("web")
    if web.exists():
        app.mount("/static", StaticFiles(directory=web), name="static")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(web / "index.html")

    return app


app = create_app()
