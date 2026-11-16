"""Real data for the project site (docs/index.html), written as small JS files so the
page works from file:// and from GitHub Pages alike (each sets one window.HORUS_* value).

    uv run python scripts/make_site_data.py      # needs the demo day (`make up`)

docs/data/traffic.js  one real hour over the US, through the live loop: aircraft
                      positions every 2 min (Int16, base64), every settled detection,
                      forecast cells, pilot reports with what Horus saw, state outlines
docs/data/signal.js   vertical rate + per-minute index of a detecting aircraft and of a
                      smooth aircraft of the same type at a similar level
docs/data/cases.js    the three case studies (cases/) as timelines
Same code path as `make watch` (build_hub + Watcher), so the page shows what the stream
printed for 2026-01-01 15:00-16:00Z.
"""

from __future__ import annotations

import base64
import json
import math
import sys
from typing import Any

import h3
import numpy as np
import pandas as pd

from horus import config
from horus.api.main import build_hub
from horus.core import geo, grid
from horus.places import Places
from horus.watch import Watcher

MINUTES, STEP = 60, 120  # map: one hour, a frame every 2 min (the page interpolates)
OUT = "docs/data"


def write_js(name: str, var: str, obj: Any) -> None:
    p = config.resolve(OUT) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    p.write_text(f"window.{var}={body};\n")
    print(f"{p}: {p.stat().st_size / 1e3:.0f} kB")


def outlines(places: Places) -> list[list[float]]:
    rings = []
    for _code, _box, path in places.rings:
        v = np.round(path.vertices, 2)
        keep = np.r_[True, np.any(np.diff(v, axis=0) != 0, axis=1)]
        rings.append(v[keep].ravel().tolist())
    return rings


