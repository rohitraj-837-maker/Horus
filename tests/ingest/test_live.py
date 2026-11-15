"""Live ingest tests. All HTTP is served from recorded real responses via
httpx.MockTransport, so no API credits are consumed."""

import asyncio
import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest

from horus import config, schema
from horus.ingest import audit, live
from horus.schema import FPM_PER_MPS, FT_PER_M, KT_PER_MPS, RECORD_SCHEMA


@pytest.fixture
def opensky_payload(fixtures_dir):
    return json.loads((fixtures_dir / "opensky_states_sample.json").read_text())


@pytest.fixture
def adsblol_payload(fixtures_dir):
    return json.loads((fixtures_dir / "adsblol_point_sample.json").read_text())


@pytest.fixture
def cfg(tmp_path):
    c = config.load()
    return dataclasses.replace(c, live=dataclasses.replace(c.live, out_dir=str(tmp_path)))


class FakeClock:
    def __init__(self, t0: float = 1_794_700_000.0) -> None:
        self.t = t0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, dt: float) -> None:
        self.t += max(dt, 0.0)


# ---------------------------------------------------------------- OpenSky converter


def test_opensky_units_exact(opensky_payload):
    rec = live.opensky_to_record(opensky_payload, rate_hz=1 / 90)
    schema.validate(rec, RECORD_SCHEMA)
    raw = {s[0]: s for s in opensky_payload["states"]}
    for _, row in rec.iterrows():
        s = raw[row.icao24]
        if s[7] is not None:
            assert row.baro_alt_ft == pytest.approx(s[7] * FT_PER_M, rel=1e-6)
        if s[11] is not None:
            assert row.vr_fpm == pytest.approx(s[11] * FPM_PER_MPS, rel=1e-6, abs=1e-3)
        if s[9] is not None:
            assert row.gs_kt == pytest.approx(s[9] * KT_PER_MPS, rel=1e-6)
        assert row.time == s[4]  # last_contact
        assert row.snapshot_time == opensky_payload["time"]
        assert row.contact_age_s == opensky_payload["time"] - s[4]


def test_opensky_keeps_nulls_and_ground(opensky_payload):
    rec = live.opensky_to_record(opensky_payload, rate_hz=1 / 90)
    n_null_lat = sum(s[6] is None for s in opensky_payload["states"])
    n_ground = sum(bool(s[8]) for s in opensky_payload["states"])
    assert rec["lat"].isna().sum() == n_null_lat
    assert rec["on_ground"].sum() == n_ground
    assert (rec["icao24"] == rec["icao24"].str.lower()).all()
    assert rec["typecode"].isna().all()


def test_opensky_vertical_rate_is_64fpm_quantised(opensky_payload):
    """Real data: OpenSky m/s values (2 dp) map back onto the 64 ft/min ADS-B grid."""
    rec = live.opensky_to_record(opensky_payload, rate_hz=1 / 90)
    vr = rec.loc[rec["msg_type"] == "adsb", "vr_fpm"].dropna().to_numpy(dtype=float)
    assert len(vr) >= 20
    off_grid = np.abs(vr - 64 * np.round(vr / 64))
    assert off_grid.max() < 2.0  # 0.005 m/s rounding = 0.98 ft/min


def test_opensky_empty_states():
    rec = live.opensky_to_record({"time": 1_794_700_000, "states": None}, rate_hz=1.0)
    assert rec.empty and list(rec.columns) == schema.RECORD_COLUMNS


# ---------------------------------------------------------------- adsb.lol converter


def test_adsblol_fields(adsblol_payload):
    rec = live.adsblol_to_record(adsblol_payload, rate_hz=1 / 60)
    schema.validate(rec, RECORD_SCHEMA)
    ac = {a["hex"]: a for a in adsblol_payload["ac"]}
    now_s = adsblol_payload["now"] / 1000
    for _, row in rec.iterrows():
        a = ac[row.icao24]
        assert row.time == round(now_s - a["seen"])
        if a.get("alt_baro") == "ground":
            assert row.on_ground and np.isnan(row.baro_alt_ft)
        else:
            assert row.baro_alt_ft == a["alt_baro"]
        if "baro_rate" in a:
            assert row.vr_fpm == a["baro_rate"]
        else:
            assert np.isnan(row.vr_fpm)
        if a.get("t"):
            assert row.typecode == a["t"]
        else:
            assert pd.isna(row.typecode)
        assert row.msg_type == a["type"]


def test_adsblol_drops_non_icao(adsblol_payload):
    payload = dict(adsblol_payload)
    payload["ac"] = [*adsblol_payload["ac"], {**adsblol_payload["ac"][0], "hex": "~2a0b11"}]
    rec = live.adsblol_to_record(payload, rate_hz=1.0)
    assert not rec["icao24"].str.startswith("~").any()


