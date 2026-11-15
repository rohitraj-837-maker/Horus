"""adsb.lol history tests on a tar of three real trace files (2025-12-30)."""

import gzip
import io

import httpx
import numpy as np
import orjson
import pandas as pd
import pytest

from turbcast import config, schema
from turbcast.ingest import adsblol_history as ah
from turbcast.ingest import quantisation
from turbcast.schema import RECORD_SCHEMA


@pytest.fixture
def tar_bytes(fixtures_dir):
    return (fixtures_dir / "adsblol_history_sample.tar").read_bytes()


@pytest.fixture
def region():
    return config.load().region


def members(tar_bytes):
    return dict(ah.iter_trace_members(io.BytesIO(tar_bytes)))


def test_iter_skips_non_traces(tar_bytes):
    names = sorted(members(tar_bytes))
    assert len(names) == 3 and all("trace_full_" in n for n in names)


@pytest.mark.parametrize("split", [1, 511, 512, 70_001, 141_000])
def test_chained_stream_any_split(tar_bytes, split):
    """Tar parts are split at arbitrary byte offsets; the stream must not care."""
    parts = [[tar_bytes[:split]], [tar_bytes[split : split + 999], tar_bytes[split + 999 :]]]
    got = dict(ah.iter_trace_members(ah.ChainedStream(parts)))
    assert got == members(tar_bytes)


def test_cruise_trace_conversion(tar_bytes, region):
    blob = next(b for n, b in members(tar_bytes).items() if "a54aaa" in n)
    raw = orjson.loads(gzip.decompress(blob))
    rec = ah.trace_to_record(blob, region)
    schema.validate(rec, RECORD_SCHEMA)
    assert rec["icao24"].unique().tolist() == ["a54aaa"]
    assert (rec["typecode"] == "A21N").all()
    assert rec["lat"].between(24, 50).all() and rec["lon"].between(-125, -66).all()
    assert (rec["baro_alt_ft"] >= 18000).all()
    assert rec["time"].is_monotonic_increasing and rec["time"].is_unique
    assert rec["time"].min() >= int(raw["timestamp"])
    # Baro rates kept are on the 64 ft/min grid; geometric rates were dropped.
    vr = rec["vr_fpm"].dropna().to_numpy(dtype=float)
    assert quantisation.on_grid_fraction(vr, 64.0) == 1.0
    n_geom = sum(
        1
        for p in raw["trace"]
        if p[6] & ah.FLAG_GEOM_RATE
        and isinstance(p[3], int)
        and p[3] >= 18000
        and 24 <= p[1] <= 50
        and -125 <= p[2] <= -66
        and not p[6] & ah.FLAG_STALE
    )
    assert rec["vr_fpm"].isna().sum() >= n_geom
    assert rec["callsign"].notna().mean() > 0.9


def test_non_conus_and_non_icao_return_none(tar_bytes, region):
    m = members(tar_bytes)
    assert ah.trace_to_record(m[next(n for n in m if "0d0caa" in n)], region) is None
    assert ah.trace_to_record(m[next(n for n in m if "~2817aa" in n)], region) is None


def test_convert_stream_stats(tar_bytes, region):
    df, stats = ah.convert_stream(io.BytesIO(tar_bytes), region)
    assert stats == {"traces": 3, "aircraft": 1, "rows": len(df)}
    assert len(df) > 1000


def test_fetch_day_with_mock_transport(tmp_path, tar_bytes, region):
    """Full fetch path (GitHub API -> two tar parts -> Parquet) with no network."""
    day = pd.Timestamp("2025-12-30").date()
    tag = ah.release_tag(day)
    half = len(tar_bytes) // 2

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "api.github.com":
            assert tag in str(req.url)
            assets = [
                {"name": f"{tag}.tar.ab", "browser_download_url": "https://dl.test/ab"},
                {"name": f"{tag}.tar.aa", "browser_download_url": "https://dl.test/aa"},
            ]
            return httpx.Response(200, json={"assets": assets})
        body = tar_bytes[:half] if req.url.path == "/aa" else tar_bytes[half:]
        return httpx.Response(200, content=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    res = ah.fetch_day(day, region, out_dir=str(tmp_path), client=client)
    assert res["aircraft"] == 1 and not res["skipped"]
    back = pd.read_parquet(ah.day_path(day, str(tmp_path)))
    assert len(back) == res["rows"]
    again = ah.fetch_day(day, region, out_dir=str(tmp_path), client=client)
    assert again["skipped"]


@pytest.mark.parametrize(
    "names,expected",
    [
        # 2025-12-30: split release (real asset names)
        (["{t}.tar.ab", "{t}.tar.aa"], ["aa", "ab"]),
        # 2025-12-25: single-file release (real asset names)
        (["{t}.tar"], ["tar"]),
    ],
)
def test_part_urls_split_and_single(names, expected):
    day = pd.Timestamp("2025-12-25").date()
    tag = ah.release_tag(day)

    def handler(req: httpx.Request) -> httpx.Response:
        assets = [
            {"name": n.format(t=tag), "browser_download_url": f"https://dl.test/{n.split('.')[-1]}"}
            for n in names
        ]
        return httpx.Response(200, json={"assets": assets})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert [u.rsplit("/", 1)[1] for u in ah.part_urls(client, day)] == expected


def test_release_tag():
    assert ah.release_tag(pd.Timestamp("2026-01-05").date()) == "v2026.01.05-planes-readsb-prod-0"
    assert np.isnan(ah._num("ground")) and ah._num(5) == 5.0 and np.isnan(ah._num(True))


def test_write_stream_batches_bound_memory(tar_bytes, region):
    batches = []
    stats = ah.write_stream(io.BytesIO(tar_bytes), region, batches.append, batch_rows=1)
    whole, _ = ah.convert_stream(io.BytesIO(tar_bytes), region)
    assert len(batches) == stats["aircraft"] == 1  # one aircraft -> flushed once
    pd.testing.assert_frame_equal(batches[0], whole)