def run(cfg: config.Config) -> dict[str, Any]:
    hub = build_hub(cfg)
    feed, places = hub.feed, Places()
    t0 = feed.t_first
    t1 = t0 + MINUTES * 60
    w = Watcher(hub, places, cfg.watch)
    normal, tau = w.normal, w.tau

    # positions: last fix of each aircraft (FL200+) in each 2-min frame
    rows = feed.rows[(feed.rows["time"] > t0 - STEP) & (feed.rows["time"] <= t1)]
    rows = rows[rows["baro_alt_ft"] >= cfg.segment.min_alt_ft]
    nf = MINUTES * 60 // STEP + 1
    f = np.clip(np.ceil((rows["time"].to_numpy() - t0) / STEP).astype(int), 0, nf - 1)
    last = rows.assign(f=f).sort_values("time").groupby(["icao24", "f"]).tail(1)
    icaos = sorted(last["icao24"].unique())
    idx = {a: i for i, a in enumerate(icaos)}
    pos = np.full((len(icaos), nf, 2), -32768, dtype="<i2")
    ai = last["icao24"].map(idx).to_numpy()
    pos[ai, last["f"].to_numpy(), 0] = np.round(last["lon"].to_numpy() * 100)
    pos[ai, last["f"].to_numpy(), 1] = np.round(last["lat"].to_numpy() * 100)
    per_frame = [int((pos[:, k, 0] != -32768).sum()) for k in range(nf)]  # true counts
    seen = (pos[:, :, 0] != -32768).sum(axis=1)
    pos = pos[seen >= 4]  # drawn: aircraft visible for >= 8 min (smaller file, same picture)
    icaos = [a for a, s in zip(icaos, seen, strict=True) if s >= 4]

    # the live loop, exactly as `make watch` drives it
    fresh: list[pd.DataFrame] = []
    cells: dict[int, list[list[float]]] = {}
    reports = []
    now = t0
    hub.now = t0
    while now < t1 + 6 * 60:
        now += feed.snapshot_s
        hub.step(now)
        fr = hub.loop.fresh
        if len(fr):
            fresh.append(fr[["minute", "icao24", "lat", "lon", "alt_ft", "T"]].copy())
        pi0 = w.pi
        w.update()
        tick = int(hub.loop.tick)
        if t0 <= tick <= t1 and (tick - t0) % STEP == 0 and tick not in cells:
            x = hub.loop.probabilities(float(tick)).max(axis=1) / normal
            j = np.flatnonzero(x >= cfg.watch.rearm_x)
            cells[tick] = [[int(c), round(float(x[c]), 1)] for c in j]
        for k in range(max(pi0, 0), w.pi if pi0 >= 0 else 0):
            r = w.pireps.iloc[k]
            tp = int(r["time"])
            if not t0 <= tp <= t1:
                continue
            lo, hi = float(r["fl_lo"]), float(r["fl_hi"])
            passed, felt = w._near(tp, float(r["lat"]), float(r["lon"]), lo, hi)
            fx = w._forecast_at(float(r["lat"]), float(r["lon"]), lo, hi, tick)
            typ = r.get("aircraft")
            reports.append(
                {
                    "t": tp - t0,
                    "lat": round(float(r["lat"]), 3),
                    "lon": round(float(r["lon"]), 3),
                    "lvl": int(r["tb_max"]),
                    "fl": [int(lo), int(hi)],
                    "type": typ if isinstance(typ, str) else "",
                    "where": places.where(float(r["lon"]), float(r["lat"])),
                    "passed": passed,
                    "felt": felt,
                    "fx": round(fx, 1),
                }
            )
    allf = pd.concat(fresh, ignore_index=True)
    allf = allf[(allf["minute"] >= t0) & (allf["minute"] <= t1)]
    det = allf[allf["T"].to_numpy(float) >= tau].sort_values("minute")
    types = hub.loop.types
    detections = [
        {
            "t": int(r.minute) + 60 - t0,  # data for the minute is complete at its end
            "lat": round(float(r.lat), 3),
            "lon": round(float(r.lon), 3),
            "fl": int(round(float(r.alt_ft) / 100)),
            "x": round(float(r.T) / tau, 2),
            "id": str(r.icao24),
            "type": types.get(str(r.icao24)) or "",
            "where": places.where(float(r.lon), float(r.lat)),
        }
        for r in det.itertuples()
    ]
    lattice = hub.loop.lattice_ids
    used = sorted({c for v in cells.values() for c, _ in v})
    hexes = {
        str(c): np.round(np.array(h3.cell_to_boundary(h3.int_to_str(int(lattice[c]))))[:, ::-1], 3)
        .ravel()
        .tolist()
        for c in used
    }
    traffic = {
        "t0": t0,
        "step": STEP,
        "frames": nf,
        "n": len(icaos),
        "pos": base64.b64encode(pos.tobytes()).decode(),
        "perFrame": per_frame,
        "detections": detections,
        "cells": {str(k - t0): v for k, v in sorted(cells.items())},
        "hexes": hexes,
        "reports": reports,
        "outlines": outlines(places),
        "normal": normal,
        "forecastX": cfg.watch.forecast_x,
    }
    return {
        "traffic": traffic,
        "fresh": allf,
        "rows": feed.rows,
        "tau": tau,
        "types": types,
        "hub": hub,
        "cfg": cfg,
    }


