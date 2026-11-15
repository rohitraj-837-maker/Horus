"""OpenSky Trino (`state_vectors_data4`) full-rate historical ingest.

Safety rules (OpenSky): queries go through `pyopensky.trino.Trino.history()` only,
never hand-written SQL. `history()` always adds the `hour` partition bounds, so no
query scans the whole table. Columns are listed explicitly (`COLUMNS`), the
altitude filter runs on the server, only dates from 2023 onward are allowed, and
results go through pyopensky's local cache (`cached=True`).

Row semantics: state vectors sit on a 1 s grid (`time`). `lastcontact` is when
OpenSky last heard the aircraft; rows with `time - lastcontact > max_stale_s` are
stale repeats and are dropped.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from horus import schema
from horus.config import Region, resolve
from horus.schema import FPM_PER_MPS, FT_PER_M, KT_PER_MPS, RECORD_SCHEMA

# Explicit column list (minimises cluster scan). lastposupdate is the one addition
# to the requested set: it gives position age, used to reject stale positions.
COLUMNS = (
    "time",
    "icao24",
    "callsign",
    "lat",
    "lon",
    "velocity",
    "heading",
    "vertrate",
    "baroaltitude",
    "geoaltitude",
    "onground",
    "lastcontact",
    "lastposupdate",
)
MIN_BARO_M = 5500.0  # ~FL180, applied server-side: segmentation needs >= FL200
MAX_STALE_S = 15.0
EARLIEST = datetime(2023, 1, 1, tzinfo=UTC)  # OpenSky notice: pre-2023 data offline
OUT_DIR = "data/historical"


def _epoch_s(col: pd.Series) -> pd.Series:
    """Epoch seconds from either a datetime column (pyopensky `time`) or numbers."""
    if pd.api.types.is_datetime64_any_dtype(col) or isinstance(
        getattr(col.dtype, "pyarrow_dtype", None), pa.TimestampType
    ):
        ts = pd.to_datetime(col, utc=True)
        return (ts - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)
    return pd.to_numeric(col, errors="coerce")


def _float(col: pd.Series) -> np.ndarray:
    return pd.to_numeric(col, errors="coerce").to_numpy(dtype=float, na_value=np.nan)


def trino_to_record(df: pd.DataFrame | None, max_stale_s: float = MAX_STALE_S) -> pd.DataFrame:
    """Convert pyopensky `history()` output (SI units) to RECORD_SCHEMA (ft, ft/min,
    kt). Drops stale rows (time - lastcontact > max_stale_s) and duplicate keys."""
    if df is None or df.empty:
        return schema.empty(RECORD_SCHEMA)
    t = _epoch_s(df["time"]).to_numpy(dtype=float, na_value=np.nan)
    contact_age = t - _float(df["lastcontact"])
    keep = np.isfinite(t) & np.isfinite(contact_age) & (contact_age <= max_stale_s)
    d = df.loc[keep]
    t, contact_age = t[keep].astype(np.int64), contact_age[keep]
    callsign = d["callsign"].astype("object").where(d["callsign"].notna(), None)
    out = pd.DataFrame(
        {
            "icao24": d["icao24"].astype(str).str.strip().str.lower().to_numpy(),
            "time": t,
            "lat": _float(d["lat"]),
            "lon": _float(d["lon"]),
            "baro_alt_ft": _float(d["baroaltitude"]) * FT_PER_M,
            "vr_fpm": _float(d["vertrate"]) * FPM_PER_MPS,
            "gs_kt": _float(d["velocity"]) * KT_PER_MPS,
            "track_deg": _float(d["heading"]),
            "on_ground": d["onground"].astype("boolean").fillna(False).to_numpy(dtype=bool),
            "source": "opensky_trino",
            "rate_hz": 1.0,
            "callsign": [c.strip() or None if isinstance(c, str) else None for c in callsign],
            "typecode": None,
            "snapshot_time": t,
            "pos_age_s": t - _float(d["lastposupdate"]),
            "contact_age_s": contact_age,
            "geo_alt_ft": _float(d["geoaltitude"]) * FT_PER_M,
            "msg_type": None,
        }
    )
    out = schema.cast(out, RECORD_SCHEMA)
    out = out.sort_values(["icao24", "time"]).drop_duplicates(["icao24", "time"])
    return out.reset_index(drop=True)


def check_window(start: datetime, stop: datetime) -> None:
    if start.tzinfo is None or stop.tzinfo is None:
        raise ValueError("use timezone-aware UTC datetimes")
    if start < EARLIEST:
        raise ValueError(f"OpenSky data before {EARLIEST:%Y-%m-%d} is offline; got {start}")
    if not start < stop:
        raise ValueError("start must be before stop")


def fetch_window(
    trino: Any,
    start: datetime,
    stop: datetime,
    region: Region,
    min_baro_m: float = MIN_BARO_M,
    cached: bool = True,
) -> pd.DataFrame | None:
    """One `Trino.history()` call for [start, stop) over `region` (no raw SQL).

    `history()` treats stop as inclusive, so one second is taken off to keep
    consecutive windows from sharing a row."""
    check_window(start, stop)
    from pyopensky.schema import StateVectorsData4  # lazy: tests run without a login

    return trino.history(  # type: ignore[no-any-return]
        pd.Timestamp(start),
        pd.Timestamp(stop) - pd.Timedelta(seconds=1),
        StateVectorsData4.baroaltitude >= min_baro_m,
        bounds=(region.lon_min, region.lat_min, region.lon_max, region.lat_max),
        selected_columns=COLUMNS,
        cached=cached,
    )


def hour_path(hour_start: datetime, out_dir: str = OUT_DIR) -> Path:
    d = resolve(out_dir) / f"date={hour_start:%Y-%m-%d}" / f"hour={hour_start:%H}"
    return d / "part-0.parquet"


def write_record(rec: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    table = pa.Table.from_pandas(rec, schema=RECORD_SCHEMA, preserve_index=False)
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)  # atomic: a crash never leaves a half-written partition


# --------------------------------------------------------------------------- orchestration


def hours_between(start: datetime, stop: datetime) -> list[datetime]:
    t = pd.Timestamp(start).floor("1h")
    out = []
    while t < pd.Timestamp(stop):
        out.append(t.to_pydatetime())
        t += pd.Timedelta(hours=1)
    return out


def is_fatal(exc: BaseException) -> bool:
    """True for errors a retry cannot fix (Trino USER_ERROR: permission denied,
    bad query). Walks SQLAlchemy's `.orig` and the `__cause__` chain."""
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if getattr(e, "error_type", None) == "USER_ERROR":
            return True
        e = getattr(e, "orig", None) or e.__cause__
    return False


