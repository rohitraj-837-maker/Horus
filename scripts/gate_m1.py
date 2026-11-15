"""Milestone 1 acceptance (SPEC §10, week 1):

1. One full UTC day of CONUS tracks loaded from OpenSky historical or the Plan B
   archive. Archive sources (adsblol_hist, trino): data in all 24 hours of the day
   and a >= 23.5 h span. Live sources (opensky, adsblol): >= 23.5 h span AND no
   unplanned recorder gaps inside the day.
2. PIREPs for the same day loaded.
3. DuckDB returns one aircraft's series for that day.
4. Live recorder survived 24 h: process still alive, polls span the last 24 h, and
   every unplanned gap is covered by host sleep in macOS's power log. Excused gaps
   are printed (Rohit's decision 2026-11-15, PROGRESS.md).
5. Quantisation measured on that day (reports/data_notes.md section present).

  uv run python scripts/gate_m1.py --day 2025-12-02 --source adsblol_hist
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, date, datetime

import duckdb
import pandas as pd

from turbcast import config
from turbcast.ingest import audit, store

LIVE_SOURCES = {"opensky", "adsblol"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", type=date.fromisoformat, required=True, help="UTC day to check")
    ap.add_argument("--source", default="adsblol_hist", choices=sorted(store.SOURCES))
    ap.add_argument("--recorder-source", default="opensky", choices=sorted(LIVE_SOURCES))
    args = ap.parse_args()
    day = args.day
    cfg = config.load()
    t0 = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
    scan = f"read_parquet('{store.source_glob(args.source)}', hive_partitioning=true)"
    live_dir = config.resolve(cfg.live.out_dir)
    results: list[tuple[str, bool, str]] = []

    # 1. full day of tracks
    try:
        row = duckdb.execute(
            f"select min(time), max(time), count(distinct time // 3600), count(*), "
            f"count(distinct icao24) from {scan} where time >= {t0} and time < {t0 + 86400}"
        ).fetchone()
    except duckdb.IOException:
        row = None
    if row and row[0] is not None:
        span_h = (row[1] - row[0]) / 3600
        ok = span_h >= 23.5 and row[2] == 24
        detail = (
            f"{span_h:.2f} h span, {row[2]}/24 hours with data, {row[3]:,} rows, "
            f"{row[4]:,} aircraft"
        )
        if args.source in LIVE_SOURCES:
            day_audit = audit.audit(audit.read_poll_log(live_dir, args.source), 24, t0 + 86400)
            ok = ok and day_audit.bad_gaps == 0
            detail += f", {day_audit.bad_gaps} recorder gaps in day"
        results.append(("full day of tracks", ok, detail))
    else:
        results.append(("full day of tracks", False, f"no {args.source} data for {day}"))

    # 2. PIREPs for that day
    p = config.resolve(f"data/parquet/pireps/date={day:%Y-%m-%d}.parquet")
    if p.exists():
        n = len(pd.read_parquet(p, columns=["time"]))
        results.append(("PIREPs loaded", n > 0, f"{n:,} reports"))
    else:
        results.append(("PIREPs loaded", False, f"missing {p.name}; run turbcast.ingest.pireps"))

    # 3. one aircraft's series for that day
    try:
        icao = duckdb.execute(
            f"select icao24 from {scan} where time >= {t0} and time < {t0 + 86400} "
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

    # 4. recorder survived 24 h (host sleep excused, and reported)
    polls = audit.read_poll_log(live_dir, args.recorder_source)
    res = audit.audit(polls, 24, time.time(), sleeps=audit.host_sleeps())
    alive = audit.process_alive(live_dir / "recorder.pid")
    reasons = list(res.reasons) + ([] if alive else ["recorder process not running"])
    results.append(
        (
            f"recorder 24 h ({args.recorder_source})",
            res.passed and alive,
            f"process {'alive' if alive else 'DEAD'}, span {res.span_h:.2f} h, "
            f"{res.n_polls} polls, ok {res.ok_fraction:.3f}, "
            f"{res.excused_gaps} gaps excused as laptop sleep ({res.slept_s / 3600:.2f} h)"
            + ("" if not reasons else f" -- {'; '.join(reasons)}"),
        )
    )

    # 5. quantisation measured
    notes = config.resolve("reports/data_notes.md")
    text = notes.read_text() if notes.exists() else ""
    ok = f"BEGIN:quantisation-{args.source}" in text and "est_step_fpm" in text
    results.append(("quantisation measured", ok, f"data_notes.md quantisation-{args.source}"))

    print(f"M1 gate for {day} (tracks={args.source}, recorder={args.recorder_source})")
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    passed = all(ok for _, ok, _ in results)
    print("M1 GATE:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
