"""Trino ingest tests. No network or login: a stand-in client implements only
`history()`, so any attempt to send raw SQL through `query()` fails the test.
Frames mimic pyopensky's output (pyarrow dtypes, tz-aware `time`)."""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from turbcast import config, schema
from turbcast.ingest import historical as h
from turbcast.schema import RECORD_SCHEMA

HOUR = datetime(2025, 12, 2, 18, tzinfo=UTC)
T0 = int(HOUR.timestamp())


@pytest.fixture
def region():
    return config.load().region


def pyopensky_frame(t0: int = T0) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "time": pd.to_datetime([t0, t0 + 1, t0 + 2, t0 + 2, t0 + 3], unit="s", utc=True),
            "icao24": ["A1B2C3"] * 5,
            "callsign": ["UAL1    "] * 4 + [None],
            "lat": [40.0] * 5,
            "lon": [-100.0] * 5,
            "velocity": [230.0] * 5,
            "heading": [270.0] * 5,
            "vertrate": [0.0, 0.32512, -0.32512, -0.32512, 0.0],
            "baroaltitude": [10668.0] * 5,
            "geoaltitude": [10800.0] * 5,
            "onground": [False, False, False, False, None],
            # last row is stale: heard 16 s before its state-vector time
            "lastcontact": [t0, t0 + 1, t0 + 1, t0 + 1, t0 + 3 - 16.0],
            "lastposupdate": [t0 - 0.5, t0 + 0.9, t0 + 1.9, t0 + 1.9, t0 - 13.0],
        }
    )
    return df.convert_dtypes(dtype_backend="pyarrow")


class HistoryOnly:
    """Records history() calls; has no query() so raw SQL can't slip through."""

    def __init__(self, frame_for=None, fail_times=0):
        self.calls = []
        self.frame_for = frame_for or (lambda start: pyopensky_frame(int(start.timestamp())))
        self.fail_times = fail_times

    def history(self, start, stop, *conditions, **kw):
        self.calls.append((start, stop, conditions, kw))
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("QUERY_QUEUE_FULL")
        return self.frame_for(start)


def test_converter_units_stale_and_dupes():
    rec = h.trino_to_record(pyopensky_frame())
    schema.validate(rec, RECORD_SCHEMA)
    assert len(rec) == 3  # one duplicate key + one stale row removed
    assert rec["icao24"].iloc[0] == "a1b2c3"
    assert list(rec["time"]) == [T0, T0 + 1, T0 + 2]
    assert rec["baro_alt_ft"].iloc[0] == pytest.approx(35000.0, abs=0.01)
    np.testing.assert_allclose(rec["vr_fpm"], [0, 64, -64], atol=1e-3)
    assert rec["gs_kt"].iloc[0] == pytest.approx(447.08, abs=0.01)
    assert list(rec["contact_age_s"]) == [0, 0, 1]
    assert rec["callsign"].iloc[0] == "UAL1"
    assert (rec["source"] == "opensky_trino").all() and (rec["rate_hz"] == 1.0).all()


def test_stale_threshold_is_inclusive_at_15s():
    df = pyopensky_frame()
    df["lastcontact"] = df["lastcontact"].astype("float64")
    df.loc[4, "lastcontact"] = T0 + 3 - 15.0
    assert len(h.trino_to_record(df)) == 4


def test_empty_and_none():
    assert h.trino_to_record(None).empty
    assert list(h.trino_to_record(pd.DataFrame()).columns) == schema.RECORD_COLUMNS


def test_fetch_window_uses_history_with_safe_arguments(region):
    t = HistoryOnly()
    h.fetch_window(t, HOUR, HOUR + pd.Timedelta(hours=1), region)
    (start, stop, conds, kw) = t.calls[0]
    assert start == pd.Timestamp(HOUR)
    assert stop == pd.Timestamp(HOUR) + pd.Timedelta(seconds=3599)  # inclusive stop, no overlap
    assert kw["bounds"] == (region.lon_min, region.lat_min, region.lon_max, region.lat_max)
    assert kw["selected_columns"] == h.COLUMNS and kw["cached"] is True
    assert len(conds) == 1 and "baroaltitude" in str(conds[0])
    assert not hasattr(t, "query")


@pytest.mark.parametrize(
    "start,stop",
    [
        (datetime(2022, 12, 31, 23, tzinfo=UTC), datetime(2023, 1, 1, 1, tzinfo=UTC)),
        (datetime(2025, 12, 2, 18), datetime(2025, 12, 2, 19)),  # naive
        (HOUR, HOUR),
    ],
)
def test_check_window_rejects(start, stop):
    with pytest.raises(ValueError):
        h.check_window(start, stop)


def test_hours_between():
    hrs = h.hours_between(HOUR, HOUR + pd.Timedelta(hours=2, minutes=30))
    assert [x.hour for x in hrs] == [18, 19, 20]


def test_run_hours_writes_resumes_and_cleans_cache(tmp_path, region):
    out, cache = tmp_path / "hist", tmp_path / "cache"
    cache.mkdir()
    (cache / "someone-elses.parquet").write_text("keep me")

    class CachingTrino(HistoryOnly):
        def history(self, start, stop, *c, **kw):
            (cache / f"{int(start.timestamp())}.parquet").write_text("raw")
            return super().history(start, stop, *c, **kw)

    t = CachingTrino()
    hours = h.hours_between(HOUR, HOUR + pd.Timedelta(hours=2))
    res = h.run_hours(t, hours, region, out_dir=str(out), cache_dir=cache, log=lambda *_: None)
    assert [r["status"] for r in res] == ["ok", "ok"] and res[0]["rows"] == 3
    assert sorted(p.name for p in cache.iterdir()) == ["someone-elses.parquet"]
    back = pd.read_parquet(h.hour_path(HOUR, str(out)))
    assert back["time"].min() == T0
    again = h.run_hours(t, hours, region, out_dir=str(out), cache_dir=cache, log=lambda *_: None)
    assert [r["status"] for r in again] == ["skipped", "skipped"] and len(t.calls) == 2


def test_run_hours_retries_with_backoff(tmp_path, region):
    waits = []
    t = HistoryOnly(fail_times=2)
    res = h.run_hours(
        t, [HOUR], region, out_dir=str(tmp_path), sleep=waits.append, log=lambda *_: None
    )
    assert res[0]["status"] == "ok" and waits == [30.0, 60.0]


def test_run_hours_gives_up_and_logs(tmp_path, region):
    t = HistoryOnly(fail_times=10)
    res = h.run_hours(
        t,
        [HOUR],
        region,
        out_dir=str(tmp_path),
        attempts=2,
        sleep=lambda s: None,
        log=lambda *_: None,
    )
    assert res[0]["status"] == "failed" and "QUERY_QUEUE_FULL" in res[0]["error"]
    assert not h.hour_path(HOUR, str(tmp_path)).exists()


def test_run_hours_disk_floor(tmp_path, region):
    res = h.run_hours(
        HistoryOnly(), [HOUR], region, out_dir=str(tmp_path), min_free_gb=1e9, log=lambda *_: None
    )
    assert res[0]["status"] == "disk_floor"


@pytest.mark.trino
def test_trino_smoke_one_real_hour(tmp_path, region):
    """Real query (needs OpenSky login): one hour, explicit columns, cached."""
    from pyopensky.trino import Trino

    res = h.run_hours(Trino(), [HOUR], region, out_dir=str(tmp_path), keep_cache=True)
    assert res[0]["status"] == "ok" and res[0]["aircraft"] > 500
