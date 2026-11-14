"""Trino ingest tests. Offline tests use a frame with the documented
state_vectors_data4 column names; the `trino` test needs real credentials."""

import numpy as np
import pandas as pd
import pytest

from turbcast import config, schema
from turbcast.ingest import historical as h
from turbcast.schema import RECORD_SCHEMA

HOUR = 1_764_547_200  # 2025-12-01 00:00 UTC


@pytest.fixture
def region():
    return config.load().region


def raw_frame():
    n = 4
    return pd.DataFrame(
        {
            "time": [HOUR, HOUR + 1, HOUR + 2, HOUR + 2],  # duplicate key at +2
            "icao24": ["A1B2C3"] * n,
            "lat": [40.0] * n,
            "lon": [-100.0] * n,
            "velocity": [230.0] * n,
            "heading": [270.0] * n,
            "vertrate": [0.0, 0.32512, -0.32512, -0.32512],
            "callsign": ["UAL1    "] * n,
            "onground": [False] * n,
            "baroaltitude": [10668.0] * n,
            "geoaltitude": [10800.0] * n,
            "lastposupdate": [HOUR - 0.5, HOUR + 0.9, HOUR + 1.9, HOUR + 1.9],
            "lastcontact": [HOUR, HOUR + 1, HOUR + 1, HOUR + 1],
        }
    )


def test_query_has_partition_and_filters(region):
    q = h.build_query(HOUR, region)
    assert f"hour = {HOUR}" in q
    assert f"time < {HOUR + 3600}" in q
    assert "baroaltitude >= 5500.0" in q
    assert q.startswith("SELECT time, icao24")


def test_query_rejects_bad_inputs(region):
    with pytest.raises(ValueError):
        h.build_query(HOUR + 5, region)
    with pytest.raises(ValueError):
        h.build_query(HOUR, region, icao24="abc'; DROP TABLE x;--")
    assert "icao24 = 'a1b2c3'" in h.build_query(HOUR, region, icao24="a1b2c3")


def test_converter_units_and_staleness():
    rec = h.trino_to_record(raw_frame())
    schema.validate(rec, RECORD_SCHEMA)
    assert len(rec) == 3  # duplicate (icao24, time) dropped
    assert rec["icao24"].iloc[0] == "a1b2c3"
    assert rec["baro_alt_ft"].iloc[0] == pytest.approx(35000.0, abs=0.01)
    np.testing.assert_allclose(rec["vr_fpm"], [0, 64, -64], atol=1e-3)
    assert rec["gs_kt"].iloc[0] == pytest.approx(447.08, abs=0.01)
    assert list(rec["contact_age_s"]) == [0, 0, 1]  # third row is a stale repeat
    assert (rec["rate_hz"] == 1.0).all() and (rec["source"] == "opensky_trino").all()
    assert rec["callsign"].iloc[0] == "UAL1"


def test_fetch_hour_writes_and_resumes(tmp_path, region):
    class FakeTrino:  # stands in for the network client only; conversion is real
        calls = 0

        def query(self, sql, cached=True):
            FakeTrino.calls += 1
            assert f"hour = {HOUR}" in sql
            return raw_frame()

    t = FakeTrino()
    p1 = h.fetch_hour(t, HOUR, region, out_dir=str(tmp_path))
    p2 = h.fetch_hour(t, HOUR, region, out_dir=str(tmp_path))
    assert p1 == p2 and FakeTrino.calls == 1
    assert "date=2025-12-01" in str(p1) and "hour=00" in str(p1)
    back = pd.read_parquet(p1)
    assert len(back) == 3


@pytest.mark.trino
def test_trino_one_real_hour(tmp_path, region):
    pytest.importorskip("pyopensky")
    from pyopensky.trino import Trino

    path = h.fetch_hour(Trino(), HOUR, region, out_dir=str(tmp_path))
    rec = pd.read_parquet(path)
    schema.validate(rec, RECORD_SCHEMA)
    assert rec["icao24"].nunique() > 500
