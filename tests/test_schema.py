import numpy as np
import pandas as pd
import pytest

from horus import schema
from horus.schema import SchemaError


def make_frame(n: int = 3) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "icao24": ["a1b2c3"] * n,
            "time": np.arange(1_767_225_600, 1_767_225_600 + n, dtype=np.int64),
            "lat": np.full(n, 40.0),
            "lon": np.full(n, -100.0),
            "baro_alt_ft": np.full(n, 35000.0, dtype=np.float32),
            "vr_fpm": np.array([0, 64, -64][:n] + [0] * max(0, n - 3), dtype=np.float32),
            "gs_kt": np.full(n, 450.0, dtype=np.float32),
            "track_deg": np.full(n, 270.0, dtype=np.float32),
            "on_ground": np.zeros(n, dtype=bool),
            "source": ["opensky_live"] * n,
            "rate_hz": np.full(n, 0.1, dtype=np.float32),
        }
    )


def test_unit_constants_are_exact():
    # The ADS-B vertical-rate step: 64 ft/min == 0.32512 m/s exactly.
    assert 64 / schema.FPM_PER_MPS == pytest.approx(0.32512, abs=1e-12)
    assert schema.FT_PER_M * 0.3048 == pytest.approx(1.0, abs=1e-15)
    assert 1852 / 3600 * schema.KT_PER_MPS == pytest.approx(1.0, abs=1e-15)
    # 10,668 m == FL350
    assert 10668 * schema.FT_PER_M == pytest.approx(35000.0, abs=1e-6)


def test_valid_frame_passes():
    df = make_frame()
    assert schema.problems(df) == []
    assert schema.validate(df) is df


def test_empty_frame_is_valid():
    df = schema.empty()
    assert list(df.columns) == schema.COLUMNS
    assert schema.problems(df) == []


def test_missing_column():
    with pytest.raises(SchemaError, match="missing columns"):
        schema.validate(make_frame().drop(columns=["vr_fpm"]))


def test_extra_column():
    df = make_frame().assign(squawk="7700")
    with pytest.raises(SchemaError, match="unexpected columns"):
        schema.validate(df)


def test_float64_altitude_rejected():
    df = make_frame()
    df["baro_alt_ft"] = df["baro_alt_ft"].astype(np.float64)
    with pytest.raises(SchemaError, match="baro_alt_ft: dtype float64"):
        schema.validate(df)


def test_millisecond_time_caught():
    # Note: vr left in m/s would pass range checks; the empirical 64 fpm step
    # check in T1.5 (measure_quantisation) is what catches that unit error.
    df = make_frame()
    df["time"] = df["time"] * 1000
    with pytest.raises(SchemaError, match="milliseconds"):
        schema.validate(df)


def test_out_of_range_vertical_rate_caught():
    df = make_frame()
    df.loc[0, "vr_fpm"] = np.float32(64 * 196.85 * 10)  # fpm converted twice
    with pytest.raises(SchemaError, match="vr_fpm"):
        schema.validate(df)


def test_null_icao_rejected():
    df = make_frame()
    df.loc[1, "icao24"] = None
    with pytest.raises(SchemaError, match="icao24: contains nulls"):
        schema.validate(df)


def test_nan_positions_allowed():
    df = make_frame()
    df.loc[1, "lat"] = np.nan
    df.loc[1, "vr_fpm"] = np.nan
    assert schema.problems(df) == []


def test_all_problems_reported_together():
    df = make_frame()
    df["gs_kt"] = df["gs_kt"].astype(np.float64)
    df.loc[0, "lat"] = 123.0
    found = schema.problems(df)
    assert len(found) == 2