def test_dedupe_keeps_freshest():
    base = live.opensky_to_record(
        {
            "time": 1_794_700_100,
            "states": [
                [
                    "abc123",
                    "X ",
                    "US",
                    1_794_700_090,
                    1_794_700_095,
                    -100.0,
                    40.0,
                    10000.0,
                    False,
                    230.0,
                    90.0,
                    0.0,
                    None,
                    10100.0,
                    "1200",
                    False,
                    0,
                ]
            ],
        },
        rate_hz=1.0,
    )
    later = base.assign(snapshot_time=1_794_700_190, contact_age_s=np.float32(95.0))
    out = live.dedupe(pd.concat([later, base]))
    assert len(out) == 1 and out["contact_age_s"].iloc[0] == 5.0


# ---------------------------------------------------------------- tiling


def test_tiles_cover_conus():
    region = config.load().region
    tiles = np.array(live.cover_tiles(region, 250))
    lat, lon = np.meshgrid(
        np.linspace(region.lat_min, region.lat_max, 60),
        np.linspace(region.lon_min, region.lon_max, 120),
    )
    d = live.haversine_km(lat.ravel()[:, None], lon.ravel()[:, None], tiles[:, 0], tiles[:, 1])
    assert d.min(axis=1).max() <= 250 * live.KM_PER_NM
    assert len(tiles) < 60  # sanity: not wildly over-tiled


# ---------------------------------------------------------------- credentials + pacing


def test_load_credentials_variants(tmp_path):
    p = tmp_path / "c.json"
    assert live.load_credentials(p) is None
    p.write_text(json.dumps({"clientId": "id-1", "clientSecret": "s-1"}))
    c = live.load_credentials(p)
    assert c is not None and c.client_id == "id-1"
    assert "s-1" not in repr(c)
    p.write_text(json.dumps({"client_id": "id-2", "client_secret": "s-2"}))
    assert live.load_credentials(p).client_id == "id-2"
    p.write_text(json.dumps({"user": "u", "password": "topsecret"}))
    with pytest.raises(ValueError) as e:
        live.load_credentials(p)
    assert "topsecret" not in str(e.value) and "password" in str(e.value)


@pytest.mark.parametrize(
    "remaining,secs_left,expected",
    [
        (None, 50_000, 90.0),
        (4000, 86_400, 90.0),  # 1000 calls/day -> 86.4 s, floored at 90
        (40, 3600, 360.0),  # 10 calls left for an hour
        (3, 3600, 3605.0),  # can't afford one call: wait for reset
    ],
)
def test_opensky_interval(remaining, secs_left, expected):
    now = 1_794_700_800 + (86_400 - secs_left)  # 1_794_700_800 is a UTC midnight
    assert 1_794_700_800 % 86_400 == 0
    got = live.opensky_interval(remaining, now, floor_s=90, cost=4)
    assert got == pytest.approx(expected)


def test_token_refresh(opensky_payload):
    clock = FakeClock()
    token_calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        token_calls.append(req.url)
        return httpx.Response(
            200, json={"access_token": f"tok{len(token_calls)}", "expires_in": 1800}
        )

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            tm = live.TokenManager(live.Credentials("id", "sec"), now=clock)
            h1 = await tm.headers(c)
            clock.t += 600
            h2 = await tm.headers(c)
            clock.t += 1200  # past expiry - 60 s
            h3 = await tm.headers(c)
            return h1, h2, h3

    h1, h2, h3 = asyncio.run(go())
    assert h1 == h2 == {"Authorization": "Bearer tok1"}
    assert h3 == {"Authorization": "Bearer tok2"}
    assert len(token_calls) == 2


# ---------------------------------------------------------------- recorder loop