def context(d: dict[str, Any], hit: pd.Series) -> dict[str, Any]:
    """Real surroundings of the detection: its H3 cell, the GFS wind there, the lines
    `make watch` printed for it, and the next aircraft that flew through that cell."""
    from horus.core.winds import wind_at
    from horus.eval import passes

    cfg, hub, rows, allf, tau, types = (
        d[k] for k in ("cfg", "hub", "rows", "fresh", "tau", "types")
    )
    g = cfg.grid
    tc, lat, lon, alt = (
        int(hit["minute"]),
        float(hit["lat"]),
        float(hit["lon"]),
        float(hit["alt_ft"]),
    )
    cell = h3.latlng_to_cell(lat, lon, g.h3_res)
    u, v = wind_at(
        hub.winds, np.array([tc + 30.0]), np.array([lat]), np.array([lon]), np.array([alt])
    )
    spd_kt = float(np.hypot(u[0], v[0])) * 1.943844
    dir_from = (math.degrees(math.atan2(-float(u[0]), -float(v[0]))) + 360) % 360
    fbin = int(geo.fl_bin(np.array([alt]), g.fl_min, g.fl_max, g.fl_bin)[0])
    # the next other aircraft inside a cell the real forecast had lit (>= forecast_x, the
    # 2-min frames written above, so drift with the wind counts) within 2,000 ft of the
    # detection's band, in the 15 min after the detection
    t0, step = d["traffic"]["t0"], d["traffic"]["step"]
    lit = {
        int(k) + t0: {c for c, x in v if x >= cfg.watch.forecast_x}
        for k, v in d["traffic"]["cells"].items()
    }
    w = rows[
        (rows["time"] > tc + 60)
        & (rows["time"] <= tc + 60 + 900)
        & (rows["icao24"] != hit["icao24"])
    ]
    fb = geo.fl_bin(w["baro_alt_ft"].to_numpy(), g.fl_min, g.fl_max, g.fl_bin)
    w = w[(fb >= 0) & (np.abs(fb - fbin) <= 1)]
    idx = grid.cell_index(
        passes.cell_ids(w["lat"].to_numpy(), w["lon"].to_numpy(), g.h3_res),
        hub.loop.lattice_ids,
    )
    frame = (np.floor((w["time"].to_numpy() - t0) / step) * step + t0).astype(int)
    inside = [int(j) in lit.get(int(f), set()) for j, f in zip(idx, frame, strict=True)]
    w = w[np.array(inside, dtype=bool)].sort_values("time")
    nxt = None
    if len(w):
        r = w.iloc[0]
        a = str(r["icao24"])
        m = allf[
            (allf["icao24"] == a)
            & (allf["minute"] >= int(r["time"]) - 60)
            & (allf["minute"] <= int(r["time"]) + 900)
        ]
        xs = (m["T"].to_numpy(float) / tau) if len(m) else np.array([])
        xs = xs[np.isfinite(xs)]
        nxt = {
            "id": a,
            "type": types.get(a) or "",
            "fl": int(round(float(r["baro_alt_ft"]) / 100)),
            "after_min": round((int(r["time"]) - (tc + 60)) / 60),
            "peak": round(float(xs.max()), 2) if len(xs) else None,
        }
    lines = config.resolve("docs/watch-sample.txt").read_text().splitlines()
    turb = next((ln.strip() for ln in lines if "TURBULENCE" in ln and str(hit["icao24"]) in ln), "")
    i = next((k for k, ln in enumerate(lines) if ln.strip() == turb), -1)
    fc = next((ln.strip() for ln in lines[i + 1 : i + 6] if "FORECAST" in ln), "") if i >= 0 else ""
    return {
        "cell": cell,
        "lat": round(lat, 3),
        "lon": round(lon, 3),
        "wind_kt": round(spd_kt),
        "wind_from": round(dir_from),
        "next": nxt,
        "lines": {"turb": " ".join(turb.split()), "fc": " ".join(fc.split())},
    }


