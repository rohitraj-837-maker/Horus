"""Terminal UI: the braille map renderer and the Textual app on the recorded replay."""

import asyncio
import json
from pathlib import Path

import numpy as np

from horus.tui import mapview as mv
from tests.api.test_api import _hub


def test_view_fit_keeps_the_box_and_square_dots() -> None:
    v = mv.View.fit(120, 40)
    assert v.lon0 <= -125 and v.lon1 >= -66 and v.lat0 <= 24 and v.lat1 >= 50
    deg_per_dot_x = (v.lon1 - v.lon0) * mv.COSLAT / 240
    deg_per_dot_y = (v.lat1 - v.lat0) / 160
    assert abs(deg_per_dot_x - deg_per_dot_y) < 1e-9


def test_render_layers_dots_cells_and_reports() -> None:
    cols, rows = 40, 12
    v = mv.View(-110.0, -90.0, 30.0, 40.0)
    turb = np.full((rows, cols), np.nan)
    turb[5, 10] = 0.05
    L = mv.Layers(
        wind_speed=np.full((rows, cols), 50.0),
        turb_p=turb,
        floor=0.002,
        outline=[np.array([[-105.0, 32.0], [-95.0, 32.0], [-95.0, 38.0], [-105.0, 32.0]])],
        aircraft=np.array([[-100.0, 35.0], [-92.0, 31.0]]),
        detecting=np.array([True, False]),
        pireps=[(-108.0, 39.0, True)],
    )
    lines = mv.render(v, cols, rows, L)
    text = "".join(s for row in lines for s, _ in row)
    styles = " ".join(st for row in lines for _, st in row)
    assert len(lines) == rows
    assert sum(len(s) for s, _ in lines[0]) == cols
    assert "^" in text and "#ff5040" in styles  # severe report glyph
    assert "⣿" in text and "#ffb02e" in styles  # detecting aircraft fills its cell, amber
    assert any(0x2801 <= ord(ch) <= 0x28FF for ch in text)  # braille outline / aircraft dots
    assert "on #" in styles  # wind / forecast background


def test_app_runs_live_and_case_modes(tmp_path: Path) -> None:
    from horus.tui.app import HelpScreen, HorusTUI

    hub = _hub()
    t0 = int(hub.feed.t_first)
    frames = [
        {
            "t": t0 + 60 * i,
            "aircraft": [["abc123", 39.0, -100.0 + 0.1 * i, 350, 2.0 if i > 3 else 0.2]],
            "cells": [["8426cd7ffffffff", 0.04]] if i > 5 else [],
        }
        for i in range(12)
    ]
    meta = {
        "id": "r1-x",
        "date": "2026-01-01",
        "time": t0 + 360,
        "lat": 39.0,
        "lon": -100.0,
        "fl_lo": 350,
        "fl_hi": 350,
        "intensity": "MOD",
        "aircraft_in_window": 5,
        "b3_window_score": 3.0,
        "selection": "test",
        "floor": 0.002,
    }
    (tmp_path / "r1-x.json").write_text(json.dumps({"meta": meta, "frames": frames}))

    async def run() -> None:
        app = HorusTUI(hub=hub, replays_dir=str(tmp_path))
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(2.5)
            top = str(app.query_one("#top").render())
            assert "HORUS" in top and "FL340" in top
            assert any(0x2801 <= ord(c) <= 0x28FF for c in str(app.query_one("#map").render()))
            await pilot.press("up")
            await pilot.pause(0.6)
            assert "FL360" in str(app.query_one("#top").render())
            await pilot.press("c")
            await pilot.pause(2.0)
            assert app.mode == "case" and "CASE 1/1" in str(app.query_one("#top").render())
            assert "report" in str(app.query_one("#bar").render())
            await pilot.press("question_mark")
            await pilot.pause(0.3)
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("x")
            await pilot.press("l")
            await pilot.pause(0.6)
            assert app.mode == "live"

    asyncio.run(run())
