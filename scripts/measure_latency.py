"""End-to-end live latency (SPEC §6.4, M6): snapshot receipt -> WS message sent.

    make serve   (in another terminal), then: uv run python scripts/measure_latency.py

Reads /api/metrics from the running server after it has processed >= 50 snapshots
and writes the `live-latency` section of reports/perf.md. Budget: p95 < 10 s."""

from __future__ import annotations

import json
import sys
import time
import urllib.request

from horus.reports import replace_section

URL = "http://127.0.0.1:8000"
BUDGET_MS = 10_000


def get(path: str) -> dict:
    with urllib.request.urlopen(URL + path, timeout=10) as r:
        return json.load(r)


def main() -> int:
    for _ in range(120):
        m = get("/api/metrics")
        if m.get("n", 0) >= 50:
            break
        time.sleep(5)
    h = get("/api/health")
    ok = m.get("n", 0) >= 50 and m["p95_ms"] < BUDGET_MS
    body = (
        f"Measured {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} on this laptop: "
        f"archive feed (test day, CONUS, ~{h['aircraft']:,} aircraft per snapshot) "
        "through `LiveLoop`, "
        f"{m.get('n', 0)} snapshots. Latency = snapshot handed to the loop -> WebSocket "
        "message sent (re-score buffers, settle, grid ticks with advection, diff, JSON).\n\n"
        f"| p50 (ms) | p95 (ms) | Budget |\n|---|---|---|\n"
        f"| {m.get('p50_ms', float('nan')):.0f} | {m.get('p95_ms', float('nan')):.0f} "
        "| p95 < 10 s → "
        f"**{'PASS' if ok else 'FAIL'}** |"
    )
    replace_section("perf.md", "live-latency", body)
    print(body)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
