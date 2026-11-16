"""README numbers come from reports/, never typed by hand (CLAUDE.md rule 5).

    uv run python scripts/readme_numbers.py --write   # regenerate the README blocks
    uv run python scripts/readme_numbers.py --check   # exit 1 if README differs

Blocks in README.md:
    <!-- BEGIN:results --> ... <!-- END:results -->   headline + tables from reports/*.md
    <!-- BEGIN:sample -->  ... <!-- END:sample -->    excerpt of docs/watch-sample.txt
"""

from __future__ import annotations

import argparse
import re
import sys

from horus import config


def section(report: str, name: str) -> str:
    text = config.resolve(f"reports/{report}").read_text()
    m = re.search(
        rf"<!-- BEGIN:{re.escape(name)} -->(.*?)<!-- END:{re.escape(name)} -->", text, re.S
    )
    if not m:
        raise SystemExit(f"reports/{report}: section {name!r} not found")
    return m.group(1)


def table(body: str, first_col: str) -> list[dict[str, str]]:
    """First markdown table in `body` whose header starts with `first_col`."""
    lines = body.splitlines()
    for i, ln in enumerate(lines):
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if ln.startswith("|") and cells[0] == first_col:
            rows = []
            for row in lines[i + 2 :]:
                if not row.startswith("|"):
                    break
                vals = [c.strip() for c in row.strip().strip("|").split("|")]
                rows.append(dict(zip(cells, vals, strict=True)))
            return rows
    raise SystemExit(f"no table starting with {first_col!r}")


def pick(rows: list[dict[str, str]], **key: str) -> dict[str, str]:
    hit = [r for r in rows if all(r[k] == v for k, v in key.items())]
    if len(hit) != 1:
        raise SystemExit(f"expected one row for {key}, got {len(hit)}")
    return hit[0]


def grab(pattern: str, text: str) -> str:
    m = re.search(pattern, text)
    if not m:
        raise SystemExit(f"pattern not found: {pattern}")
    return m.group(1)


