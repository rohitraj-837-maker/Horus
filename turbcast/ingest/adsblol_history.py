"""adsb.lol daily history archives -> CONUS Parquet (study-period data source).

Releases `adsblol/globe_history_<year>` hold one day each as a split tar
(`<tag>.tar.aa`, `.ab`, ...; ~3.5 GB/day, global, ODbL-1.0). Inside, readsb writes
`traces/xx/trace_full_<icao>.json` (gzip JSON):

    {"icao", "t" (typecode), "timestamp" (day start), "trace": [point, ...]}
    point = [dt, lat, lon, alt|"ground", gs, track, flags, baro_rate, {msg}|null,
             msg_type, geom_alt, geom_rate, ias, roll]

flags bits (readsb trace format): 1 stale position, 2 new leg, 4 vertical rate is
geometric (not baro), 8 altitude is geometric. Geometric rates are not 64 ft/min
quantised, so they are dropped (NaN) and stale points are skipped.

The tar is streamed straight from HTTP through `tarfile` (mode "r|"): a day never
touches disk, only the CONUS >= FL180 points are written.

Sampling is irregular (~16-20 s median in cruise, denser when values change), so
`rate_hz` is NaN for this source.
"""

from __future__ import annotations

import gzip
import io
import tarfile
from collections.abc import Callable, Iterable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import orjson
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from turbcast import schema
from turbcast.config import Region, resolve
from turbcast.schema import RECORD_SCHEMA

API_TAG_URL = "https://api.github.com/repos/adsblol/globe_history_{year}/releases/tags/{tag}"
USER_AGENT = "turbcast/0.1 (research prototype)"
OUT_DIR = "data/raw/adsblol_hist"
MIN_ALT_FT = 18000.0  # segmentation needs >= FL200; keep a margin
FLAG_STALE, FLAG_NEW_LEG, FLAG_GEOM_RATE, FLAG_GEOM_ALT = 1, 2, 4, 8


def release_tag(day: date) -> str:
    return f"v{day:%Y.%m.%d}-planes-readsb-prod-0"


# --------------------------------------------------------------------------- streaming


class ChainedStream(io.RawIOBase):
    """A read-only file object over consecutive byte-chunk iterators (tar parts)."""

    def __init__(self, parts: Iterable[Iterable[bytes]]) -> None:
        self._parts = iter(parts)
        self._chunks: Iterator[bytes] = iter(())
        self._buf = b""

    def readable(self) -> bool:
        return True

    def readinto(self, b: Any) -> int:
        while not self._buf:
            try:
                self._buf = next(self._chunks)
            except StopIteration:
                try:
                    self._chunks = iter(next(self._parts))
                except StopIteration:
                    return 0
        n = min(len(b), len(self._buf))
        b[:n] = self._buf[:n]
        self._buf = self._buf[n:]
        return n


def iter_trace_members(fileobj: Any) -> Iterator[tuple[str, bytes]]:
    """Yield (name, raw bytes) for each trace_full member of a streamed tar."""
    with tarfile.open(fileobj=fileobj, mode="r|") as tf:
        for m in tf:
            if m.isfile() and "/traces/" in m.name and "trace_full_" in m.name:
                f = tf.extractfile(m)
                if f is not None:
                    yield m.name, f.read()


# --------------------------------------------------------------------------- conversion


def _num(v: Any) -> float:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else np.nan


def trace_to_record(
    blob: bytes, region: Region, min_alt_ft: float = MIN_ALT_FT
) -> pd.DataFrame | None:
    """Convert one trace file to RECORD_SCHEMA rows inside `region` at/above
    `min_alt_ft`. Returns None if no point qualifies (most of the globe)."""
    raw = gzip.decompress(blob) if blob[:2] == b"\x1f\x8b" else blob
    d = orjson.loads(raw)
    icao = str(d.get("icao", ""))
    tr = d.get("trace") or []
    if not tr or icao.startswith("~"):
        return None
    lat = np.fromiter((_num(p[1]) for p in tr), float, len(tr))
    lon = np.fromiter((_num(p[2]) for p in tr), float, len(tr))
    alt = np.fromiter((_num(p[3]) for p in tr), float, len(tr))
    flags = np.fromiter((int(p[6]) if isinstance(p[6], int) else 0 for p in tr), np.int64, len(tr))
    keep = (
        (lat >= region.lat_min)
        & (lat <= region.lat_max)
        & (lon >= region.lon_min)
        & (lon <= region.lon_max)
        & (alt >= min_alt_ft)
        & ((flags & (FLAG_STALE | FLAG_GEOM_ALT)) == 0)
    )
    if not keep.any():
        return None

    t0 = float(d["timestamp"])
    t = np.round(t0 + np.fromiter((_num(p[0]) for p in tr), float, len(tr))).astype(np.int64)
    vr = np.fromiter((_num(p[7]) for p in tr), float, len(tr))
    vr[(flags & FLAG_GEOM_RATE) != 0] = np.nan  # geometric rate: not quantised baro
    callsign = pd.Series(
        [
            (p[8].get("flight") or "").strip() or None if isinstance(p[8], dict) else None
            for p in tr
        ],
        dtype=object,
    ).ffill()
    msg_type = [p[9] if len(p) > 9 and isinstance(p[9], str) else None for p in tr]
    geo_alt = np.fromiter((_num(p[10]) if len(p) > 10 else np.nan for p in tr), float, len(tr))
    idx = np.flatnonzero(keep)
    out = pd.DataFrame(
        {
            "icao24": icao.lower(),
            "time": t[idx],
            "lat": lat[idx],
            "lon": lon[idx],
            "baro_alt_ft": alt[idx],
            "vr_fpm": vr[idx],
            "gs_kt": np.fromiter((_num(tr[i][4]) for i in idx), float, len(idx)),
            "track_deg": np.fromiter((_num(tr[i][5]) for i in idx), float, len(idx)),
            "on_ground": False,
            "source": "adsblol_hist",
            "rate_hz": np.nan,
            "callsign": callsign.to_numpy()[idx],
            "typecode": d.get("t") or None,
            "snapshot_time": t[idx],
            "pos_age_s": 0.0,
            "contact_age_s": 0.0,
            "geo_alt_ft": geo_alt[idx],
            "msg_type": [msg_type[i] for i in idx],
        }
    )
    out = schema.cast(out, RECORD_SCHEMA)
    return out.drop_duplicates(["icao24", "time"], keep="last")


