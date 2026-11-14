"""Fetch full-rate OpenSky Trino state vectors for [start, end), hour by hour.

    uv sync --extra trino
    uv run python scripts/fetch_study_period.py --start 2025-12-01 --end 2025-12-02

Resumable: hours already on disk are skipped. Needs OpenSky Trino credentials
configured for pyopensky.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, date, datetime

from turbcast import config
from turbcast.ingest import historical


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, required=True, help="exclusive")
    args = ap.parse_args()
    try:
        from pyopensky.trino import Trino
    except ImportError:
        print("pyopensky not installed: run `uv sync --extra trino`", file=sys.stderr)
        return 2
    cfg = config.load()
    trino = Trino()
    t0 = int(datetime(args.start.year, args.start.month, args.start.day, tzinfo=UTC).timestamp())
    t1 = int(datetime(args.end.year, args.end.month, args.end.day, tzinfo=UTC).timestamp())
    for h in range(t0, t1, 3600):
        tic = time.perf_counter()
        path = historical.fetch_hour(trino, h, cfg.region)
        print(
            f"{datetime.fromtimestamp(h, UTC):%Y-%m-%d %H}h -> {path} "
            f"({time.perf_counter() - tic:.0f} s)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
