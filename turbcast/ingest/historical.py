"""OpenSky Trino (`state_vectors_data4`) full-rate historical ingest.

Status: research access applied for, not yet granted (2026-11-15). The query builder
and converter are tested offline; `pytest -m trino` fetches one real hour once
pyopensky credentials are configured.

Row semantics: Trino state vectors sit on a 1 s grid (`time`); when no new message
arrived the previous values repeat. `contact_age_s = time - lastcontact` marks those
stale repeats so the residual stage can treat them explicitly.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from turbcast import schema
from turbcast.config import Region, resolve
from turbcast.schema import FPM_PER_MPS, FT_PER_M, KT_PER_MPS, RECORD_SCHEMA

TRINO_COLUMNS = [
    "time",
    "icao24",
    "lat",
    "lon",
    "velocity",
    "heading",
    "vertrate",
    "callsign",
    "onground",
    "baroaltitude",
    "geoaltitude",
    "lastposupdate",
    "lastcontact",
]
MIN_BARO_M = 5500.0  # ~FL180: segmentation needs >= FL200, keep a margin
OUT_DIR = "data/raw/trino"


def build_query(
    hour_start: int, region: Region, min_baro_m: float = MIN_BARO_M, icao24: str | None = None
) -> str:
    """SQL for one hour partition. All interpolated values are numeric or validated."""
    if hour_start % 3600:
        raise ValueError("hour_start must be aligned to an hour (epoch s)")
    where = [
        f"hour = {int(hour_start)}",
        f"time >= {int(hour_start)}",
        f"time < {int(hour_start) + 3600}",
        f"lat BETWEEN {float(region.lat_min)} AND {float(region.lat_max)}",
        f"lon BETWEEN {float(region.lon_min)} AND {float(region.lon_max)}",
        f"baroaltitude >= {float(min_baro_m)}",
    ]
    if icao24 is not None:
        if not re.fullmatch(r"[0-9a-f]{6}", icao24):
            raise ValueError(f"bad icao24: {icao24!r}")
        where.append(f"icao24 = '{icao24}'")
    return f"SELECT {', '.join(TRINO_COLUMNS)} FROM state_vectors_data4 WHERE " + " AND ".join(
        where
    )


def trino_to_record(df: pd.DataFrame) -> pd.DataFrame:
    """Convert raw Trino rows (SI units) into RECORD_SCHEMA (ft, ft/min, kt)."""
    if df.empty:
        return schema.empty(RECORD_SCHEMA)
    t = pd.to_numeric(df["time"]).astype(np.int64)
    out = pd.DataFrame(
        {
            "icao24": df["icao24"].astype(str).str.strip().str.lower(),
            "time": t,
            "lat": pd.to_numeric(df["lat"], errors="coerce"),
            "lon": pd.to_numeric(df["lon"], errors="coerce"),
            "baro_alt_ft": pd.to_numeric(df["baroaltitude"], errors="coerce") * FT_PER_M,
            "vr_fpm": pd.to_numeric(df["vertrate"], errors="coerce") * FPM_PER_MPS,
            "gs_kt": pd.to_numeric(df["velocity"], errors="coerce") * KT_PER_MPS,
            "track_deg": pd.to_numeric(df["heading"], errors="coerce"),
            "on_ground": df["onground"].fillna(False).astype(bool),
            "source": "opensky_trino",
            "rate_hz": 1.0,
            "callsign": df["callsign"]
            .astype(object)
            .where(df["callsign"].notna(), None)
            .map(lambda v: v.strip() or None if isinstance(v, str) else None),
            "typecode": None,
            "snapshot_time": t,
            "pos_age_s": t - pd.to_numeric(df["lastposupdate"], errors="coerce"),
            "contact_age_s": t - pd.to_numeric(df["lastcontact"], errors="coerce"),
            "geo_alt_ft": pd.to_numeric(df["geoaltitude"], errors="coerce") * FT_PER_M,
            "msg_type": None,
        }
    )
    out = schema.cast(out, RECORD_SCHEMA)
    return out.sort_values(["icao24", "time"]).drop_duplicates(["icao24", "time"])


def hour_path(hour_start: int, out_dir: str = OUT_DIR) -> Path:
    ts = pd.Timestamp(hour_start, unit="s", tz="UTC")
    return resolve(out_dir) / f"date={ts:%Y-%m-%d}" / f"hour={ts:%H}" / "part-0.parquet"


def fetch_hour(
    trino: Any, hour_start: int, region: Region, out_dir: str = OUT_DIR, overwrite: bool = False
) -> Path:
    """Fetch one hour via a pyopensky `Trino` object and write it. Resumable: an
    existing partition is skipped unless `overwrite`."""
    path = hour_path(hour_start, out_dir)
    if path.exists() and not overwrite:
        return path
    raw = trino.query(build_query(hour_start, region), cached=False)
    rec = trino_to_record(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    pq.write_table(
        pa.Table.from_pandas(rec, schema=RECORD_SCHEMA, preserve_index=False),
        tmp,
        compression="zstd",
    )
    tmp.replace(path)  # atomic: a crash never leaves a half-written partition
    return path