def test_run_backs_off_then_records(cfg, opensky_payload, tmp_path):
    clock = FakeClock()
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"x-rate-limit-retry-after-seconds": "30"})
        if calls["n"] == 2:
            return httpx.Response(503)
        payload = {**opensky_payload, "time": int(clock())}
        return httpx.Response(200, json=payload, headers={"x-rate-limit-remaining": "3996"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            src = live.OpenSkySource(cfg, live.TokenManager(None), now=clock)
            return await live.run(cfg, src, duration_s=600, client=c, now=clock, sleep=clock.sleep)

    asyncio.run(go())
    polls = audit.read_poll_log(tmp_path, "opensky")
    assert list(polls["ok"][:3]) == [0, 0, 1]
    assert polls["interval_s"].iloc[0] == 30.0  # honoured Retry-After
    assert polls["interval_s"].iloc[1] == 10.0  # exponential backoff 5 -> 10
    assert polls["credits_remaining"].iloc[2] == 3996

    import duckdb

    glob = f"{tmp_path}/source=opensky/**/*.parquet"
    got = duckdb.sql(f"select * from read_parquet('{glob}', hive_partitioning=true)").df()
    n_ok = int(polls["ok"].sum())
    assert len(got) == n_ok * len(live.dedupe(live.opensky_to_record(opensky_payload, 1.0)))
    assert set(got["date"].astype(str)) == {"2026-11-14"}


def test_recorder_flush_roundtrip(tmp_path, opensky_payload):
    rec = live.Recorder(tmp_path, "opensky")
    df = live.opensky_to_record(opensky_payload, rate_hz=1 / 90)
    rec.add(df)
    paths = rec.flush()
    assert len(paths) == 1 and "hour=" in str(paths[0]) and not rec.buffer
    back = pd.read_parquet(paths[0])
    pd.testing.assert_frame_equal(back, df.reset_index(drop=True))


def test_adsblol_source_dedupes_tiles(cfg, adsblol_payload):
    small = dataclasses.replace(
        cfg,
        region=dataclasses.replace(cfg.region, lat_min=38, lat_max=40, lon_min=-100, lon_max=-97),
    )
    clock = FakeClock()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=adsblol_payload)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            src = live.AdsbLolSource(small, sleep=clock.sleep)
            return await src.fetch(c), len(src.tiles)

    (df, meta), n_tiles = asyncio.run(go())
    assert n_tiles >= 2  # every tile returns the same aircraft
    assert df["icao24"].is_unique
    assert meta["tiles_failed"] == 0


# ---------------------------------------------------------------- audit


def _polls(times, intervals, ok=None):
    return pd.DataFrame({"poll_time": times, "ok": ok or [1] * len(times), "interval_s": intervals})


def test_audit_passes_regular_polls():
    t = np.arange(0, 24 * 3600 + 1, 90.0)
    res = audit.audit(_polls(t, [90.0] * len(t)), hours=24, end=t[-1])
    assert res.passed, res.reasons


def test_audit_flags_unplanned_gap():
    t = list(np.arange(0, 24 * 3600 + 1, 90.0))
    t = [x for x in t if not (36000 < x < 40000)]
    res = audit.audit(_polls(t, [90.0] * len(t)), hours=24, end=t[-1])
    assert not res.passed and res.bad_gaps == 1


def test_audit_accepts_planned_backoff():
    t = [0.0, 90.0, 690.0] + list(np.arange(780.0, 24 * 3600 + 691, 90.0))
    iv = [90.0, 600.0] + [90.0] * (len(t) - 2)
    res = audit.audit(_polls(t, iv), hours=24, end=t[-1])
    assert res.passed, res.reasons


# ------------------------------------------------- host sleep (real pmset excerpt)

# Real recorder gaps on 2026-11-15 (lid closed on battery, 03:46-06:47 UTC)
FIXTURES = Path(__file__).parents[1] / "fixtures"
GAP_POLLS = [1794714353.0, 1794720865.0, 1794725306.0]


def _pmset_sample() -> str:
    return (FIXTURES / "pmset_sleep_sample.log").read_text()


def test_parse_pmset_sleeps_real_log():
    s = audit.parse_pmset_sleeps(_pmset_sample())
    # starts at "Entering Sleep state" (03:46:19 UTC), not at "Entering DarkWake state"
    assert s[0, 0] == datetime(2026, 11, 15, 3, 46, 19, tzinfo=UTC).timestamp()
    # last interval ends at the lid-open Wake (06:47:36 UTC); "Wake Requests" lines ignored
    assert s[-1, 1] == datetime(2026, 11, 15, 6, 47, 36, tzinfo=UTC).timestamp()
    assert (s[:, 1] > s[:, 0]).all() and (s[1:, 0] >= s[:-1, 1]).all()


def _with_gap_polls():
    before = list(np.arange(GAP_POLLS[0] - 3600, GAP_POLLS[0], 90.0))
    after = list(np.arange(GAP_POLLS[2] + 90, GAP_POLLS[2] + 3600, 90.0))
    t = before + GAP_POLLS + after
    return _polls(t, [90.0] * len(t)), t[-1]


def test_audit_excuses_gaps_only_when_host_asleep():
    polls, end = _with_gap_polls()
    plain = audit.audit(polls, hours=6, end=end)
    assert plain.bad_gaps == 2
    res = audit.audit(polls, hours=6, end=end, sleeps=audit.parse_pmset_sleeps(_pmset_sample()))
    assert res.bad_gaps == 0 and res.excused_gaps == 2
    assert 2.5 * 3600 < res.slept_s < GAP_POLLS[2] - GAP_POLLS[0]


def test_audit_does_not_excuse_gap_when_host_awake():
    polls, end = _with_gap_polls()
    # host asleep for only half of the first gap -> recorder must have stalled
    half = np.array([[GAP_POLLS[0], (GAP_POLLS[0] + GAP_POLLS[1]) / 2]])
    res = audit.audit(polls, hours=6, end=end, sleeps=half)
    assert res.bad_gaps == 2 and res.excused_gaps == 0
