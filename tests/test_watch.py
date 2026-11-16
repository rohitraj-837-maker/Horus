"""Text stream (horus/watch.py): live run on the recorded replay, event rules, case story."""

import io
import json
import math
from pathlib import Path
from types import SimpleNamespace

import h3
import numpy as np
import pandas as pd

from horus.core import grid
from horus.places import Places
from horus.watch import Printer, Watcher, main, run_live
from tests.api.test_api import CFG, _hub

PLACES = Places()


def test_places_name_states() -> None:
    assert PLACES.state(-104.99, 39.74) == "CO"  # Denver
    assert PLACES.where(-140.0, 30.0) == "offshore"


def test_live_run_on_recorded_replay_matches_what_the_grid_used() -> None:
    hub = _hub()
    buf = io.StringIO()
    w = run_live(hub, Printer(buf, colour=False), CFG.watch, PLACES, math.inf, minutes=29)
    text = buf.getvalue()
    assert "HORUS" in text and "Not for flight decisions" in text and "\x1b" not in text
    assert "aircraft above FL200" in text and "Summary" in text
    assert text.count("TURBULENCE ") - 1 == w.n_hits > 0  # minus the legend line
    # the fixture PIREP (FL330-350, KS) is past the window start, so it is reported
    assert "PILOT REPORT  moderate at FL330-350 over KS" in text
    # every TURBULENCE line is an aircraft the grid took as evidence (settled T >= tau)
    stamps = [ln[:7].strip() for ln in text.splitlines() if ln[9:19] == "TURBULENCE"]
    assert stamps == sorted(stamps)


def _fake_hub(t0: int, p: np.ndarray, pireps: pd.DataFrame) -> SimpleNamespace:
    ids = grid.region_cells(24.0, 50.0, -125.0, -66.0, CFG.grid.h3_res)
    n_fl = (CFG.grid.fl_max - CFG.grid.fl_min) // CFG.grid.fl_bin
    params = grid.GridParams(l0=-5.0, half_life_s=np.full(n_fl, 1800.0), tau=1.0)
    loop = SimpleNamespace(
        params=params,
        lattice_ids=ids,
        tick=t0,
        fresh=pd.DataFrame(),
        types={"abc123": "B738"},
        probabilities=lambda t: p,
    )
    return SimpleNamespace(cfg=CFG, loop=loop, pireps=pireps, aircraft=lambda: [])


def _minute(t: int, T: float, lat: float = 39.0, lon: float = -100.0) -> pd.DataFrame:
    return pd.DataFrame(
        {"minute": [t], "icao24": ["abc123"], "lat": [lat], "lon": [lon], "alt_ft": [35000.0]}
        | {"T": [T]}
    )


def test_watcher_rules_runs_calm_forecast_and_report_check() -> None:
    t0 = 1_767_200_000 - 1_767_200_000 % 60
    ids = grid.region_cells(24.0, 50.0, -125.0, -66.0, CFG.grid.h3_res)
    normal = float(grid.sigmoid(np.array(-5.0)))
    p = np.full((len(ids), 13), normal)
    c = h3.latlng_to_cell(39.0, -100.0, CFG.grid.h3_res)
    j = int(np.flatnonzero(ids == h3.str_to_int(c))[0])
    k = int(np.flatnonzero(ids == h3.str_to_int(h3.grid_disk(c, 1)[1]))[0])
    p[j, 7] = p[k, 7] = 10 * normal  # a lit cell and its neighbour: one FORECAST line
    pir = pd.DataFrame(
        {
            "time": [t0 + 150, t0 + 150],
            "lat": [39.1, 30.0],
            "lon": [-100.1, -90.0],
            "fl_lo": [340.0, 120.0],  # the second is below FL200: never shown
            "fl_hi": [360.0, 140.0],
            "tb_max": [6, 4],
        }
    )
    hub = _fake_hub(t0, p, pir)
    w = Watcher(hub, PLACES, CFG.watch)
    lines = []

    def step(tick: int, fresh: pd.DataFrame) -> None:
        hub.loop.tick, hub.loop.fresh = tick, fresh
        lines.extend(w.update())

    step(t0 + 60, _minute(t0, 2.0))
    step(t0 + 120, _minute(t0 + 60, 3.0))  # same run: no second TURBULENCE line
    step(t0 + 180, pd.DataFrame())  # report at t0+150 is checked
    step(t0 + 120 + CFG.watch.calm_after_s + 60, pd.DataFrame())  # run ends
    kinds = [ln.label for ln in lines]
    assert kinds.count("TURBULENCE") == 1 and kinds.count("FORECAST") == 1
    hit = next(ln for ln in lines if ln.label == "TURBULENCE")
    assert hit.t == t0 + 60 and "abc123  B738  FL350   2.0× threshold  over KS" in hit.text
    calm = next(ln for ln in lines if ln.label == "calm")
    assert "bumpy for 2 min" in calm.text and "peak 3.0×" in calm.text
    rep = [ln for ln in lines if ln.label == "PILOT REPORT"]
    assert len(rep) == 1 and rep[0].kind == "pirep_sev"
    assert "severe at FL340-360 over KS" in rep[0].text
    assert "1 aircraft passed within 40 km just before, 1 felt it" in rep[0].text
    assert "forecast 10× normal" in rep[0].text
    assert (w.n_hits, w.n_reports, w.n_covered, w.n_backed) == (1, 1, 1, 1)


def test_case_story(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    t0 = 1_768_000_000 - 1_768_000_000 % 60
    cell = h3.latlng_to_cell(39.1, -100.0, CFG.grid.h3_res)
    frames = [
        {
            "t": t0 + 60 * i,
            "aircraft": [
                ["abc123", 39.0, -100.0 + 0.01 * i, 350, 1e3 if i > 3 else 0.0],
                ["far001", 30.0, -80.0, 350, 1e3],  # >150 km away: left out of the story
            ],
            "cells": [[cell, 0.02]] if i > 5 else [],
        }
        for i in range(12)
    ]
    meta = {
        "time": t0 + 360,
        "lat": 39.0,
        "lon": -100.0,
        "fl_lo": 350,
        "fl_hi": 350,
        "intensity": "MOD",
        "floor": 0.002,
    }
    d = tmp_path / "cases"
    d.mkdir(parents=True)
    (d / "r1-x.json").write_text(json.dumps({"meta": meta, "frames": frames}))
    import horus.config as hc

    real = hc.resolve
    monkeypatch.setattr(
        hc, "resolve", lambda p: tmp_path / p if str(p) == "cases" else real(p)
    )
    assert main(["--case", "1", "--fast", "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "CASE 1 of 1" in out and "moderate turbulence reported at FL350 over KS" in out
    assert "TURBULENCE    abc123" in out and "far001" not in out
    assert "PILOT REPORT" in out and "1 aircraft felt it within 40 km" in out
    assert "FORECAST      cell 23 km from the report at 20× normal" in out
    assert "1 of them within 40 km" in out
