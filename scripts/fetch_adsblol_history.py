"""Stream adsb.lol daily history releases for [start, end) into CONUS Parquet.

    uv run python scripts/fetch_adsblol_history.py --start 2025-12-01 --end 2026-01-15

Each day downloads ~3.5 GB (streamed, never written to disk) and keeps only CONUS
points at or above FL180. Resumable: finished days are skipped. Progress is
appended to data/raw/adsblol_hist/_log.csv.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta

from horus import config
from horus.ingest import adsblol_history as ah


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, required=True, help="exclusive")
    args = ap.parse_args()
    cfg = config.load()
    log = config.resolve(ah.OUT_DIR) / "_log.csv"
    log.parent.mkdir(parents=True, exist_ok=True)
    if not log.exists():
        log.write_text("day,skipped,traces,aircraft,rows,seconds,error\n")
    day, failures = args.start, 0
    while day < args.end:
        tic = time.perf_counter()
        try:
            r = ah.fetch_day(day, cfg.region)
            err = ""
        except Exception as e:  # keep going; a failed day is retried on the next run
            r, err, failures = (
                {"day": str(day), "skipped": False},
                f"{type(e).__name__}: {e}",
                failures + 1,
            )
        secs = time.perf_counter() - tic
        line = (
            f"{day},{int(r.get('skipped', False))},{r.get('traces', '')},{r.get('aircraft', '')},"
            f"{r.get('rows', '')},{secs:.0f},{err.replace(',', ';')[:200]}\n"
        )
        with open(log, "a") as f:
            f.write(line)
        print(line.strip(), flush=True)
        day += timedelta(days=1)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
