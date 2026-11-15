"""M3 acceptance gate (SPEC §10 week 3): B3 rows at 1 s / 5 s / 10 s, the window
sensitivity table, per-type parameters saved, and no leakage in any artefact."""

from __future__ import annotations

import re
import sys

from horus import config
from horus.eval import splits


def main() -> int:
    cfg = config.load()
    ok = True

    def check(name: str, cond: bool, detail: str) -> None:
        nonlocal ok
        ok &= cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}: {detail}")

    print("M3 gate")
    text = config.resolve("reports/detector_ablation.md").read_text()
    for r in cfg.eval.rates_s:
        sec = "detector-ablation" + ("" if r == 1 else f"-{r}s")
        m = re.search(rf"<!-- BEGIN:{sec} -->(.*?)<!-- END:{sec} -->", text, re.S)
        line = re.search(r"^\| B3 \|(.*)$", m.group(1), re.M) if m else None
        nums = re.findall(r"\| (\d+\.\d+) ", line.group(1)) if line else []
        check(
            f"B3 row at {r} s", len(nums) >= 4, f"{len(nums)} numeric cells" if line else "missing"
        )
    sens = config.resolve("reports/window_sensitivity.md")
    body = sens.read_text() if sens.exists() else ""
    n_cells = len(re.findall(r"^\| ±\d+ min \| \d+ km \|", body, re.M))
    want = len(cfg.match.sensitivity.window_s) * len(cfg.match.sensitivity.dist_km)
    check("window sensitivity", n_cells >= want, f"{n_cells}/{want} cells")
    norm = config.resolve("models/type_norm.parquet")
    check("per-type parameters", norm.exists(), str(norm.relative_to(config.REPO_ROOT)))
    try:
        n = len(splits.check_models_dir(config.resolve("models"), cfg))
        check("leakage", True, f"{n} artefacts fitted on train dates only")
    except splits.LeakageError as e:
        check("leakage", False, str(e))
    print("M3 GATE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
