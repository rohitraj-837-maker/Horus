"""Detector ablation bench (SPEC §6.2): rows B0-B2 on the held-out test split.

    uv run python -m horus.eval.detector_bench

1. Train days: count alarm events on clear aircraft-minutes (no PIREP with a
   turbulence group within negatives.exclusion_time_s / exclusion_dist_km) and pick
   each detector's threshold tau for detect.target_fa_per_1000h. Nothing else is fitted.
2. Test days: score PIREP windows (positives) and sampled no-PIREP windows
   (negatives), keep windows with >= negatives.min_passes aircraft, and report
   PR-AUC, ROC-AUC, recall at tau, precision at bench.recall_for_precision, and the
   false-alarm rate tau actually gives on the test days.
Writes reports/detector_ablation.md (section `detector-ablation`), the tau values to
models/detector_thresholds.json (with train_dates, checked by tests/test_leakage.py)
and the scored windows to data/derived/bench/windows_test.parquet.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import date, timedelta

import numpy as np
import pandas as pd

from horus import config
from horus.config import Config
from horus.eval import match, metrics, negatives, scores, splits
from horus.ingest.pireps import LGT, MOD
from horus.reports import replace_section

DETECTORS = {
    "B0": ("b0", "RMS vertical acceleration (g), fixed threshold"),
    "B1": ("b1", "B0 minus ADS-B quantisation noise"),
    "B2": ("b2", "B1 + level-flight segments + residual sigma (Sheppard)"),
}
LABELS = {
    "MOD+": ("tb_max", MOD),
    "LGT+": ("tb_max", LGT),
    "MOD+ (weakest-intensity rule)": ("tb_min", MOD),
}
OBS_COLS = ["icao24", "minute", "lat", "lon", "alt_ft", "n_s", *scores.SCORES]


def git_rev() -> str:
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "horus", "config.yaml"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        return rev + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def load_pireps(days: list[str]) -> pd.DataFrame:
    d0 = date.fromisoformat(days[0]) - timedelta(days=1)
    d1 = date.fromisoformat(days[-1]) + timedelta(days=1)
    base = config.resolve("data/parquet/pireps")
    parts = [
        pd.read_parquet(p)
        for i in range((d1 - d0).days + 1)
        if (p := base / f"date={d0 + timedelta(days=i)}.parquet").exists()
    ]
    return pd.concat(parts, ignore_index=True)


def available(days: list[str], cfg: Config) -> list[str]:
    return [d for d in days if scores.scores_path(d, cfg).exists()]


def clear_mask(obs: pd.DataFrame, tb_reports: pd.DataFrame, cfg: Config) -> np.ndarray:
    n = cfg.match.negatives
    return ~match.near_any_event(
        obs["minute"].to_numpy(),
        obs["lat"].to_numpy(),
        obs["lon"].to_numpy(),
        tb_reports["time"].to_numpy(),
        tb_reports["lat"].to_numpy(),
        tb_reports["lon"].to_numpy(),
        n.exclusion_time_s,
        n.exclusion_dist_km,
    )


def obs_with_margin(day: str, cfg: Config, margin_s: int) -> pd.DataFrame:
    """The day's rows plus neighbouring days' rows within `margin_s` of midnight."""
    d = date.fromisoformat(day)
    t0 = int(pd.Timestamp(d, tz="UTC").timestamp())
    parts = [scores.load_scores(day, cfg, OBS_COLS)]
    for nd, filt in (
        (d - timedelta(days=1), [("minute", ">=", t0 - margin_s)]),
        (d + timedelta(days=1), [("minute", "<", t0 + 86400 + margin_s)]),
    ):
        p = scores.scores_path(nd, cfg)
        if p.exists():
            parts.append(pd.read_parquet(p, columns=OBS_COLS, filters=filt))
    out = pd.concat(parts, ignore_index=True)
    out["icao24"] = out["icao24"].astype(str)
    return out


def fa_counters(
    days: list[str], tb: pd.DataFrame, cfg: Config
) -> dict[str, metrics.FalseAlarmCounter]:
    ctr = {k: metrics.FalseAlarmCounter() for k in DETECTORS}
    for day in days:
        obs = scores.load_scores(day, cfg, OBS_COLS)
        clear = clear_mask(obs, tb, cfg)
        o = obs[clear]
        ic = o["icao24"].astype(str).to_numpy()
        mi = o["minute"].to_numpy(np.int64)
        secs = float(o["n_s"].sum())
        for k, (col, _) in DETECTORS.items():
            ctr[k].add(ic, mi, o[col].to_numpy(float), secs)
        print(
            f"  clear-air alarms counted for {day}: {clear.mean():.1%} of minutes clear", flush=True
        )
    return ctr


def test_windows(
    days: list[str], pireps: pd.DataFrame, tb: pd.DataFrame, cfg: Config
) -> pd.DataFrame:
    spec = match.WindowSpec.from_config(cfg)
    margin = spec.window_s + 2 * match.MINUTE_S
    allw = match.pirep_windows(pireps, cfg, LGT)  # superset: every label set is >= LGT
    cols = [c for c, _ in DETECTORS.values()]
    out = []
    for day in days:
        d = date.fromisoformat(day)
        t0 = int(pd.Timestamp(d, tz="UTC").timestamp())
        obs = obs_with_margin(day, cfg, margin)
        pos = allw[(allw["time"] >= t0) & (allw["time"] < t0 + 86400)].copy()
        pos["label_src"] = "pirep"
        own = obs[(obs["minute"] >= t0) & (obs["minute"] < t0 + 86400)].reset_index(drop=True)
        rng = np.random.default_rng([cfg.bench.seed, d.toordinal()])
        neg = negatives.sample_negative_windows(
            own, clear_mask(own, tb, cfg), cfg.bench.neg_per_day, rng
        )
        neg["label_src"] = "no_pirep"
        neg["tb_max"] = -1
        neg["tb_min"] = -1
        w = pd.concat([pos, neg], ignore_index=True)
        w = pd.concat([w, match.window_scores(w, obs, spec, cols)], axis=1)
        w["date"] = day
        out.append(w)
        print(f"  {day}: {len(pos)} PIREP windows (LGT+), {len(neg)} no-PIREP windows", flush=True)
    return pd.concat(out, ignore_index=True)


def evaluate(w: pd.DataFrame, taus: dict[str, float], cfg: Config) -> list[dict[str, object]]:
    rows = []
    busy = w[w["n_passes"] >= cfg.match.negatives.min_passes]
    neg = busy[busy["label_src"] == "no_pirep"]
    for label, (col, lvl) in LABELS.items():
        pos = busy[(busy["label_src"] == "pirep") & (busy[col] >= lvl)]
        y = np.r_[np.ones(len(pos)), np.zeros(len(neg))]
        for k, (sc, _) in DETECTORS.items():
            s = np.r_[pos[sc].to_numpy(float), neg[sc].to_numpy(float)]
            rows.append(
                {
                    "label": label,
                    "row": k,
                    **metrics.summary(y, s, taus[k], cfg.bench.recall_for_precision),
                }
            )
    return rows


def render(
    res: pd.DataFrame,
    taus: dict[str, float],
    fa_test: dict[str, float],
    meta: dict[str, object],
    w: pd.DataFrame,
    cfg: Config,
) -> str:
    rate = cfg.detect.target_fa_per_1000h
    rr = cfg.bench.recall_for_precision
    neg = cfg.match.negatives
    main = res[res["label"] == "MOD+"]
    n_pos, n_neg = int(main["n_pos"].iloc[0]), int(main["n_neg"].iloc[0])
    lines = [
        f"Generated by `python -m horus.eval.detector_bench` at commit `{meta['commit']}`. "
        f"Source `{cfg.bench.source}` (held 1 s series, see data_notes.md). "
        f"Train (tau only): {meta['train_range']} ({meta['n_train']} days, "
        f"{meta['train_hours']:,.0f} clear flight-hours). "
        f"Test: {meta['test_range']} ({meta['n_test']} days, "
        f"{meta['test_hours']:,.0f} clear flight-hours).",
        "",
        f"**Positives:** MOD+ PIREPs (strongest intensity mentioned) reaching "
        f"FL{cfg.bench.min_fl}+ in CONUS. **Negatives:** windows centred on random "
        f"aircraft-minutes with no turbulence PIREP within ±{neg.exclusion_time_s // 3600} h / "
        f"{neg.exclusion_dist_km:.0f} km. Both: ±{cfg.match.window_s // 60} min, "
        f"{cfg.match.dist_km:.0f} km, ±{cfg.match.alt_ft:,.0f} ft, "
        f">= {neg.min_passes} aircraft in the window. "
        f"Test windows: {n_pos} positive, {n_neg} negative (prevalence "
        f"{main['prevalence'].iloc[0]:.3f} = PR-AUC of a random score).",
        "",
        f"| Row | Method | PR-AUC | ROC-AUC | Recall @ {rate:g} FA/1000 h "
        f"| FA/1000 h on test | Precision @ recall {rr:g} | tau (train) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for k, (_, desc) in DETECTORS.items():
        r = main[main["row"] == k].iloc[0]
        lines.append(
            f"| {k} | {desc} | {r['pr_auc']:.3f} | {r['roc_auc']:.3f} | "
            f"{r['recall_at_tau']:.3f} | {fa_test[k]:.2f} | "
            f"{r['precision_at_recall']:.3f} | {taus[k]:.4g} |"
        )
    lines += [
        "",
        "Other label sets (same negatives, same tau):",
        "",
        "| Label | Row | Positives | PR-AUC | ROC-AUC | Recall @ tau |",
        "|---|---|---|---|---|---|",
    ]
    for _, r in res[res["label"] != "MOD+"].iterrows():
        lines.append(
            f"| {r['label']} | {r['row']} | {int(r['n_pos'])} | {r['pr_auc']:.3f} | "
            f"{r['roc_auc']:.3f} | {r['recall_at_tau']:.3f} |"
        )
    is_pirep = w["label_src"] == "pirep"
    n_all = int(is_pirep.sum())
    n_busy = int((is_pirep & (w["n_passes"] >= neg.min_passes)).sum())
    lines += [
        "",
        f"Coverage: {n_busy} of {n_all} LGT+ PIREP windows on test days had "
        f">= {neg.min_passes} aircraft in the window; "
        f"{int((w['n_passes'] == 0).sum())} windows had none.",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args()
    cfg = config.load()
    train = available(cfg.splits.train, cfg)
    test = available(cfg.splits.test, cfg)
    if not train or not test:
        print("no scored days for train or test; run scripts/score_days.py first")
        return 2
    splits.check_dates(train, cfg, "detector thresholds")
    pireps = load_pireps(train + test)
    tb = pireps[pireps["has_tb"]].reset_index(drop=True)

    print(
        f"train: {len(train)} days -> thresholds at {cfg.detect.target_fa_per_1000h} FA/1000 h",
        flush=True,
    )
    ctr = fa_counters(train, tb, cfg)
    taus = {k: c.threshold_for(cfg.detect.target_fa_per_1000h) for k, c in ctr.items()}
    print("  tau:", taus, flush=True)

    print(f"test: {len(test)} days", flush=True)
    ctr_test = fa_counters(test, tb, cfg)
    fa_test = {k: float(ctr_test[k].rate_per_1000h(taus[k])[0]) for k in DETECTORS}
    w = test_windows(test, pireps, tb, cfg)
    res = pd.DataFrame(evaluate(w, taus, cfg))

    meta: dict[str, object] = {
        "commit": git_rev(),
        "train_range": f"{train[0]} → {train[-1]}",
        "test_range": f"{test[0]} → {test[-1]}",
        "n_train": len(train),
        "n_test": len(test),
        "train_hours": next(iter(ctr.values())).hours,
        "test_hours": next(iter(ctr_test.values())).hours,
    }
    replace_section(
        "detector_ablation.md", "detector-ablation", render(res, taus, fa_test, meta, w, cfg)
    )
    models = config.resolve("models")
    models.mkdir(exist_ok=True)
    (models / "detector_thresholds.json").write_text(
        json.dumps(
            {
                "tau": taus,
                "target_fa_per_1000h": cfg.detect.target_fa_per_1000h,
                "source": cfg.bench.source,
                "commit": meta["commit"],
                "train_dates": train,
            },
            indent=2,
        )
    )
    out = config.resolve("data/derived/bench")
    out.mkdir(parents=True, exist_ok=True)
    w.to_parquet(out / "windows_test.parquet", index=False)
    res.to_csv(out / "detector_metrics.csv", index=False)
    print(res.to_string(), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
