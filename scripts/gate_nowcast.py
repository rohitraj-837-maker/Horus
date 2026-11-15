"""M4 / M5 acceptance gates (SPEC §10 weeks 4-5).

uv run python scripts/gate_nowcast.py m4   # N0/N1 rows, fitted t_half per band, winds
uv run python scripts/gate_nowcast.py m5   # + N2/N3 rows, reliability plots, perf budget
"""

from __future__ import annotations

import json
import re
import sys

from horus import config
from horus.eval import splits


def main() -> int:
    stage = sys.argv[1] if len(sys.argv) > 1 else "m4"
    cfg = config.load()
    ok = True

    def check(name: str, cond: bool, detail: str) -> None:
        nonlocal ok
        ok &= cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}: {detail}")

    print(f"{stage.upper()} gate")
    rep = config.resolve("reports/nowcast_ablation.md")
    text = rep.read_text() if rep.exists() else ""
    rows = ["N0", "N1"] + (["N2", "N3"] if stage == "m5" else [])
    for r in rows:
        n = len(re.findall(rf"^\| {r} \| (aircraft|pirep) \|.*\d", text, re.M))
        check(f"row {r}", n >= 1, f"{n} target rows")
    hl = config.resolve("models/half_life.json")
    if hl.exists():
        d = json.loads(hl.read_text())
        bands = [b for b in d["bands"] if b["t_half_s"] > 0]
        check(
            "t_half per band", len(bands) == len(cfg.grid.half_life_bands_fl), f"{len(bands)} bands"
        )
    else:
        check("t_half per band", False, "models/half_life.json missing")
    w = config.resolve(f"data/winds/{cfg.splits.train_start}_{cfg.splits.test_end}.npz")
    check("GFS winds", w.exists(), str(w.name))
    if stage == "m5":
        for r in ("N2", "N3"):
            f = config.resolve(f"reports/figures/reliability_{r}.png")
            check(f"reliability plot {r}", f.exists(), f.name)
        perf = config.resolve("reports/perf.md")
        ptxt = perf.read_text() if perf.exists() else ""
        check("grid update < 2 s for 10k aircraft", "**PASS**" in ptxt, "reports/perf.md")
    try:
        n = len(splits.check_models_dir(config.resolve("models"), cfg))
        check("leakage", True, f"{n} artefacts fitted on train dates only")
    except splits.LeakageError as e:
        check("leakage", False, str(e))
    print(f"{stage.upper()} GATE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