def signal(d: dict[str, Any]) -> dict[str, Any]:
    """The strongest typed detection below FL460, and a smooth same-type aircraft."""
    allf, rows, tau, types = d["fresh"], d["rows"], d["tau"], d["types"]
    t0 = d["traffic"]["t0"]
    det = allf[(allf["T"] >= tau) & (allf["alt_ft"] < 46000)].copy()
    det["type"] = det["icao24"].map(types)
    det = det[det["type"].notna()].sort_values("T", ascending=False)
    hit = det.iloc[0]
    tc = int(hit["minute"])
    lo_t, hi_t = max(tc - 15 * 60, t0), tc + 15 * 60
    same = allf[
        allf["icao24"].map(types).eq(hit["type"])
        & (allf["minute"] >= lo_t)
        & (allf["minute"] <= hi_t)
        & ((allf["alt_ft"] - hit["alt_ft"]).abs() <= 2000)
    ]
    g = same.groupby("icao24")["T"].agg(["max", "count"])
    g = g[(g["max"] < 0.25 * tau) & (g.index != hit["icao24"])].sort_values("count")
    calm_id = g.index[-1]

    def series(icao: str) -> dict[str, Any]:
        r = rows[(rows["icao24"] == icao) & (rows["time"] >= lo_t) & (rows["time"] <= hi_t)]
        m = allf[(allf["icao24"] == icao) & (allf["minute"] >= lo_t) & (allf["minute"] <= hi_t)]
        alt = float(m["alt_ft"].median())
        lat, lon = float(m["lat"].median()), float(m["lon"].median())
        return {
            "id": icao,
            "type": types.get(icao) or "",
            "fl": int(round(alt / 100)),
            "where": Places().where(lon, lat),
            "vr": [
                [int(t - lo_t), int(v)]
                for t, v in zip(r["time"], r["vr_fpm"], strict=True)
                if v == v and abs(v) < 10000
            ],
            "idx": [
                [int(mm - lo_t), round(float(T) / tau, 3)]
                for mm, T in zip(m["minute"], m["T"], strict=True)
                if T == T
            ],
        }

    return {
        "start": lo_t,
        "span": hi_t - lo_t,
        "turbulent": series(str(hit["icao24"])),
        "smooth": series(str(calm_id)),
        "context": context(d, hit),
    }


def cases(cfg: config.Config, tau: float) -> list[dict[str, Any]]:
    out = []
    places = Places()
    for p in sorted(config.resolve(cfg.watch.cases_dir).glob("*.json")):
        c = json.loads(p.read_text())
        m, frames = c["meta"], c["frames"]
        normal = float(m["floor"]) / 2
        lo, hi = float(m["fl_lo"]), float(m["fl_hi"])
        R = cfg.watch.case_radius_km

        def dist(lat: float, lon: float, m: dict[str, Any] = m) -> float:
            return float(geo.haversine_km(lat, lon, m["lat"], m["lon"]))

        dets, lit, count = [], [], []
        for f in frames:
            rel = round((f["t"] - m["time"]) / 60)
            near = [a for a in f["aircraft"] if dist(a[1], a[2]) <= R]
            count.append([rel, len(near)])
            for icao, lat, lon, fl, T in near:
                if T is not None and T >= tau:
                    d = dist(lat, lon)
                    dets.append(
                        {
                            "m": rel,
                            "d": round(d, 1),
                            "fl": int(fl),
                            "x": round(T / tau, 2),
                            "id": icao,
                            "match": d <= cfg.watch.pirep_near_km and lo - 20 <= fl <= hi + 20,
                        }
                    )
            hot = [
                (pv / normal, dist(*h3.cell_to_latlng(h)))
                for h, pv in f["cells"]
                if pv / normal >= cfg.watch.forecast_x
            ]
            hot = [x for x in hot if x[1] <= R]
            lit.append([rel, len(hot), round(max((x for x, _ in hot), default=0), 1)])
        out.append(
            {
                "id": m["id"],
                "when": m["time"],
                "level": m["intensity"],
                "fl": [int(lo), int(hi)],
                "where": places.where(m["lon"], m["lat"]),
                "detections": dets,
                "lit": lit,
                "count": count,
                "radius": R,
                "near": cfg.watch.pirep_near_km,
            }
        )
    return out


def main() -> int:
    cfg = config.load()
    d = run(cfg)
    write_js("traffic.js", "HORUS_TRAFFIC", d["traffic"])
    write_js("signal.js", "HORUS_SIGNAL", signal(d))
    write_js("cases.js", "HORUS_CASES", cases(cfg, d["tau"]))
    t = d["traffic"]
    print(
        f"aircraft {t['n']}, detections {len(t['detections'])}, "
        f"forecast frames {len(t['cells'])}, reports {len(t['reports'])}, "
        f"hexes {len(t['hexes'])}, tau {d['tau']:.3f}"
    )
    assert math.isfinite(d["tau"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
