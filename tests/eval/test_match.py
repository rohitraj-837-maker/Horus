"""PIREP window matching (eval/match.py): distance, altitude and time edges."""

import numpy as np
import pandas as pd
import pytest

from horus import config
from horus.core.geo import haversine_km
from horus.eval import match

CFG = config.load()
SPEC = match.WindowSpec(window_s=600, dist_km=40.0, alt_ft=2000.0)
T0 = 1_764_633_600  # 2025-12-02 00:00 UTC


def _north(lat: float, km: float) -> float:
    return lat + np.degrees(km / match.EARTH_RADIUS_KM)


def _obs(rows: list[tuple[str, int, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["icao24", "minute", "lat", "lon", "alt_ft", "b2"])


def _window(
    time: float, lat: float = 40.0, lon: float = -100.0, lo: float = 34000, hi: float = 36000
) -> pd.DataFrame:
    return pd.DataFrame(
        {"time": [time], "lat": [lat], "lon": [lon], "alt_lo_ft": [lo], "alt_hi_ft": [hi]}
    )


def test_39_km_matches_and_41_km_does_not() -> None:
    lat39, lat41 = _north(40.0, 39.0), _north(40.0, 41.0)
    assert haversine_km(40, -100, lat39, -100) == pytest.approx(39.0, abs=1e-6)
    obs = _obs([("a", T0, lat39, -100.0, 35000, 5.0), ("b", T0, lat41, -100.0, 35000, 9.0)])
    r = match.window_scores(_window(T0 + 30), obs, SPEC, ["b2"])
    assert r["n_passes"].iloc[0] == 1
    assert r["b2"].iloc[0] == 5.0


@pytest.mark.parametrize(
    ("alt", "inside"),
    [(32000, True), (31999, False), (38000, True), (38001, False), (35000, True)],
)
def test_altitude_range_overlap_edges(alt: float, inside: bool) -> None:
    obs = _obs([("a", T0, 40.0, -100.0, alt, 7.0)])
    r = match.window_scores(_window(T0 + 30, lo=34000, hi=36000), obs, SPEC, ["b2"])
    assert bool(r["n_passes"].iloc[0] == 1) == inside


@pytest.mark.parametrize(
    ("minute", "inside"),
    [
        (T0 - 660, False),  # minute [T0-660, T0-600) ends exactly at t - 600: no overlap
        (T0 - 659, True),
        (T0 + 600, True),  # minute starts exactly at t + 600
        (T0 + 601, False),
    ],
)
def test_time_overlap_edges(minute: int, inside: bool) -> None:
    obs = _obs([("a", minute, 40.0, -100.0, 35000, 7.0)])
    r = match.window_scores(_window(T0), obs, SPEC, ["b2"])
    assert bool(r["n_passes"].iloc[0] == 1) == inside


def test_max_over_rows_counts_distinct_aircraft_and_nan_is_zero() -> None:
    obs = _obs(
        [
            ("a", T0, 40.0, -100.0, 35000, 3.0),
            ("a", T0 + 60, 40.01, -100.0, 35000, 8.0),
            ("b", T0, 40.02, -100.0, 35000, np.nan),
        ]
    )
    r = match.window_scores(_window(T0 + 30), obs, SPEC, ["b2"])
    assert r["n_passes"].iloc[0] == 2
    assert r["b2"].iloc[0] == 8.0
    empty = match.window_scores(_window(T0 + 30, lat=10.0), obs, SPEC, ["b2"])
    assert empty["n_passes"].iloc[0] == 0 and empty["b2"].iloc[0] == 0.0


def test_near_any_event_matches_brute_force() -> None:
    rng = np.random.default_rng(3)
    n, k = 4000, 60
    minute = T0 + 60 * rng.integers(0, 600, n)
    lat, lon = rng.uniform(30, 45, n), rng.uniform(-110, -85, n)
    et = T0 + rng.uniform(-3000, 40000, k)
    elat, elon = rng.uniform(30, 45, k), rng.uniform(-110, -85, k)
    got = match.near_any_event(minute, lat, lon, et, elat, elon, 7200, 150.0)
    d = haversine_km(lat[:, None], lon[:, None], elat[None, :], elon[None, :])
    tm = (et[None, :] > minute[:, None] - 60 - 7200) & (et[None, :] <= minute[:, None] + 7200)
    want = ((d <= 150.0) & tm).any(axis=1)
    assert np.array_equal(got, want)
    assert 0.05 < want.mean() < 0.95  # the test exercises both outcomes


def test_pirep_windows_filters_level_region_and_flight_level() -> None:
    p = pd.DataFrame(
        {
            "time": [T0] * 5,
            "lat": [40, 40, 60, 40, 40],
            "lon": [-100, -100, -100, -100, -100],
            "fl_lo": [330, 330, 330, 150, 180],
            "fl_hi": [350, 350, 350, 190, 200],
            "tb_max": [4, 2, 4, 4, 6],
            "tb_min": [4, 2, 4, 4, 6],
        }
    )
    w = match.pirep_windows(p, CFG, 4)
    assert len(w) == 2  # LGT, outside CONUS, below FL200 dropped
    assert w["alt_lo_ft"].tolist() == [33000.0, 18000.0]