def results() -> str:
    det1 = section("detector_ablation.md", "detector-ablation")
    det10 = section("detector_ablation.md", "detector-ablation-10s")
    t1, t10 = table(det1, "Row"), table(det10, "Row")
    rec = table(section("detector_ablation.md", "recall-vs-fa"), "Rate")
    now = table(section("nowcast_ablation.md", "nowcast-ablation"), "Row")
    hl_body = section("half_life.md", "half-life")
    hl = table(hl_body, "FL band")
    grid_body = section("perf.md", "grid-update")
    grid_rows = table(grid_body, "Step")
    live_body = section("perf.md", "live-latency")
    live = table(live_body, "p50 (ms)")[0]

    prevalence = grab(r"prevalence (\d\.\d+)", det1)
    n_pos = grab(r"Test windows: (\d+) positive", det1)
    n_neg = grab(r"(\d+) negative", det1)
    test = grab(r"Test: (\d{4}-\d\d-\d\d → \d{4}-\d\d-\d\d \(\d+ days)", det1)
    train = grab(r"Train \(normalisation \+ tau only\): (\d{4}-\d\d-\d\d → \d{4}-\d\d-\d\d)", det1)
    b3 = pick(t1, Row="B3")
    b3r = pick(rec, Rate="1 s", Row="B3")
    n2a = pick(now, Row="N2", Target="aircraft")
    clima = pick(now, Row="CLIM", Target="aircraft")
    n3a = pick(now, Row="N3", Target="aircraft")
    total = pick(grid_rows, Step="total")
    aircraft_live = grab(r"~([\d,]+) aircraft per snapshot", live_body)
    grid_n = grab(r"Grid: ([\d,]+ H3 res-4 cells × \d+ FL bins)", grid_body)
    pr_x = float(n2a["PR-AUC"]) / float(n2a["Base rate"])

    out = [
        f"Trained on {train} (train days only), tested on {test}). "
        "Generated from `reports/` by `scripts/readme_numbers.py`; CI fails if they differ.",
        "",
        f"- **Detector:** the best row (B3) scores PR-AUC **{b3['PR-AUC']}** against a "
        f"random-score baseline of {prevalence}, over {n_pos} MOD+ pilot-report windows and "
        f"{n_neg} windows with no report.",
        "- **But at strict alarm rates it catches little:** "
        f"{float(b3r['Recall @ 1 FA/1000 h']):.1%} of reported turbulence at 1 false alarm per "
        f"1,000 flight-hours, {float(b3r['Recall @ 10 FA/1000 h']):.1%} at 10.",
        f"- **Nowcast:** wind advection (N2) ranks next-15-min detections at ROC-AUC "
        f"**{n2a['ROC-AUC']}** (climatology {clima['ROC-AUC']}); PR-AUC {n2a['PR-AUC']} is "
        f"{pr_x:.1f}× the base rate {n2a['Base rate']}. Negative evidence (N3) hurts "
        f"(ROC-AUC {n3a['ROC-AUC']}).",
        "- **Persistence:** a detection stays informative for t½ ≈ "
        + ", ".join(f"{r['t½ (min)']} min ({r['FL band']})" for r in hl)
        + ".",
        f"- **Speed:** one grid update for 10,000 aircraft takes {total['p95 (ms)']} ms (p95); "
        f"live latency p95 {float(live['p95 (ms)']) / 1000:.1f} s for ~{aircraft_live} "
        "aircraft per snapshot.",
        "",
        "**Detector** (MOD+ windows ±10 min / 40 km / ±2,000 ft; thresholds fitted on train days)",
        "",
        "| Row | Method | PR-AUC 1 s | PR-AUC 10 s | ROC-AUC 1 s "
        "| Recall @ 1 FA/1000 h (1 s) | Recall @ 1 FA/1000 h (10 s) "
        "| Recall @ 10 FA/1000 h (1 s) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    methods = {
        "B0": "RMS vertical acceleration",
        "B1": "B0 minus ADS-B rounding noise",
        "B2": "B1 + level segments + residual σ (Sheppard)",
        "B3": "B2 ÷ per-type smooth-air scale",
    }
    for row, method in methods.items():
        a, b = pick(t1, Row=row), pick(t10, Row=row)
        r1, r10 = pick(rec, Rate="1 s", Row=row), pick(rec, Rate="10 s", Row=row)
        out.append(
            f"| {row} | {method} | {a['PR-AUC']} | {b['PR-AUC']} | {a['ROC-AUC']} "
            f"| {r1['Recall @ 1 FA/1000 h']} | {r10['Recall @ 1 FA/1000 h']} "
            f"| {r1['Recall @ 10 FA/1000 h']} |"
        )
    out += [
        "",
        f"**Nowcast** (every 5 min, 15-min horizon; target: an aircraft entering the cell detects; "
        f"grid {grid_n})",
        "",
        "| Row | Model | Brier | ECE | PR-AUC | ROC-AUC | ROC-AUC vs MOD+ PIREPs |",
        "|---|---|---|---|---|---|---|",
    ]
    names = {
        "CLIM": "climatology (base rate)",
        "N0": "persistence (no decay)",
        "N1": "log-odds + fitted decay",
        "N2": "N1 + GFS wind advection",
        "N3": "N2 + negative evidence",
    }
    for row, name in names.items():
        a, p = pick(now, Row=row, Target="aircraft"), pick(now, Row=row, Target="pirep")
        out.append(
            f"| {row} | {name} | {a['Brier']} | {a['ECE']} | {a['PR-AUC']} | {a['ROC-AUC']} "
            f"| {p['ROC-AUC']} |"
        )
    out += [
        "",
        "**Fitted decay half-life** (how long a detection keeps predicting the next aircraft's)",
        "",
        "| FL band | t½ (min) | 95% interval (min) |",
        "|---|---|---|",
    ]
    out += [f"| {r['FL band']} | {r['t½ (min)']} | {r['95% interval (min)']} |" for r in hl]
    return "\n".join(out)


def sample(n: int = 18) -> str:
    text = config.resolve("docs/watch-sample.txt").read_text().splitlines()
    events = [ln for ln in text if re.match(r"^\s*\d\d:\d\dZ  ", ln)]
    summ = text[text.index(next(ln for ln in text if ln.startswith("Summary"))) :]
    body = events[:n] + ["  ..."] + [""] + summ
    return "```text\n" + "\n".join(body).rstrip() + "\n```"


def render(readme: str) -> str:
    for name, body in (("results", results()), ("sample", sample())):
        pat = re.compile(rf"(<!-- BEGIN:{name} -->).*?(<!-- END:{name} -->)", re.S)
        if not pat.search(readme):
            raise SystemExit(f"README.md: block {name!r} missing")
        readme = pat.sub(lambda m, b=body: f"{m.group(1)}\n{b}\n{m.group(2)}", readme)
    return readme


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true")
    g.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    path = config.resolve("README.md")
    cur = path.read_text()
    new = render(cur)
    if a.write:
        path.write_text(new)
        print(
            "README.md: numbers regenerated from reports/"
            if new != cur
            else "README.md: up to date"
        )
        return 0
    if new != cur:
        print("README.md numbers differ from reports/: run `make readme`")
        return 1
    print("README.md numbers match reports/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
