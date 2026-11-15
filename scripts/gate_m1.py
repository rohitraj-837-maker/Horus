"""Milestone 1 acceptance (SPEC §10, week 1):

1. One full UTC day of CONUS tracks loaded: snapshots span >= 23.5 h of that day AND
   the recorder audit over that UTC day finds no unplanned gaps (e.g. laptop sleep).
2. PIREPs for the same day loaded.
3. DuckDB returns one aircraft's series for that full day.
4. Live recorder survived 24 h (scripts/check_recorder.py logic).
5. Quantisation measured on that day (reports/data_notes.md section present).

  uv run python scripts/gate_m1.py [--day YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, date, datetime, timedelta

import duckdb
import pandas as pd

from turbcast import config
from turbcast.ingest import audit, store


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--day",
        type=date.fromisoformat,
        default=None,
        help="UTC day to check (default: yesterday UTC)",
    )
    ap.add_argument("--source", default="opensky")
    args = ap.parse_args()
    day = args.day or (datetime.now(UTC).date() - timedelta(days=1))
    cfg = config.load()
    t0 = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
    results: list[tuple[str, bool, str]] = []

    polls = audit.read_poll_log(config.resolve(cfg.live.out_dir), args.source)

    # 1. full day of tracks (span AND no unplanned recorder gaps inside the day)
    day_audit = audit.audit(polls, 24, t0 + 86400)
    try:
        snaps = duckdb.execute(
            f"select min(snapshot_time), max(snapshot_time), count(distinct snapshot_time), "
            f"count(*), count(distinct icao24) from "
            f"read_parquet('{store.source_glob(args.source)}', hive_partitioning=true) "
            f"where snapshot_time >= {t0} and snapshot_time < {t0 + 86400}"
        ).fetchone()
    except duckdb.IOException:
        snaps = None
    if snaps and snaps[0] is not None:
        span_h = (snaps[1] - snaps[0]) / 3600
        ok = span_h >= 23.5 and day_audit.bad_gaps == 0
        results.append(
            (
                "full day of tracks",
                ok,
                f"{span_h:.2f} h span, {snaps[2]} snapshots, {snaps[3]:,} rows, "
                f"{snaps[4]:,} aircraft, {day_audit.bad_gaps} unplanned gaps in day "
                f"(max {day_audit.max_gap_s:.0f} s)",
            )
        )
    else:
        results.append(("full day of tracks", False, f"no {args.source} data for {day}"))

    # 2. PIREPs for that day
    p = config.resolve(f"data/parquet/pireps/date={day:%Y-%m-%d}.parquet")
    if p.exists():
        n = len(pd.read_parquet(p, columns=["time"]))
        results.append(("PIREPs loaded", n > 0, f"{n:,} reports"))
    else:
        results.append(("PIREPs loaded", False, f"missing {p.name}; run turbcast.ingest.pireps"))

    # 3. one aircraft's full-day series
    try:
        icao = duckdb.execute(
            f"select icao24 from read_parquet('{store.source_glob(args.source)}', "
            f"hive_partitioning=true) where time >= {t0} and time < {t0 + 86400} "
            f"group by 1 order by count(*) desc limit 1"
        ).fetchone()
    except duckdb.IOException:
        icao = None
    if icao:
        tr = store.load_track(icao[0], t0, t0 + 86400, source=args.source)
        results.append(
            (
                "per-aircraft series",
                len(tr) > 1,
                f"{icao[0]}: {len(tr)} samples, {tr['time'].min()}..{tr['time'].max()}",
            )
        )
    else:
        results.append(("per-aircraft series", False, "no aircraft"))

    # 4. recorder 24 h
    res = audit.audit(polls, 24, time.time())
    results.append(
        (
            "recorder 24 h",
            res.passed,
            f"span {res.span_h:.2f} h, {res.n_polls} polls, ok {res.ok_fraction:.3f}, "
            f"max gap {res.max_gap_s:.0f} s"
            + ("" if res.passed else f" -- {'; '.join(res.reasons)}"),
        )
    )

    # 5. quantisation measured
    notes = config.resolve("reports/data_notes.md")
    text = notes.read_text() if notes.exists() else ""
    ok = f"BEGIN:quantisation-{args.source}" in text and "est_step_fpm" in text
    results.append(("quantisation measured", ok, "reports/data_notes.md"))

    print(f"M1 gate for {day} (source={args.source})")
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    passed = all(ok for _, ok, _ in results)
    print("M1 GATE:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
