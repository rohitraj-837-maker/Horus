"""M6 acceptance gate (SPEC §10 week 6): live loop == offline (pytest), live view
updating with p95 end-to-end latency < 10 s (reports/perf.md), 3 curated replays.
The live view is a plain text stream (horus/watch.py; Rohit's decision 2026-11-16,
replacing the web map and then the Textual dashboard); covered by tests/test_watch.py."""

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
    reps = sorted(config.resolve(config.load().watch.cases_dir).glob("*.json"))
    good = [p for p in reps if len(json.loads(p.read_text())["frames"]) >= 30]
    check("curated replays", len(good) >= 3, f"{len(good)} replays with >= 30 frames")
    watch = config.resolve("horus/watch.py")
    check("text stream", watch.exists(), "horus/watch.py (tested in tests/test_watch.py)")
    for name, must in (
        ("watch-sample.txt", ("TURBULENCE", "FORECAST", "PILOT REPORT", "Summary")),
        ("cases-sample.txt", ("CASE 1 of", "PILOT REPORT")),
    ):
        p = config.resolve(f"docs/{name}")
        text = p.read_text() if p.exists() else ""
        check(f"sample {name}", all(m in text for m in must), f"docs/{name} (make watch-sample)")
    print("M6 GATE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
