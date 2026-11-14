import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from turbcast import reports
from turbcast.ingest import live, quantisation, store


@pytest.fixture
def archive(tmp_path, fixtures_dir):
    """A two-poll archive built from the recorded OpenSky response."""
    payload = json.loads((fixtures_dir / "opensky_states_sample.json").read_text())
    rec = live.Recorder(tmp_path, "opensky")
    a = live.opensky_to_record(payload, rate_hz=1 / 90)
    later = {
        **payload,
        "time": payload["time"] + 90,
        "states": [[*s[:4], s[4] + 90, *s[5:]] for s in payload["states"]],
    }
    b = live.opensky_to_record(later, rate_hz=1 / 90)
    rec.add(a)
    rec.add(b)
    rec.flush()
    return tmp_path / "source=opensky", a, b


def test_load_track_returns_series(archive):
    root, a, b = archive
    icao = a["icao24"].iloc[0]
    tr = store.load_track(icao, 0, 2**40, root=root)
    assert list(tr["time"]) == sorted(
        set(a.loc[a.icao24 == icao, "time"]) | set(b.loc[b.icao24 == icao, "time"])
    )
    assert tr["time"].is_monotonic_increasing


def test_load_day_and_iter_tracks(archive):
    root, a, _ = archive
    day = pd.Timestamp(int(a["time"].min()), unit="s", tz="UTC").date()
    df = store.load_day(day, root=root, columns=["icao24", "time", "vr_fpm"])
    assert list(df.columns) == ["icao24", "time", "vr_fpm"]
    tracks = dict(store.iter_tracks(day, root=root, min_alt_ft=20000))
    assert tracks and all((g["baro_alt_ft"] >= 20000).all() for g in tracks.values())
    assert store.load_day(date(2000, 1, 1), root=root).empty


def test_resample_no_interpolation():
    tr = pd.DataFrame(
        {
            "time": [100, 101, 103, 103, 110],
            "lat": [1.0, 2.0, 3.0, 3.5, 4.0],
            "lon": 0.0,
            "baro_alt_ft": 35000.0,
            "vr_fpm": [0, 64, -64, 128, 0],
            "gs_kt": 450.0,
            "track_deg": 90.0,
            "on_ground": False,
        }
    )
    r = store.resample(tr, 1)
    assert list(r["time"]) == list(range(100, 111))
    assert r["observed"].sum() == 4
    assert r.loc[r.time == 103, "vr_fpm"].item() == 128  # last obs in bin
    assert np.isnan(r.loc[r.time == 102, "vr_fpm"].item())  # gap stays NaN
    r5 = store.resample(tr, 5)
    assert list(r5["time"]) == [100, 105, 110]
    assert r5["vr_fpm"].dtype == np.float32


def test_estimate_step_on_quantised_values():
    rng = np.random.default_rng(0)
    vr = 64.0 * rng.integers(-30, 30, 5000)
    assert quantisation.estimate_step(vr) == 64
    assert quantisation.estimate_step(vr + rng.normal(0, 0.5, vr.size)) == 64  # m/s rounding
    assert quantisation.estimate_step(rng.uniform(-2000, 2000, 5000)) < 16


def test_quantisation_summary_on_real_fixture(fixtures_dir):
    payload = json.loads((fixtures_dir / "opensky_states_sample.json").read_text())
    rec = live.opensky_to_record(payload, rate_hz=1 / 90)
    t = quantisation.summarise(rec)
    adsb = t[t["msg_type"] == "adsb"].iloc[0]
    assert adsb["on_64_grid"] == 1.0 and adsb["est_step_fpm"] == 64


def test_replace_section(tmp_path):
    p = reports.replace_section("x.md", "a", "one", root=tmp_path)
    p.write_text("# Notes\nhand text\n\n" + p.read_text())
    reports.replace_section("x.md", "a", "two", root=tmp_path)
    reports.replace_section("x.md", "b", "bee", root=tmp_path)
    text = p.read_text()
    assert "hand text" in text and "two" in text and "one" not in text and "bee" in text
