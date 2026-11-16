"""Curated replays (SPEC §7, M6): MOD+ test-day PIREPs replayed through the live loop.

    uv run python scripts/make_replays.py --n 3

Picks the n MOD+ PIREP windows on test days (one per day, >= min_passes aircraft)
with the highest B3 window score: they show the detector at its clearest, and the
UI labels them that way. For each, tracks within 400 km and ±45 min go through
LiveLoop minute by minute; frames from -30 to +30 min keep the aircraft (with T)
and the grid cells above the display floor in the PIREP's FL bins.
"""

from __future__ import annotations

import argparse
import json
from datetime import date

import h3
import numpy as np
import pandas as pd

from horus import config
from horus.api.live_loop import LiveLoop
from horus.core import geo, grid
from horus.core.geo import haversine_km
from horus.eval import detector_bench as db
from horus.eval import nowcast_bench as nb
from horus.eval import scores
from horus.ingest import winds as wio
from horus.ingest.pireps import MOD, SEV


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=3)
    a = ap.parse_args()
    cfg = config.load()
    w = pd.read_parquet(config.resolve("data/derived/bench/windows_test.parquet"))
    w = w[(w["win_s"] == cfg.match.window_s) & (w["dist_km"] == cfg.match.dist_km)]
    w = w[
        (w["label_src"] == "pirep")
        & (w["tb_max"] >= MOD)
        & (w["n_passes"] >= cfg.match.negatives.min_passes)
    ]
    picks = w.sort_values("t_q", ascending=False).drop_duplicates("date").head(a.n)

    norm, sizes = nb.load_norm(), db.size_map()
    params, _ = nb.grid_params(cfg, json.loads(config.resolve("models/half_life.json").read_text()))
    r = cfg.region
    ids = grid.region_cells(r.lat_min, r.lat_max, r.lon_min, r.lon_max, cfg.grid.h3_res)
    lat_c = grid.Lattice.build(ids)
    wf = wio.load(
        wio.path(
            cfg, date.fromisoformat(cfg.splits.train_start), date.fromisoformat(cfg.splits.test_end)
        )
    )
    g = cfg.grid
    cw = nb.CellWinds(lat_c, wf, (np.arange(g.fl_min, g.fl_max, g.fl_bin) + g.fl_bin / 2) * 100.0)
    floor = float(grid.sigmoid(np.array(params.l0))) * 2
    out_dir = config.resolve("data/replays")
    out_dir.mkdir(parents=True, exist_ok=True)

    for k, ev in enumerate(picks.itertuples(), 1):
        t_ev = int(ev.time)
        tab = scores.load_tracks(date.fromisoformat(ev.date), cfg).to_pandas()
        tab = tab[(tab["time"] >= t_ev - 2700) & (tab["time"] <= t_ev + 2700)]
        near = haversine_km(ev.lat, ev.lon, tab["lat"].to_numpy(), tab["lon"].to_numpy()) <= 400
        tab = tab[near].sort_values("time")
        loop = LiveLoop(cfg, norm, sizes, params, ids, cw.at, lat_c, t0=t_ev - 2700)
        fl_lo = geo.fl_bin(np.array([ev.alt_lo_ft]), g.fl_min, g.fl_max, g.fl_bin)[0]
        fl_hi = geo.fl_bin(np.array([ev.alt_hi_ft]), g.fl_min, g.fl_max, g.fl_bin)[0]
        bins = [b for b in range(max(fl_lo, 0), (fl_hi if fl_hi >= 0 else fl_lo) + 1)] or [0]
        frames = []
        for now in range(t_ev - 2700 + 60, t_ev + 1801, 60):
            loop.ingest(tab[(tab["time"] > now - 60) & (tab["time"] <= now)])
            loop.step(now)
            if now < t_ev - 1800:
                continue
            p = loop.probabilities(float(now))[:, bins].max(axis=1)
            cells = [
                [h3.int_to_str(int(ids[i])), round(float(p[i]), 5)]
                for i in np.flatnonzero(p > floor)
            ]
            lt = loop.latest
            lt = lt[lt["minute"] >= now - 180]
            ac = [
                [
                    x.icao24,
                    round(float(x.lat), 3),
                    round(float(x.lon), 3),
                    int(round(float(x.alt_ft) / 100)),
                    None if pd.isna(x.T) else round(float(x.T), 2),
                ]
                for x in lt.itertuples()
            ]
            frames.append({"t": now, "aircraft": ac, "cells": cells})
        rid = f"r{k}-{ev.date}"
        tb = "SEV" if ev.tb_max >= SEV else "MOD"
        meta = {
            "id": rid,
            "date": ev.date,
            "time": t_ev,
            "lat": float(ev.lat),
            "lon": float(ev.lon),
            "fl_lo": int(ev.alt_lo_ft / 100),
            "fl_hi": int(ev.alt_hi_ft / 100),
            "intensity": tb,
            "aircraft_in_window": int(ev.n_passes),
            "b3_window_score": round(float(ev.t_q), 2),
            "selection": "highest B3 window score among MOD+ test-day PIREPs (one per day)",
            "floor": floor,
        }
        (out_dir / f"{rid}.json").write_text(json.dumps({"meta": meta, "frames": frames}))
        print(
            f"{rid}: {tb} FL{meta['fl_lo']}-{meta['fl_hi']} at {ev.lat:.2f},{ev.lon:.2f}; "
            f"{len(frames)} frames, max cells {max(len(f['cells']) for f in frames)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
