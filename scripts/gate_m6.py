"""M6 acceptance gate (SPEC §10 week 6): live loop == offline (pytest), live map
updating with p95 end-to-end latency < 10 s (reports/perf.md), 3 curated replays."""

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
    web = config.resolve("web")
    files = [web / f for f in ("index.html", "app.js", "style.css")]
    check("map front end", all(f.exists() for f in files), "web/index.html, app.js, style.css")
    shots = [config.resolve(f"docs/img/horus-map-{m}.png") for m in ("live", "replay")]
    check("UI screenshots", all(s.exists() for s in shots), "docs/img/horus-map-{live,replay}.png")
    print("M6 GATE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
