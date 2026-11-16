"""Recall of MOD+ PIREP windows at each saved false-alarm rate (train thresholds),
per data rate, from the saved bench windows. Writes the `recall-vs-fa` section of
reports/detector_ablation.md. No refitting: thresholds come from
models/detector_thresholds*.json (train only)."""

from __future__ import annotations

import json

import pandas as pd

from horus import config
from horus.eval.detector_bench import ROWS, suffix
from horus.ingest.pireps import MOD
from horus.reports import replace_section


def main() -> int:
    cfg = config.load()
    lines = [
        "MOD+ test windows (>= "
        f"{cfg.match.negatives.min_passes} aircraft, ±{cfg.match.window_s // 60} min / "
        f"{cfg.match.dist_km:.0f} km). Thresholds from train clear-air alarm rates. "
        "'Clear air' only means no PIREP within ±2 h / 150 km, and most turbulence is never "
        "reported, so these false-alarm rates are upper bounds.",
        "",
        "| Rate | Row | Recall @ 1 FA/1000 h | Recall @ 10 FA/1000 h "
        "| No-PIREP windows flagged @ 10 |",
        "|---|---|---|---|---|",
    ]
    for r in cfg.eval.rates_s:
        th_p = config.resolve(f"models/detector_thresholds{suffix(r)}.json")
        w_p = config.resolve(f"data/derived/bench/windows_test{suffix(r)}.parquet")
        if not (th_p.exists() and w_p.exists()):
            continue
        th = json.loads(th_p.read_text())["tau_by_rate"]
        w = pd.read_parquet(w_p)
        w = w[(w["win_s"] == cfg.match.window_s) & (w["dist_km"] == cfg.match.dist_km)]
        w = w[w["n_passes"] >= cfg.match.negatives.min_passes]
        pos = w[(w["label_src"] == "pirep") & (w["tb_max"] >= MOD)]
        neg = w[w["label_src"] == "no_pirep"]
        for k, (col, _) in ROWS.items():
            if k not in th or col not in w:
                continue
            t1, t10 = th[k].get("1.0"), th[k].get("10.0")
            rec1 = (pos[col] >= t1).mean() if t1 is not None else float("nan")
            rec10 = (pos[col] >= t10).mean() if t10 is not None else float("nan")
            fl10 = (neg[col] >= t10).mean() if t10 is not None else float("nan")
            lines.append(f"| {r} s | {k} | {rec1:.3f} | {rec10:.3f} | {fl10:.3f} |")
    replace_section("detector_ablation.md", "recall-vs-fa", "\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
