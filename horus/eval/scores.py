"""Per-minute detector scores for one archive day (input to the detector bench).

For every aircraft, each held series (core/hold.py) gets three scores:
    b0  RMS vertical acceleration over W_stat (textbook, SPEC §6.2 B0)
    b1  b0 with the ADS-B quantisation noise removed (B1)
    b2  Sheppard-corrected residual sigma inside level-flight segments (B2)
B0/B1 are scored on every airborne second at or above segment.min_alt_ft (no
segmentation); B2 only inside level segments. The 1 s series is reduced to one
row per (aircraft, UTC minute): the max of each score, the seconds in scope (the
flight-hours denominator), and the aircraft position at the end of the minute.

Data rate (SPEC §6.2 rate rows): with `step_s` = k > 1 the held track is sampled
on a k-second grid (each sample = the latest value at the end of its k s bin, as a
k-second snapshot feed would see it), and the residual windows come from
eval.rate_windows so every stat window still holds >= min_samples_stat samples.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from horus.config import Config, resolve
from horus.core import hold, residual, rolling, segment
from horus.ingest.store import source_glob

SCORES = ("b0", "b1", "b2")
MAX_DT_STEPS = 1.5  # accel only between adjacent grid samples
TRACK_COLS = ["icao24", "time", "lat", "lon", "baro_alt_ft", "vr_fpm", "on_ground", "typecode"]


def rate_params(
    cfg: Config, step_s: int = 1
) -> tuple[residual.ResidualParams, segment.SegmentParams]:
    """Residual and segment parameters for data rate `step_s` (eval.rate_windows)."""
    key = str(step_s)
    if key not in cfg.eval.rate_windows:
        raise KeyError(f"no eval.rate_windows entry for {step_s} s")
    w_tr, w_st = cfg.eval.rate_windows[key]
    rp = replace(
        residual.ResidualParams.from_config(cfg), w_trend_s=float(w_tr), w_stat_s=float(w_st)
    )
    sp0 = segment.SegmentParams.from_config(cfg)
    sp = replace(sp0, min_segment_s=float(w_tr + w_st))
    return rp, sp


def sep_len(cfg: Config, step_s: int = 1) -> int:
    """Empty samples between pieces: wider than every centred window used here."""
    rp, sp = rate_params(cfg, step_s)
    widths = [sp.window_s, rp.w_trend_s, rp.w_stat_s]
    return max(rolling.window_samples(w, step_s) for w in widths) + 1


def load_tracks(
    day: date, cfg: Config, root: str | Path | None = None, part: tuple[int, int] = (0, 1)
) -> pa.Table:
    """One UTC day of track rows (allowed message types) for the aircraft whose
    hash falls in `part` = (k, n_parts), sorted by (icao24, time)."""
    t0 = int(pd.Timestamp(day, tz="UTC").timestamp())
    types = ", ".join(f"'{m}'" for m in cfg.hold.msg_types)
    glob = source_glob(cfg.bench.source, root)
    # Rows of a UTC day can sit in that day's or the next day's partition.
    dates = f"'{day}', '{day + timedelta(days=1)}'"
    k, n = part
    q = f"""
        select {", ".join(TRACK_COLS)}
        from read_parquet('{glob}', hive_partitioning=true, union_by_name=true)
        where cast(date as varchar) in ({dates})
          and time >= {t0} and time < {t0 + 86400}
          and msg_type in ({types}) and baro_alt_ft is not null
          and lat is not null and lon is not null
          and hash(icao24) % {n} = {k}
        order by icao24, time
    """
    con = duckdb.connect()
    try:
        con.execute("SET enable_progress_bar = false")
        con.execute("SET threads = 2")
        con.execute("SET memory_limit = '1GB'")
        res = con.sql(q).arrow()
        return res.read_all() if isinstance(res, pa.RecordBatchReader) else res
    finally:
        con.close()


def _dedupe(key: np.ndarray, t: np.ndarray) -> np.ndarray:
    keep = np.ones(len(t), dtype=bool)
    keep[:-1] = (key[1:] != key[:-1]) | (t[1:] != t[:-1])  # last row per (key, time)
    return keep


def score_arrays(
    key: np.ndarray,
    time_s: np.ndarray,
    cols: dict[str, np.ndarray],
    cfg: Config,
    step_s: int = 1,
) -> pd.DataFrame:
    """Score sorted, de-duplicated rows of many aircraft; one row per (key, minute)."""
    lay = hold.hold_layout(key, time_s, cfg.hold.max_gap_s, sep_len(cfg, step_s), step_s)
    if len(lay.src) == 0:
        return pd.DataFrame()
    alt = lay.take(cols["baro_alt_ft"])
    vr = lay.take(cols["vr_fpm"])
    ground = lay.take(cols["on_ground"].astype(float), fill=0.0) > 0.5
    t = lay.time

    scope = lay.observed & ~ground & (alt >= cfg.segment.min_alt_ft)
    vr0 = np.where(scope, vr, np.nan)
    rp, sp = rate_params(cfg, step_s)
    # Accel on grid positions (in seconds), not epoch time: two aircraft can sit next
    # to each other in the layout at the same epoch second, but their grid positions
    # are always >= sep_len samples apart, so the max-dt guard masks every
    # cross-piece difference.
    pos = np.arange(len(lay.src), dtype=float) * step_s
    max_dt = MAX_DT_STEPS * step_s
    b0 = residual.accel_rms(pos, vr0, step_s, rp.w_stat_s, rp.min_samples_stat, None, max_dt)
    b1 = residual.accel_rms(pos, vr0, step_s, rp.w_stat_s, rp.min_samples_stat, rp.step_fpm, max_dt)
    segs = segment.level_flight(alt, vr, ground, step_s, sp)
    b2 = residual.residual_stats(segment.mask_outside(vr, segs.seg_id), step_s, rp).sigma
    b0 = np.where(scope, b0, np.nan)
    b1 = np.where(scope, b1, np.nan)
    b2 = np.where(scope, b2, np.nan)

    idx = np.flatnonzero(scope)
    if len(idx) == 0:
        return pd.DataFrame()
    df = pd.DataFrame(
        {
            "key": lay.piece_key[lay.piece[idx]],
            "minute": (t[idx] // 60 * 60).astype(np.int64),
            "lat": lay.take(cols["lat"])[idx],
            "lon": lay.take(cols["lon"])[idx],
            "alt_ft": alt[idx],
            "b0": b0[idx],
            "b1": b1[idx],
            "b2": b2[idx],
        }
    )
    g = df.groupby(["key", "minute"], sort=True)
    out = g.agg(
        lat=("lat", "last"),
        lon=("lon", "last"),
        alt_ft=("alt_ft", "last"),
        n_s=("alt_ft", "size"),
        b0=("b0", "max"),
        b1=("b1", "max"),
        b2=("b2", "max"),
        n_b2=("b2", "count"),
    ).reset_index()
    out["n_s"] *= step_s  # samples -> seconds
    out["n_b2"] *= step_s
    return out


def score_day(
    day: date,
    cfg: Config,
    root: str | Path | None = None,
    chunk_rows: int = 150_000,
    n_parts: int = 4,
    step_s: int = 1,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Per-minute scores for one day. Memory is bounded twice: aircraft are read in
    `n_parts` hash slices, and each slice is scored in chunks of `chunk_rows` stored
    rows (the held 1 s grid is ~14x larger, so 150k rows is ~2M samples)."""
    parts, stats = [], []
    for k in range(n_parts):
        out, st = _score_part(day, cfg, root, chunk_rows, (k, n_parts), step_s)
        if len(out):
            parts.append(out)
        stats.append(st)
    df = pd.concat(parts, ignore_index=True)
    for c in ["icao24", "typecode"]:
        df[c] = df[c].astype("category")
    total = {k: float(sum(s[k] for s in stats)) for k in stats[0]}
    return df.sort_values(["icao24", "minute"], ignore_index=True), total


