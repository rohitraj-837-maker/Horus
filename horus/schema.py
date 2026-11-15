"""Canonical track schema and unit conversions.

Every ingest path (Trino, live REST, alternative feeds) converts into this
schema. Downstream code assumes these units and never re-converts.
"""

from __future__ import annotations

import pandas as pd
import pyarrow as pa

# Exact unit definitions (international foot = 0.3048 m, nautical mile = 1852 m).
FT_PER_M = 1.0 / 0.3048  # 3.280839895...
FPM_PER_MPS = 60.0 / 0.3048  # 196.8503937...
KT_PER_MPS = 3600.0 / 1852.0  # 1.9438444...

TRACK_SCHEMA = pa.schema(
    [
        pa.field("icao24", pa.string(), nullable=False),
        pa.field("time", pa.int64(), nullable=False),  # epoch seconds, UTC
        pa.field("lat", pa.float64()),
        pa.field("lon", pa.float64()),
        pa.field("baro_alt_ft", pa.float32()),
        pa.field("vr_fpm", pa.float32()),
        pa.field("gs_kt", pa.float32()),
        pa.field("track_deg", pa.float32()),
        pa.field("on_ground", pa.bool_()),
        pa.field("source", pa.string(), nullable=False),
        pa.field("rate_hz", pa.float32()),
    ]
)

COLUMNS = TRACK_SCHEMA.names

# What the archives store: the track columns plus provenance extras. Lossless
# enough to re-derive anything downstream; `to_track()` drops the extras.
RECORD_EXTRAS = pa.schema(
    [
        pa.field("callsign", pa.string()),
        pa.field("typecode", pa.string()),  # inline from feed if available, else null
        pa.field("snapshot_time", pa.int64(), nullable=False),  # when we polled / row time
        pa.field("pos_age_s", pa.float32()),  # time - last position update
        pa.field("contact_age_s", pa.float32()),  # snapshot_time - last message
        pa.field("geo_alt_ft", pa.float32()),
        pa.field("msg_type", pa.string()),  # e.g. adsb_icao, mlat, tisb_icao (adsb.lol only)
    ]
)
RECORD_SCHEMA = pa.schema(list(TRACK_SCHEMA) + list(RECORD_EXTRAS))
RECORD_COLUMNS = RECORD_SCHEMA.names

# Physically plausible ranges; values outside indicate a unit or parsing bug.
_RANGES: dict[str, tuple[float, float]] = {
    "lat": (-90.0, 90.0),
    "lon": (-180.0, 180.0),
    "baro_alt_ft": (-2000.0, 65000.0),
    "vr_fpm": (-20000.0, 20000.0),
    "gs_kt": (0.0, 1000.0),
    "track_deg": (0.0, 360.0),
    "rate_hz": (0.0, 10.0),
}
_TIME_RANGE = (946684800, 4102444800)  # 2000-01-01 .. 2100-01-01


class SchemaError(ValueError):
    pass


def problems(df: pd.DataFrame, schema: pa.Schema = TRACK_SCHEMA) -> list[str]:
    """Return every schema problem found in `df` (empty list = valid)."""
    out: list[str] = []
    names = schema.names
    missing = [c for c in names if c not in df.columns]
    extra = [c for c in df.columns if c not in names]
    if missing:
        out.append(f"missing columns: {missing}")
    if extra:
        out.append(f"unexpected columns: {extra}")
    if missing:
        return out

    for field in schema:
        col = df[field.name]
        expected = field.type.to_pandas_dtype()
        if field.type == pa.string():
            if not (pd.api.types.is_string_dtype(col) or col.dtype == object):
                out.append(f"{field.name}: dtype {col.dtype}, expected string")
        elif field.type == pa.bool_():
            if not pd.api.types.is_bool_dtype(col):
                out.append(f"{field.name}: dtype {col.dtype}, expected bool")
        elif col.dtype != expected:
            out.append(f"{field.name}: dtype {col.dtype}, expected {expected.__name__}")
        if not field.nullable and col.isna().any():
            out.append(f"{field.name}: contains nulls")

    t = df["time"]
    if len(t) and (t.min() < _TIME_RANGE[0] or t.max() > _TIME_RANGE[1]):
        out.append("time: not epoch seconds in 2000..2100 (milliseconds?)")
    for name, (lo, hi) in _RANGES.items():
        vals = df[name].dropna()
        if len(vals) and (vals.min() < lo or vals.max() > hi):
            out.append(f"{name}: values outside [{lo}, {hi}] (unit error?)")
    return out


def validate(df: pd.DataFrame, schema: pa.Schema = TRACK_SCHEMA) -> pd.DataFrame:
    """Raise SchemaError listing every problem; return `df` unchanged if valid."""
    found = problems(df, schema)
    if found:
        raise SchemaError("; ".join(found))
    return df


def empty(schema: pa.Schema = TRACK_SCHEMA) -> pd.DataFrame:
    """An empty, correctly typed frame for `schema`."""
    return schema.empty_table().to_pandas()


def cast(df: pd.DataFrame, schema: pa.Schema = TRACK_SCHEMA) -> pd.DataFrame:
    """Order columns and coerce dtypes to `schema` (no unit conversion)."""
    out = pd.DataFrame(index=df.index)
    for field in schema:
        col = df[field.name] if field.name in df.columns else pd.Series(None, index=df.index)
        if field.type == pa.string():
            # pandas 3 "str" dtype: matches what Parquet round-trips to; nulls -> NaN.
            out[field.name] = col.astype(object).where(col.notna(), None).astype("str")
        elif field.type == pa.bool_():
            out[field.name] = col.fillna(False).astype(bool)
        else:
            out[field.name] = col.astype(field.type.to_pandas_dtype())
    return out.reset_index(drop=True)


def to_track(record: pd.DataFrame) -> pd.DataFrame:
    """Drop provenance extras, keeping canonical track columns."""
    return record[COLUMNS].copy()
