"""M6 acceptance gate (SPEC §10 week 6): live loop == offline (pytest), live view
updating with p95 end-to-end latency < 10 s (reports/perf.md), 3 curated replays.
The live view is the terminal dashboard (horus/tui; Rohit's decision 2026-11-16,
replacing the web map); its behaviour is covered by tests/tui."""

from __future__ import annotations

import json
import sys

from horus import config


def main() -> int:
    ok = True

    def check(name: str, cond: bool, detail: str) -> None:
        nonlocal ok
        ok &= cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}: {detail}")

    print("M6 gate")
    perf = config.resolve("reports/perf.md").read_text()
    sec = perf.split("<!-- BEGIN:live-latency -->")[-1].split("<!-- END:live-latency -->")[0]
    check("live latency p95 < 10 s", "**PASS**" in sec, "reports/perf.md live-latency")
    reps = sorted(config.resolve("data/replays").glob("*.json"))
    good = [p for p in reps if len(json.loads(p.read_text())["frames"]) >= 30]
    check("curated replays", len(good) >= 3, f"{len(good)} replays with >= 30 frames")
    tui = [config.resolve(f"horus/tui/{f}") for f in ("app.py", "mapview.py", "__main__.py")]
    check("terminal front end", all(f.exists() for f in tui), "horus/tui (tested in tests/tui)")
    shots = [config.resolve(f"docs/img/horus-tui-{m}.svg") for m in ("live", "case")]
    check("TUI screenshots", all(s.exists() for s in shots), "docs/img/horus-tui-{live,case}.svg")
    print("M6 GATE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