def _score_part(
    day: date,
    cfg: Config,
    root: str | Path | None,
    chunk_rows: int,
    part: tuple[int, int],
    step_s: int = 1,
) -> tuple[pd.DataFrame, dict[str, float]]:
    tab = load_tracks(day, cfg, root, part)
    n_rows = tab.num_rows
    enc = pc.dictionary_encode(tab.column("icao24")).combine_chunks()
    codes = enc.indices.to_numpy(zero_copy_only=False).astype(np.int64)
    icao = np.asarray(enc.dictionary.to_pylist(), dtype=object)
    tenc = pc.dictionary_encode(tab.column("typecode")).combine_chunks()
    tcodes = pc.fill_null(tenc.indices, -1).to_numpy(zero_copy_only=False).astype(np.int32)
    tnames = np.asarray(tenc.dictionary.to_pylist() + [None], dtype=object)  # -1 -> None
    t = tab.column("time").to_numpy().astype(np.int64)
    cols = {
        c: pc.fill_null(tab.column(c), np.nan).to_numpy(zero_copy_only=False).astype(np.float32)
        for c in ["lat", "lon", "baro_alt_ft", "vr_fpm"]
    }
    cols["lat"] = cols["lat"].astype(np.float64)  # keep full position precision
    cols["lon"] = cols["lon"].astype(np.float64)
    cols["on_ground"] = pc.fill_null(tab.column("on_ground"), False).to_numpy(zero_copy_only=False)
    keep = _dedupe(codes, t)
    del tab, enc, tenc

    order_key = codes[keep]
    t = t[keep]
    cols = {c: v[keep] for c, v in cols.items()}
    types = pd.Series(tcodes[keep]).groupby(order_key).last()
    del codes, tcodes, keep

    # Rows arrive sorted by icao24 string; dictionary codes follow first appearance,
    # so they are sorted too. Cut chunks at aircraft boundaries.
    bounds = np.flatnonzero(np.diff(order_key)) + 1
    cuts = [0]
    for bd in bounds.tolist():
        if bd - cuts[-1] >= chunk_rows:
            cuts.append(bd)
    cuts.append(len(t))
    parts = []
    for lo, hi in zip(cuts[:-1], cuts[1:], strict=True):
        a, b = int(lo), int(hi)
        sub = {c: v[a:b] for c, v in cols.items()}
        part = score_arrays(order_key[a:b], t[a:b], sub, cfg, step_s)
        if len(part):
            parts.append(part)
    out = pd.concat(parts, ignore_index=True)
    out["typecode"] = tnames[types.reindex(out["key"]).to_numpy()]
    out["icao24"] = icao[out["key"].to_numpy()]
    out = out.drop(columns="key")
    for c in ["lat", "lon"]:
        out[c] = out[c].astype(np.float64)
    for c in ["alt_ft", *SCORES]:
        out[c] = out[c].astype(np.float32)
    for c in ["n_s", "n_b2"]:
        out[c] = out[c].astype(np.int16)
    out = out[["icao24", "typecode", "minute", "lat", "lon", "alt_ft", "n_s", *SCORES, "n_b2"]]
    stats = {
        "rows_in": float(n_rows),
        "aircraft": float(len(icao)),
        "flight_hours": float(out["n_s"].sum()) / 3600.0,
        "level_hours": float(out["n_b2"].sum()) / 3600.0,
        "minutes": float(len(out)),
    }
    return out, stats


def scores_dir(cfg: Config, step_s: int = 1) -> Path:
    base = resolve(cfg.bench.scores_dir)
    return base if step_s == 1 else base.with_name(f"{base.name}_r{step_s}s")


def scores_path(day: date | str, cfg: Config, step_s: int = 1) -> Path:
    return scores_dir(cfg, step_s) / f"date={day}" / "scores.parquet"


def load_scores(
    day: date | str, cfg: Config, columns: list[str] | None = None, step_s: int = 1
) -> pd.DataFrame:
    return pd.read_parquet(scores_path(day, cfg, step_s), columns=columns)
