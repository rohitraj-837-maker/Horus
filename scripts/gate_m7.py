"""M7 acceptance gate (SPEC §10 week 7: hardening). Run by `make gate-m7`, after
`make check` (lint, types, all tests incl. leakage), `make bench` and `make readme`.

    one-command setup    `make up` = uv sync + scripts/setup_data.py; here: --check
    README numbers       match reports/ (scripts/readme_numbers.py --check)
    CI                   .github/workflows/ci.yml runs lint, types, tests, README check
    demo                 GIF + text samples from real `make watch` output
    performance          reports/perf.md budgets PASS (refreshed by `make bench`)
SPEC deviation (Rohit, 2026-11-17): no public demo link. The project rule is local
commits only, no remote and no push, so the demo is the README GIF and the samples.
"""

from __future__ import annotations

import subprocess
import sys

import yaml

from horus import config


def main() -> int:
    ok = True

    def check(name: str, cond: bool, detail: str) -> None:
        nonlocal ok
        ok &= cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}: {detail}")

    def run(*args: str) -> tuple[bool, str]:
        p = subprocess.run(
            [sys.executable, *args], capture_output=True, text=True, cwd=config.resolve(".")
        )
        lines = (p.stdout + p.stderr).strip().splitlines()
        return p.returncode == 0, lines[-1] if lines else ""

    print("M7 gate")
    good, last = run("scripts/setup_data.py", "--check")
    check("make up (setup check)", good, last)
    good, last = run("scripts/readme_numbers.py", "--check")
    check("README numbers", good, last)

    readme = config.resolve("README.md").read_text()
    for need, what in (
        ("Not for flight decisions", "disclaimer"),
        ("## Limitations", "limitations section"),
        ("Bringing Up OpenSky", "OpenSky citation (SPEC §12)"),
        ("Open Database License", "adsb.lol ODbL attribution"),
        ("docs/img/horus-watch.gif", "demo GIF shown"),
    ):
        check(f"README {what}", need in readme, repr(need))

    ci_path = config.resolve(".github/workflows/ci.yml")
    steps = []
    if ci_path.exists():
        ci = yaml.safe_load(ci_path.read_text())
        steps = [s.get("run", "") for s in ci["jobs"]["check"]["steps"]]
    for cmd in (
        "uv sync --frozen",
        "uv run ruff check .",
        "uv run mypy horus/core horus/eval",
        "uv run pytest -q",
        "uv run python scripts/readme_numbers.py --check",
    ):
        check("CI step", cmd in steps, cmd)

    for rel, minimum in (
        ("docs/img/horus-watch.gif", 100_000),
        ("docs/watch-sample.txt", 1_000),
        ("docs/cases-sample.txt", 1_000),
    ):
        p = config.resolve(rel)
        size = p.stat().st_size if p.exists() else 0
        check("demo artefact", size >= minimum, f"{rel} ({size:,} bytes)")

    perf = config.resolve("reports/perf.md").read_text()
    for sec in ("grid-update", "live-latency"):
        body = perf.split(f"<!-- BEGIN:{sec} -->")[-1].split(f"<!-- END:{sec} -->")[0]
        check(f"perf {sec}", "**PASS**" in body, "reports/perf.md")
    print("  [NOTE] public demo link: dropped by decision (no remote, no push); demo is local")
    print("M7 GATE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
