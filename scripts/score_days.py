"""Compute per-minute detector scores (B0-B2) for archive days.

    uv run python scripts/score_days.py --start 2025-12-02 --end 2026-01-15 --workers 3

Resumable: days that already have a scores file are skipped. One line per day is
appended to <scores_dir>/_log.csv.
"""

from __future__ import annotations

import argparse
import csv
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta

from horus import config
from horus.eval import scores

LOG_FIELDS = [
    "date",
    "status",
    "seconds",
    "rows_in",
    "aircraft",
    "flight_hours",
    "level_hours",
    "minutes",
    "error",
]


def _day_has_tracks(day: date, cfg: config.Config) -> bool:
    base = config.resolve("data/raw/adsblol_hist") if cfg.bench.source == "adsblol_hist" else None
    return base is None or (base / f"date={day}").exists()


def run_one(day_s: str, step_s: int = 1) -> dict[str, object]:
    t0 = time.time()
    row: dict[str, object] = {"date": day_s}
    try:  # any failure (config, data, code) is recorded; the run continues
        cfg = config.load()
        day = date.fromisoformat(day_s)
        if not _day_has_tracks(day, cfg):
            return {**row, "status": "no_data", "seconds": 0.0}
        df, stats = scores.score_day(day, cfg, step_s=step_s)
        out = scores.scores_path(day, cfg, step_s)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        df.to_parquet(tmp, index=False)
        tmp.replace(out)
        row.update(stats)
        row["status"] = "ok"
    except Exception as e:
        row.update(status="error", error=f"{type(e).__name__}: {e}")
    row["seconds"] = round(time.time() - t0, 1)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--force", action="store_true", help="recompute existing days")
    ap.add_argument("--rate", type=int, default=1, help="data rate in seconds (eval.rates_s)")
    a = ap.parse_args()

    cfg = config.load()
    d0, d1 = date.fromisoformat(a.start), date.fromisoformat(a.end)
    days = [str(d0 + timedelta(days=i)) for i in range((d1 - d0).days + 1)]
    todo = [d for d in days if a.force or not scores.scores_path(d, cfg, a.rate).exists()]
    print(f"{len(days)} days, {len(todo)} to score, {a.workers} worker(s)", flush=True)

    log = scores.scores_dir(cfg, a.rate) / "_log.csv"
    log.parent.mkdir(parents=True, exist_ok=True)
    new_file = not log.exists()
    failed = 0
    with open(log, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        if new_file:
            w.writeheader()
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(run_one, d, a.rate): d for d in todo}
            for f in as_completed(futs):
                row = f.result()
                w.writerow({k: row.get(k, "") for k in LOG_FIELDS})
                fh.flush()
                failed += row["status"] == "error"
                print(
                    f"{row['date']} {row['status']} {row.get('seconds')} s "
                    f"fh={row.get('flight_hours', '')} {row.get('error', '')}",
                    flush=True,
                )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
