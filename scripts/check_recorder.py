"""Check the live recorder ran continuously.

    uv run python scripts/check_recorder.py --hours 24
Exit code 0 = pass.
"""

from __future__ import annotations

import argparse
import sys
import time

import duckdb

from horus import config
from horus.ingest.audit import audit, read_poll_log


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--source", default=None)
    ap.add_argument("--end", type=float, default=None, help="epoch s (default: now)")
    args = ap.parse_args()
    cfg = config.load()
    source = args.source or cfg.live.source
    end = args.end or time.time()

    out_dir = config.resolve(cfg.live.out_dir)
    polls = read_poll_log(out_dir, source)
    res = audit(polls, args.hours, end)
    print(f"source={source} window={args.hours} h")
    print(f"  polls={res.n_polls} ok={res.ok_fraction:.3f} span={res.span_h:.2f} h")
    print(f"  max gap={res.max_gap_s:.0f} s (allowed there {res.worst_gap_allowed_s:.0f} s)")
    glob = f"{out_dir}/source={source}/**/*.parquet"
    try:
        n, n_ac = duckdb.execute(
            f"select count(*), count(distinct icao24) from read_parquet('{glob}', "
            f"hive_partitioning=true) where snapshot_time > {end - args.hours * 3600}"
        ).fetchone() or (0, 0)
        print(f"  rows={n:,} aircraft={n_ac:,}")
    except duckdb.IOException:
        print("  no parquet files yet")
    print("PASS" if res.passed else "FAIL: " + "; ".join(res.reasons))
    return 0 if res.passed else 1


if __name__ == "__main__":
    sys.exit(main())