def convert_stream(
    fileobj: Any, region: Region, min_alt_ft: float = MIN_ALT_FT
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Convert a whole stream into one frame (small inputs and tests)."""
    frames: list[pd.DataFrame] = []
    stats = write_stream(fileobj, region, frames.append, min_alt_ft)
    df = pd.concat(frames, ignore_index=True) if frames else schema.empty(RECORD_SCHEMA)
    return df, stats


def write_stream(
    fileobj: Any,
    region: Region,
    sink: Callable[[pd.DataFrame], Any],
    min_alt_ft: float = MIN_ALT_FT,
    batch_rows: int = 500_000,
) -> dict[str, int]:
    """Convert trace members and hand them to `sink` in batches of ~`batch_rows`,
    so memory stays bounded however large the day is (8 GB laptop)."""
    pending: list[pd.DataFrame] = []
    n_pending = n_traces = n_aircraft = n_rows = 0
    for _, blob in iter_trace_members(fileobj):
        n_traces += 1
        rec = trace_to_record(blob, region, min_alt_ft)
        if rec is None:
            continue
        n_aircraft += 1
        n_rows += len(rec)
        pending.append(rec)
        n_pending += len(rec)
        if n_pending >= batch_rows:
            sink(pd.concat(pending, ignore_index=True))
            pending, n_pending = [], 0
    if pending:
        sink(pd.concat(pending, ignore_index=True))
    return {"traces": n_traces, "aircraft": n_aircraft, "rows": n_rows}


# --------------------------------------------------------------------------- download


def part_urls(client: httpx.Client, day: date) -> list[str]:
    tag = release_tag(day)
    r = client.get(API_TAG_URL.format(year=day.year, tag=tag))
    r.raise_for_status()
    assets = sorted(r.json()["assets"], key=lambda a: a["name"])
    urls = [a["browser_download_url"] for a in assets if ".tar." in a["name"]]
    if not urls:
        raise ValueError(f"no tar parts in release {tag}")
    return urls


def day_path(day: date, out_dir: str = OUT_DIR) -> Path:
    return resolve(out_dir) / f"date={day:%Y-%m-%d}" / "part-0.parquet"


def fetch_day(
    day: date, region: Region, out_dir: str = OUT_DIR, client: httpx.Client | None = None
) -> dict[str, Any]:
    """Stream one day's release, keep CONUS >= FL180, write Parquet atomically."""
    path = day_path(day, out_dir)
    if path.exists():
        return {"day": str(day), "skipped": True}
    own = client is None
    client = client or httpx.Client(
        timeout=httpx.Timeout(60, read=300),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        urls = part_urls(client, day)

        def stream(url: str) -> Iterator[bytes]:
            with client.stream("GET", url) as r:
                r.raise_for_status()
                yield from r.iter_bytes(chunk_size=1 << 20)

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with pq.ParquetWriter(tmp, RECORD_SCHEMA, compression="zstd") as writer:

            def sink(df: pd.DataFrame) -> None:
                writer.write_table(
                    pa.Table.from_pandas(df, schema=RECORD_SCHEMA, preserve_index=False)
                )

            stats = write_stream(ChainedStream(stream(u) for u in urls), region, sink)
        tmp.replace(path)  # atomic: an interrupted day leaves no partial partition
    finally:
        if own:
            client.close()
    return {"day": str(day), "skipped": False, **stats}