def run_hours(
    trino: Any,
    hours: list[datetime],
    region: Region,
    out_dir: str = OUT_DIR,
    cache_dir: Path | None = None,
    keep_cache: bool = False,
    min_free_gb: float = 20.0,
    attempts: int = 4,
    backoff_s: float = 30.0,
    sleep: Any = None,
    log: Any = print,
) -> list[dict[str, Any]]:
    """Fetch, convert and write one hour at a time (bounded memory, resumable).

    * Existing hour partitions are skipped.
    * Each hour is retried with exponential backoff before being logged as failed.
      Fatal errors (`is_fatal`: permission denied, bad query) stop the whole run at
      once: retrying them only repeats the login and the refusal.
    * pyopensky cache files created by this hour are deleted once our own Parquet
      is written (unless `keep_cache`): the raw cache would otherwise double disk use.
    * Stops before an hour if free disk is below `min_free_gb`.
    """
    import shutil
    import time as _time

    sleep = sleep or _time.sleep
    results: list[dict[str, Any]] = []
    for h in hours:
        path = hour_path(h, out_dir)
        if path.exists():
            results.append({"hour": h.isoformat(), "status": "skipped"})
            continue
        free_gb = shutil.disk_usage(path.anchor or "/").free / 1e9
        if free_gb < min_free_gb:
            log(f"stopping: {free_gb:.1f} GB free < {min_free_gb} GB floor")
            results.append({"hour": h.isoformat(), "status": "disk_floor"})
            break
        before = set(cache_dir.iterdir()) if cache_dir and cache_dir.exists() else set()
        tic = _time.perf_counter()
        for i in range(attempts):
            try:
                raw = fetch_window(trino, h, h + pd.Timedelta(hours=1), region)
                n_raw = 0 if raw is None else len(raw)
                rec = trino_to_record(raw)
                del raw
                write_record(rec, path)
                res = {
                    "hour": h.isoformat(),
                    "status": "ok",
                    "rows_raw": n_raw,
                    "rows": len(rec),
                    "aircraft": int(rec["icao24"].nunique()),
                    "mb": round(path.stat().st_size / 1e6, 1),
                    "seconds": round(_time.perf_counter() - tic, 1),
                }
                break
            except Exception as e:  # network / cluster errors: back off and retry
                if is_fatal(e):
                    res = {
                        "hour": h.isoformat(),
                        "status": "fatal",
                        "error": f"{type(e).__name__}: {e}",
                    }
                    break
                if i == attempts - 1:
                    res = {
                        "hour": h.isoformat(),
                        "status": "failed",
                        "error": f"{type(e).__name__}: {e}",
                    }
                else:
                    wait = backoff_s * 2**i
                    log(
                        f"{h:%Y-%m-%d %H}h attempt {i + 1} failed "
                        f"({type(e).__name__}); retry in {wait:.0f} s"
                    )
                    sleep(wait)
        if cache_dir and not keep_cache and res["status"] == "ok" and cache_dir.exists():
            for f in set(cache_dir.iterdir()) - before:
                f.unlink(missing_ok=True)
        log(res)
        results.append(res)
        if res["status"] == "fatal":
            log("stopping: this error will not go away on retry (see message above)")
            break
    return results
