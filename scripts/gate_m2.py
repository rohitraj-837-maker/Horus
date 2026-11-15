"""M2 acceptance gate (SPEC §10 week 2): synthetic tests pass (run by `make check`),
and B0/B1/B2 rows exist in reports/detector_ablation.md, computed from every study
day that has track data, with thresholds fitted on train dates only."""

from __future__ import annotations

import json
import re
import sys

from horus import config
from horus.eval import scores, splits


def main() -> int:
    cfg = config.load()
    ok = True

    def check(name: str, cond: bool, detail: str) -> None:
        nonlocal ok
        ok &= cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}: {detail}")

    print("M2 gate")
    days = cfg.splits.train + cfg.splits.test
    raw = config.resolve("data/raw/adsblol_hist")
    with_tracks = [d for d in days if (raw / f"date={d}").exists()]
    scored = [d for d in with_tracks if scores.scores_path(d, cfg).exists()]
    check(
        "scored days",
        len(scored) == len(with_tracks) > 0,
        f"{len(scored)}/{len(with_tracks)} days with track data",
    )

    rep = config.resolve("reports/detector_ablation.md")
    text = rep.read_text() if rep.exists() else ""
    m = re.search(
        r"<!-- BEGIN:detector-ablation -->(.*?)<!-- END:detector-ablation -->", text, re.S
    )
    body = m.group(1) if m else ""
    for row in ("B0", "B1", "B2"):
        line = re.search(rf"^\| {row} \|(.*)$", body, re.M)
        nums = re.findall(r"\| (\d+\.\d+) ", line.group(1)) if line else []
        check(f"row {row}", len(nums) >= 4, f"{len(nums)} numeric cells" if line else "missing")

    th = config.resolve("models/detector_thresholds.json")
    if th.exists():
        data = json.loads(th.read_text())
        try:
            splits.check_dates(data["train_dates"], cfg, th.name)
            leak, detail = True, f"fitted on {len(data['train_dates'])} train days only"
        except splits.LeakageError as e:
            leak, detail = False, str(e)
        check("thresholds leakage", leak, detail)
        train_scored = [d for d in cfg.splits.train if d in scored]
        check(
            "thresholds use all train days",
            data["train_dates"] == train_scored,
            f"{len(data['train_dates'])} vs {len(train_scored)}",
        )
    else:
        check("thresholds leakage", False, "models/detector_thresholds.json missing")

    print("M2 GATE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
