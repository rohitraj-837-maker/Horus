"""DuckDB access to the Parquet archives, plus uniform-time resampling.

Every archive (live OpenSky, live adsb.lol, Trino) stores RECORD_SCHEMA rows in
Hive partitions, so one loader serves all sources (SPEC: source-agnostic eval).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from horus.config import resolve
from horus.schema import RECORD_COLUMNS

SOURCES = {
    "opensky": "data/raw/live/source=opensky",
    "adsblol": "data/raw/live/source=adsblol",
    "trino": "data/historical",
    "adsblol_hist": "data/raw/adsblol_hist",
}
NUMERIC = ["lat", "lon", "baro_alt_ft", "vr_fpm", "gs_kt", "track_deg"]


def source_glob(source: str, root: str | Path | None = None) -> str:
    base = Path(root) if root is not None else resolve(SOURCES[source])
    return f"{base}/**/*.parquet"


# Canonical columns by name: partition columns differ per source (live and Trino
# use date=/hour=, the adsb.lol archive only date=), so never `* exclude (...)`.
_COLS = ", ".join(RECORD_COLUMNS)


def _scan(glob: str) -> str:
    return f"read_parquet('{glob}', hive_partitioning=true, union_by_name=true)"


def load_track(
    icao24: str, t0: int, t1: int, source: str = "opensky", root: str | Path | None = None
) -> pd.DataFrame:
    """All rows for one aircraft in [t0, t1), sorted, one row per time."""
    q = (
        f"select {_COLS} from {_scan(source_glob(source, root))} "
        f"where icao24 = ? and time >= ? and time < ? order by time"
    )
    df = duckdb.execute(q, [icao24, int(t0), int(t1)]).df()
    return df.drop_duplicates("time", keep="last").reset_index(drop=True)


def load_day(
    day: date,
    source: str = "opensky",
    root: str | Path | None = None,
    columns: list[str] | None = None,
    min_alt_ft: float | None = None,
) -> pd.DataFrame:
    """One UTC day, column-pruned (keeps memory low on 8 GB)."""
    cols = ", ".join(columns) if columns else _COLS
    t0 = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
    where = f"time >= {t0} and time < {t0 + 86400}"
    if min_alt_ft is not None:
        where += f" and baro_alt_ft >= {float(min_alt_ft)}"
    # Partition pruning: rows for this day can sit in the day's or the next day's
    # partition (snapshot time vs message time), so scan both.
    dates = [f"'{day:%Y-%m-%d}'", f"'{day + timedelta(days=1):%Y-%m-%d}'"]
    q = (
        f"select {cols} from {_scan(source_glob(source, root))} "
        f"where cast(date as varchar) in ({', '.join(dates)}) and {where} "
        f"order by icao24, time"
    )
    return duckdb.sql(q).df()


def iter_tracks(
    day: date,
    source: str = "opensky",
    root: str | Path | None = None,
    min_alt_ft: float | None = None,
) -> Iterator[tuple[str, pd.DataFrame]]:
    df = load_day(day, source, root, min_alt_ft=min_alt_ft)
    for icao, g in df.groupby("icao24", sort=True):
        yield str(icao), g.drop_duplicates("time", keep="last").reset_index(drop=True)


def resample(track: pd.DataFrame, step_s: int) -> pd.DataFrame:
    """Put a track on a uniform time grid of `step_s`.

    Each bin takes the last observation inside it; bins with no observation are NaN.
    No interpolation: inventing vertical-rate values would corrupt the quantisation
    statistics the residual stage relies on.
    """
    if track.empty:
        return pd.DataFrame(columns=["time", *NUMERIC, "on_ground", "observed"])
    t = track["time"].to_numpy(dtype=np.int64)
    t0 = t.min() - t.min() % step_s
    n = int((t.max() - t0) // step_s) + 1
    idx = ((t - t0) // step_s).astype(np.int64)
    out = pd.DataFrame({"time": t0 + np.arange(n, dtype=np.int64) * step_s})
    last = pd.Series(np.arange(len(t))).groupby(idx).last()  # row index of last obs per bin
    for c in NUMERIC:
        vals = np.full(n, np.nan, dtype=np.float32)
        vals[last.index.to_numpy()] = track[c].to_numpy(dtype=np.float32)[last.to_numpy()]
        out[c] = vals
    ground = np.zeros(n, dtype=bool)
    ground[last.index.to_numpy()] = track["on_ground"].to_numpy(dtype=bool)[last.to_numpy()]
    out["on_ground"] = ground
    observed = np.zeros(n, dtype=bool)
    observed[last.index.to_numpy()] = True
    out["observed"] = observed
    return out
