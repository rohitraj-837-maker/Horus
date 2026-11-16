"""Winds: recorded Open-Meteo response -> u/v field -> lookup (no network)."""

import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from horus.core.winds import WindField, uv_from_speed_dir, wind_at
from horus.ingest import winds

LEVELS = [400, 300, 250, 200]


def test_direction_convention_wind_from_west_blows_east() -> None:
    u, v = uv_from_speed_dir(np.array([10.0, 10.0]), np.array([270.0, 0.0]))
    assert u[0] == pytest.approx(10.0) and v[0] == pytest.approx(0.0, abs=1e-9)
    assert u[1] == pytest.approx(0.0, abs=1e-9) and v[1] == pytest.approx(-10.0)


def test_parse_recorded_response(fixtures_dir: Path) -> None:
    items = json.loads((fixtures_dir / "openmeteo_winds_sample.json").read_text())
    t, u, v = winds.parse(items, LEVELS)
    assert u.shape == (24, 4, 2) and np.isfinite(u).all()
    assert t[1] - t[0] == 3600.0
    h = items[0]["hourly"]
    uu, vv = uv_from_speed_dir(
        np.array(h["wind_speed_250hPa"]), np.array(h["wind_direction_250hPa"])
    )
    np.testing.assert_allclose(u[:, 2, 0], uu, rtol=1e-6)
    assert np.hypot(u, v).max() < 120.0  # m/s: physical jet-stream range


def test_download_via_mock_transport(fixtures_dir: Path) -> None:
    body = (fixtures_dir / "openmeteo_winds_sample.json").read_bytes()
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, content=body)

    from horus import config

    cfg = config.load()
    lat = np.array([40.0])
    lon = np.array([-100.0, -98.0])
    items = winds.fetch(
        httpx.Client(transport=httpx.MockTransport(handler)),
        [40, 40],
        [-100, -98],
        __import__("datetime").date(2025, 12, 2),
        __import__("datetime").date(2025, 12, 2),
        LEVELS,
    )
    assert "wind_speed_400hPa" in seen[0].url.params["hourly"]
    t, u, v = winds.parse(items, LEVELS)
    w = WindField(
        t, np.array(LEVELS, float), lat, lon, u.reshape(24, 4, 1, 2), v.reshape(24, 4, 1, 2)
    )
    assert cfg.winds.levels_hpa == LEVELS
    # exactly at a lattice node, level and hour: the stored value comes back
    from horus.core.geo import altitude_ft

    alt250 = float(altitude_ft(np.array([250.0]))[0])
    uu, _ = wind_at(w, t[3:4], np.array([40.0]), np.array([-98.0]), np.array([alt250]))
    assert uu[0] == pytest.approx(float(u[3, 2, 1]), rel=1e-4)
    # halfway in time and between the two nodes: the average of the four values
    um, _ = wind_at(
        w, np.array([(t[3] + t[4]) / 2]), np.array([40.0]), np.array([-99.0]), np.array([alt250])
    )
    want = np.mean([u[3, 2, 0], u[3, 2, 1], u[4, 2, 0], u[4, 2, 1]])
    assert um[0] == pytest.approx(float(want), rel=1e-4)


def test_find_picks_the_widest_file_covering_the_day(tmp_path, monkeypatch) -> None:
    from datetime import date

    from horus import config as hc
    from horus.ingest import winds as wio

    d = tmp_path / "winds"
    d.mkdir()
    for name in ("2026-01-01_2026-01-01", "2025-12-02_2026-01-15", "2026-02-01_2026-02-03"):
        (d / f"{name}.npz").write_bytes(b"")
    (d / "notes_x.npz").write_bytes(b"")
    real = hc.resolve
    monkeypatch.setattr(hc, "resolve", lambda p: d if p == wio.OUT_DIR else real(p))
    assert wio.find(date(2026, 1, 1)).name == "2025-12-02_2026-01-15.npz"
    assert wio.find(date(2026, 2, 3)).name == "2026-02-01_2026-02-03.npz"
    assert wio.find(date(2026, 3, 1)) is None
