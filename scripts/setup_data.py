"""One-command setup (`make up`): check, and fetch only what is missing, for
`make watch` and `make cases` on a fresh checkout.

    uv run python scripts/setup_data.py            # fetch what is missing
    uv run python scripts/setup_data.py --check    # report only, no network

Fetched from public sources (no account, nothing uploaded):
    archive day     adsb.lol history release, streamed (~3.5 GB down, ~220 MB kept)
    PIREPs          Iowa Environmental Mesonet, one day
    aircraft types  ICAO Doc 8643 via OpenSky metadata (small CSV)
    winds           Open-Meteo GFS, one day
Committed in the repo, so only checked: models/*.json, models/type_norm.parquet,
the case studies in cases/.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from horus import config
from horus.config import Config


@dataclass
class Item:
    name: str
    paths: Callable[[], list[Path]]
    fetch: Callable[[], object] | None  # None = committed, check only
    size: str


def items(cfg: Config, day: date) -> list[Item]:
    from horus.ingest import adsblol_history as ah
    from horus.ingest import aircraft_db as adb
    from horus.ingest import pireps
    from horus.ingest import winds as wio

    def winds_fetch() -> None:
        w = wio.download(cfg, day, day)
        wio.save(w, wio.path(cfg, day, day))

    def winds_paths() -> list[Path]:
        p = wio.find(day)
        return [p] if p is not None else [wio.path(cfg, day, day)]

    R = config.resolve
    return [
        Item(
            "models (committed)",
            lambda: [
                R("models/detector_thresholds.json"),
                R("models/half_life.json"),
                R("models/type_norm.parquet"),
            ],
            None,
            "",
        ),
        Item(
            "case studies (committed)",
            lambda: (
                sorted(R(cfg.watch.cases_dir).glob("*.json"))
                or [R(cfg.watch.cases_dir) / "r*.json"]
            ),
            None,
            "",
        ),
        Item(
            f"archive day {day}",
            lambda: [ah.day_path(day)],
            lambda: ah.fetch_day(day, cfg.region),
            "~3.5 GB download, 10-30 min",
        ),
        Item(
            f"pilot reports {day}",
            lambda: [R(f"data/parquet/pireps/date={day}.parquet")],
            lambda: pireps.ingest_iem(day, day + timedelta(days=1)),
            "~1 MB",
        ),
        Item("aircraft types", lambda: [R(adb.TYPES_PARQUET)], adb.build_types, "~1 MB"),
        Item(f"winds {day}", winds_paths, winds_fetch, "~0.3 MB, ~8 min: 420 grid points"),
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="report only, no downloads")
    ap.add_argument("--day", type=date.fromisoformat, help="archive day (default: first test day)")
    a = ap.parse_args(argv)
    cfg = config.load()
    day = a.day or date.fromisoformat(cfg.splits.test[0])
    missing = 0
    for it in items(cfg, day):
        have = all(p.exists() for p in it.paths())
        if have:
            print(f"  [ok]      {it.name}")
            continue
        if it.fetch is None or a.check:
            missing += 1
            why = "missing from the checkout" if it.fetch is None else f"missing ({it.size})"
            print(f"  [MISSING] {it.name}: {why}")
            continue
        print(f"  [fetch]   {it.name} ({it.size}) ...", flush=True)
        it.fetch()
        ok = all(p.exists() for p in it.paths())
        missing += not ok
        print(f"  [{'ok' if ok else 'FAILED'}]      {it.name}")
    if missing:
        print(f"{missing} item(s) missing." + ("" if a.check else " Re-run `make up` to retry."))
        return 1
    print("Ready:  make watch   (live text stream)   ·   make cases   (case studies)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
