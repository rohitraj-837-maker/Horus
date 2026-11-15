"""Fetch full-rate OpenSky Trino state vectors (CONUS, >= FL180) via pyopensky.

    uv run python scripts/fetch_trino_history.py --smoke-test
    uv run python scripts/fetch_trino_history.py --multi-day --start 2025-12-02 --end 2026-01-16

Uses `pyopensky.trino.Trino.history()` only (partitioned, explicit columns,
cached). The first run opens a browser for OpenSky login when settings.conf has a
username and no password. Data: data/historical/date=YYYY-MM-DD/hour=HH/.

--smoke-test fetches 2 hours (default 2025-12-02 18:00-20:00 UTC) and writes
volume/time/memory measurements to reports/data_notes.md so the full download can
be sized before it starts.
"""

from __future__ import annotations

import argparse
import resource
import sys
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from turbcast import config
from turbcast.ingest import historical as hist
from turbcast.ingest import quantisation
from turbcast.reports import replace_section

SMOKE_START = datetime(2025, 12, 2, 18, tzinfo=UTC)


def _utc_day(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d


def smoke_report(results: list[dict], cfg: config.Config) -> str:
    ok = [r for r in results if r.get("status") == "ok"]
    if not ok:
        return "Smoke test produced no data: " + "; ".join(str(r) for r in results)
    frames = [pd.read_parquet(hist.hour_path(datetime.fromisoformat(r["hour"]))) for r in ok]
    df = pd.concat(frames, ignore_index=True)
    vr = df["vr_fpm"].dropna().to_numpy(dtype=float)
    lvl = df[df["baro_alt_ft"] >= 20000].sort_values(["icao24", "time"])
    gaps = lvl.groupby("icao24")["time"].diff().dropna().to_numpy()
    rows_h = np.mean([r["rows"] for r in ok])
    mb_h = np.mean([r["mb"] for r in ok])
    s_h = np.mean([r["seconds"] for r in ok])
    stale = 1 - sum(r["rows"] for r in ok) / max(sum(r["rows_raw"] for r in ok), 1)
    n_hours = (len(cfg.splits.train) + len(cfg.splits.test)) * 24
    peak_gb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9  # bytes on macOS
    return "\n".join(
        [
            f"Smoke test {ok[0]['hour']} + {len(ok)} h, CONUS >= FL180 "
            f"(`scripts/fetch_trino_history.py --smoke-test`).",
            "",
            "| Measure | Value |",
            "|---|---|",
            f"| Rows per hour (after stale filter) | {rows_h:,.0f} |",
            f"| Aircraft per hour | {np.mean([r['aircraft'] for r in ok]):,.0f} |",
            f"| Stale rows dropped (contact age > {hist.MAX_STALE_S:.0f} s) | {stale:.1%} |",
            f"| Parquet MB per hour | {mb_h:,.1f} |",
            f"| Seconds per hour (query + convert + write) | {s_h:,.0f} |",
            f"| Peak memory (RSS) | {peak_gb:.2f} GB |",
            f"| Vertical rates on 64 ft/min grid | {quantisation.on_grid_fraction(vr, 64.0):.3f} |",
            f"| Gap percentiles 50/90/99 % at FL200+ (s) | "
            f"{' / '.join(f'{x:.0f}' for x in np.percentile(gaps, [50, 90, 99]))} |",
            f"| **Projected study window ({n_hours} h): disk** | "
            f"**{mb_h * n_hours / 1e3:,.0f} GB** |",
            f"| **Projected study window: wall time** | **{s_h * n_hours / 3600:,.0f} h** |",
        ]
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke-test", action="store_true", help="fetch 2 hours and report sizing")
    mode.add_argument(
        "--multi-day", action="store_true", help="fetch [--start, --end) hour by hour"
    )
    ap.add_argument("--start", type=_utc_day, default=None, help="UTC date/time (multi-day, smoke)")
    ap.add_argument("--end", type=_utc_day, default=None, help="UTC date, exclusive (multi-day)")
    ap.add_argument("--min-free-gb", type=float, default=20.0, help="stop if free disk drops below")
    ap.add_argument("--keep-cache", action="store_true", help="keep pyopensky's raw cache files")
    args = ap.parse_args()

    cfg = config.load()
    if args.smoke_test:
        start = args.start or SMOKE_START
        hours = hist.hours_between(start, start + timedelta(hours=2))
    else:
        if not (args.start and args.end):
            ap.error("--multi-day needs --start and --end")
        hours = hist.hours_between(args.start, args.end)
    hist.check_window(hours[0], hours[-1] + timedelta(hours=1))

    from pyopensky.config import cache_path
    from pyopensky.trino import Trino

    print(
        f"{len(hours)} hour(s) {hours[0]:%Y-%m-%d %H:%M} -> {hours[-1]:%Y-%m-%d %H:%M} UTC; "
        f"cache {cache_path}"
    )
    results = hist.run_hours(
        Trino(),
        hours,
        cfg.region,
        cache_dir=cache_path,
        keep_cache=args.keep_cache,
        min_free_gb=args.min_free_gb,
    )
    log = config.resolve(hist.OUT_DIR) / "_log.csv"
    pd.DataFrame(results).to_csv(log, mode="a", header=not log.exists(), index=False)

    if args.smoke_test:
        body = smoke_report(results, cfg)
        print(body)
        replace_section("data_notes.md", "trino-smoke", body)
    failed = [r for r in results if r.get("status") in ("failed", "disk_floor")]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
