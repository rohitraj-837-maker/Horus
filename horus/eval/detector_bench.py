"""Detector ablation bench (SPEC §6.2): rows B0-B3 on the held-out test split.

    uv run python -m horus.eval.detector_bench [--rate 1|5|10] [--windows sweep]

1. Train days only: fit the per-type normalisation (B3) on smooth aircraft-minutes
   (no turbulence PIREP within negatives.exclusion_time_s / exclusion_dist_km, at
   least normalise.min_level_s level seconds), then count alarm events on clear
   minutes and pick each row's threshold tau for detect.target_fa_per_1000h.
2. Test days: score PIREP windows (positives) and sampled no-PIREP windows
   (negatives), keep windows with >= negatives.min_passes aircraft, and report
   PR-AUC, ROC-AUC, recall at tau, precision at bench.recall_for_precision, and the
   false-alarm rate tau gives on the test days. `--windows sweep` repeats the
   window scoring over match.sensitivity (same tau, same centres).
Writes reports/detector_ablation.md (section per rate), reports/window_sensitivity.md,
models/detector_thresholds[_Ns].json and models/type_norm[_Ns].parquet (both with
train_dates, checked by tests/test_leakage.py), and data/derived/bench/.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from horus import config
from horus.config import Config
from horus.core import normalise as nz
from horus.eval import match, metrics, negatives, scores, splits
from horus.ingest.pireps import LGT, MOD
from horus.reports import replace_section

ROWS = {
    "B0": ("b0", "RMS vertical acceleration (g), fixed threshold"),
    "B1": ("b1", "B0 minus ADS-B quantisation noise"),
    "B2": ("b2", "B1 + level-flight segments + residual sigma (Sheppard)"),
    "B3": ("t_q", "B2 / per-type smooth-air q-scale (normalise.method)"),
    "B3-pct": ("t_pct", "B2 as per-type smooth-air percentile"),
    "B3-z": ("t_z", "B2 as per-type robust z of log sigma"),
}
NORM_COLS = {"t_q": "quantile_scale", "t_pct": "percentile", "t_z": "robust_z"}
LABELS = {
    "MOD+": ("tb_max", MOD),
    "LGT+": ("tb_max", LGT),
    "MOD+ (weakest-intensity rule)": ("tb_min", MOD),
}
OBS_COLS = ["icao24", "typecode", "minute", "lat", "lon", "alt_ft", "n_s", "n_b2", *scores.SCORES]
SMOOTH_SAMPLE_PER_DAY = 300_000


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


def suffix(step_s: int) -> str:
    return "" if step_s == 1 else f"_{step_s}s"


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


def size_map() -> dict[str, str]:
    t = pd.read_parquet(config.resolve("data/parquet/aircraft_types.parquet"))
    return dict(zip(t["typecode"], t["size_class"], strict=True))


def clear_mask(obs: pd.DataFrame, tb: pd.DataFrame, cfg: Config) -> np.ndarray:
    n = cfg.match.negatives
    return ~match.near_any_event(
        obs["minute"].to_numpy(),
        obs["lat"].to_numpy(),
        obs["lon"].to_numpy(),
        tb["time"].to_numpy(),
        tb["lat"].to_numpy(),
        tb["lon"].to_numpy(),
        n.exclusion_time_s,
        n.exclusion_dist_km,
    )


@dataclass
class Ctx:
    cfg: Config
    step_s: int
    tb: pd.DataFrame
    sizes: dict[str, str]
    norms: dict[str, nz.TypeNorm] = field(default_factory=dict)
    norm_notes: dict[str, str] = field(default_factory=dict)

    def load(self, day: str, filters: list[tuple[str, str, int]] | None = None) -> pd.DataFrame:
        o = pd.read_parquet(
            scores.scores_path(day, self.cfg, self.step_s), columns=OBS_COLS, filters=filters
        )
        o["icao24"] = o["icao24"].astype(str)
        o["typecode"] = o["typecode"].astype(object).where(o["typecode"].notna(), None)
        o["size_class"] = o["typecode"].map(self.sizes).fillna("UNK")
        for col, method in NORM_COLS.items():
            if method in self.norms:
                o[col] = nz.index(
                    self.norms[method],
                    o["b2"].to_numpy(float),
                    o["typecode"].to_numpy(object),
                    o["size_class"].to_numpy(object),
                )
        return o

    def rows(self) -> dict[str, tuple[str, str]]:
        return {
            k: v for k, v in ROWS.items() if v[0] in scores.SCORES or NORM_COLS[v[0]] in self.norms
        }


def fit_norms(ctx: Ctx, days: list[str], clear: dict[str, np.ndarray]) -> None:
    cfg = ctx.cfg
    rng = np.random.default_rng(cfg.bench.seed)
    parts = []
    for day in days:
        o = ctx.load(day)
        m = clear[day] & (o["n_b2"].to_numpy() >= cfg.normalise.min_level_s) & o["b2"].notna()
        s = o.loc[m, ["b2", "typecode", "size_class", "icao24"]]
        if len(s) > SMOOTH_SAMPLE_PER_DAY:
            s = s.iloc[np.sort(rng.choice(len(s), SMOOTH_SAMPLE_PER_DAY, replace=False))]
        parts.append(s.assign(unit=s["icao24"] + "/" + day))
    smooth = pd.concat(parts, ignore_index=True)
    print(
        f"  smooth minutes for normalisation: {len(smooth):,} "
        f"(zero sigma {np.mean(smooth['b2'] == 0):.1%})",
        flush=True,
    )
    for method in NORM_COLS.values():
        p = nz.NormParams(method, cfg.normalise.scale_quantile, cfg.normalise.n_min_segments)
        try:
            ctx.norms[method] = nz.fit(
                smooth["b2"].to_numpy(float),
                smooth["typecode"].to_numpy(object),
                smooth["size_class"].to_numpy(object),
                smooth["unit"].to_numpy(object),
                p,
            )
        except nz.DegenerateFitError as e:
            ctx.norm_notes[method] = str(e)
            print(f"  {method}: not fitted ({e})", flush=True)


def save_norm(ctx: Ctx, train: list[str]) -> None:
    method = ctx.cfg.normalise.method
    t = ctx.norms[method].table
    meta = {
        b"train_dates": json.dumps(train).encode(),
        b"method": method.encode(),
        b"quantile": str(ctx.cfg.normalise.scale_quantile).encode(),
        b"rate_s": str(ctx.step_s).encode(),
    }
    tab = pa.Table.from_pandas(t, preserve_index=False)
    path = config.resolve("models") / f"type_norm{suffix(ctx.step_s)}.parquet"
    path.parent.mkdir(exist_ok=True)
    pq.write_table(tab.replace_schema_metadata({**(tab.schema.metadata or {}), **meta}), path)


def fa_counters(
    ctx: Ctx, days: list[str], clear: dict[str, np.ndarray]
) -> dict[str, metrics.FalseAlarmCounter]:
    rows = ctx.rows()
    ctr = {k: metrics.FalseAlarmCounter() for k in rows}
    for day in days:
        o = ctx.load(day)[clear[day]]
        ic, mi = o["icao24"].to_numpy(), o["minute"].to_numpy(np.int64)
        secs = float(o["n_s"].sum())
        for k, (col, _) in rows.items():
            ctr[k].add(ic, mi, o[col].to_numpy(float), secs)
    return ctr


def fa_rates(cfg: Config) -> list[float]:
    """FA rates whose thresholds are saved: the detector target and the grid's."""
    return sorted({cfg.detect.target_fa_per_1000h, cfg.grid.detect_fa_per_1000h})


def window_specs(cfg: Config, sweep: bool) -> list[match.WindowSpec]:
    base = match.WindowSpec.from_config(cfg)
    if not sweep:
        return [base]
    s = cfg.match.sensitivity
    grid = [match.WindowSpec(w, d, cfg.match.alt_ft) for w in s.window_s for d in s.dist_km]
    return [base] + [g for g in grid if g != base]


def test_windows(
    ctx: Ctx,
    days: list[str],
    pireps: pd.DataFrame,
    clear: dict[str, np.ndarray],
    specs: list[match.WindowSpec],
) -> pd.DataFrame:
    cfg = ctx.cfg
    margin = max(s.window_s for s in specs) + 2 * match.MINUTE_S
    allw = match.pirep_windows(pireps, cfg, LGT)  # superset: every label set is >= LGT
    cols = [c for c, _ in ctx.rows().values()]
    out = []
    for day in days:
        d = date.fromisoformat(day)
        t0 = int(pd.Timestamp(d, tz="UTC").timestamp())
        own = ctx.load(day)
        parts = [own]
        for nd, filt in (
            (d - timedelta(days=1), [("minute", ">=", t0 - margin)]),
            (d + timedelta(days=1), [("minute", "<", t0 + 86400 + margin)]),
        ):
            if scores.scores_path(nd, cfg, ctx.step_s).exists():
                parts.append(ctx.load(str(nd), filt))
        obs = pd.concat(parts, ignore_index=True)
        pos = allw[(allw["time"] >= t0) & (allw["time"] < t0 + 86400)].copy()
        pos["label_src"] = "pirep"
        rng = np.random.default_rng([cfg.bench.seed, d.toordinal()])
        neg = negatives.sample_negative_windows(own, clear[day], cfg.bench.neg_per_day, rng)
        neg["label_src"] = "no_pirep"
        neg["tb_max"] = -1
        neg["tb_min"] = -1
        w = pd.concat([pos, neg], ignore_index=True)
        w["wid"] = np.arange(len(w))
        w["date"] = day
        for spec in specs:
            sc = match.window_scores(w, obs, spec, cols)
            out.append(pd.concat([w, sc], axis=1).assign(win_s=spec.window_s, dist_km=spec.dist_km))
        print(f"  {day}: {len(pos)} PIREP windows (LGT+), {len(neg)} no-PIREP windows", flush=True)
    return pd.concat(out, ignore_index=True)


def evaluate(ctx: Ctx, w: pd.DataFrame, taus: dict[str, float]) -> pd.DataFrame:
    cfg = ctx.cfg
    rows = []
    for (ws, dk), g in w.groupby(["win_s", "dist_km"]):
        busy = g[g["n_passes"] >= cfg.match.negatives.min_passes]
        neg = busy[busy["label_src"] == "no_pirep"]
        for label, (col, lvl) in LABELS.items():
            pos = busy[(busy["label_src"] == "pirep") & (busy[col] >= lvl)]
            y = np.r_[np.ones(len(pos)), np.zeros(len(neg))]
            for k, (sc, _) in ctx.rows().items():
                s = np.r_[pos[sc].to_numpy(float), neg[sc].to_numpy(float)]
                m = metrics.summary(y, s, taus[k], cfg.bench.recall_for_precision)
                rows.append({"win_s": ws, "dist_km": dk, "label": label, "row": k, **m})
    return pd.DataFrame(rows)


def render(
    ctx: Ctx,
    res: pd.DataFrame,
    taus: dict[str, float],
    fa_test: dict[str, float],
    meta: dict[str, object],
    w: pd.DataFrame,
) -> str:
    cfg = ctx.cfg
    rate = cfg.detect.target_fa_per_1000h
    rr = cfg.bench.recall_for_precision
    neg = cfg.match.negatives
    base = res[(res["win_s"] == cfg.match.window_s) & (res["dist_km"] == cfg.match.dist_km)]
    main = base[base["label"] == "MOD+"]
    n_pos, n_neg = int(main["n_pos"].iloc[0]), int(main["n_neg"].iloc[0])
    lines = [
        f"**Data rate {ctx.step_s} s** (held series sampled every {ctx.step_s} s; windows "
        f"W_trend/W_stat = {cfg.eval.rate_windows[str(ctx.step_s)]} s). "
        f"Generated by `python -m horus.eval.detector_bench --rate {ctx.step_s}` at commit "
        f"`{meta['commit']}`. Source `{cfg.bench.source}`. "
        f"Train (normalisation + tau only): {meta['train_range']} ({meta['n_train']} days, "
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
    for k, (_, desc) in ROWS.items():
        if k not in taus:
            note = ctx.norm_notes.get(NORM_COLS.get(ROWS[k][0], ""), "not run")
            lines.append(f"| {k} | {desc} | n/a | n/a | n/a | n/a | n/a | {note} |")
            continue
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
    for _, r in base[base["label"] != "MOD+"].iterrows():
        lines.append(
            f"| {r['label']} | {r['row']} | {int(r['n_pos'])} | {r['pr_auc']:.3f} | "
            f"{r['roc_auc']:.3f} | {r['recall_at_tau']:.3f} |"
        )
    wb = w[(w["win_s"] == cfg.match.window_s) & (w["dist_km"] == cfg.match.dist_km)]
    is_pirep = wb["label_src"] == "pirep"
    n_all = int(is_pirep.sum())
    n_busy = int((is_pirep & (wb["n_passes"] >= neg.min_passes)).sum())
    lines += [
        "",
        f"Coverage: {n_busy} of {n_all} LGT+ PIREP windows on test days had "
        f">= {neg.min_passes} aircraft in the window; "
        f"{int((wb['n_passes'] == 0).sum())} windows had none.",
    ]
    if "quantile_scale" in ctx.norms:
        t = ctx.norms["quantile_scale"].table
        lines += [
            "",
            f"Normalisation (B3, q{cfg.normalise.scale_quantile:g} of smooth sigma): "
            + ", ".join(f"{lv} {int((t['level'] == lv).sum())}" for lv in nz.LEVELS)
            + f" groups; global scale {t.loc[t['level'] == 'global', 'scale'].iloc[0]:.1f} ft/min.",
        ]
    return "\n".join(lines)


def render_sweep(ctx: Ctx, res: pd.DataFrame) -> str:
    cfg = ctx.cfg
    sub = res[(res["label"] == "MOD+") & res["row"].isin(["B2", "B3"])]
    lines = [
        f"MOD+ test windows, data rate {ctx.step_s} s, tau fixed from train (it does not depend "
        f"on the match window). Altitude ±{cfg.match.alt_ft:,.0f} ft throughout. Same window "
        f"centres in every cell; >= {cfg.match.negatives.min_passes} aircraft required.",
        "",
        "| Window | Radius | Positives | Negatives | B2 PR-AUC | B3 PR-AUC "
        "| B2 recall @ tau | B3 recall @ tau |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for (ws, dk), g in sub.groupby(["win_s", "dist_km"]):
        b2, b3 = g[g["row"] == "B2"].iloc[0], g[g["row"] == "B3"]
        b3r = b3.iloc[0] if len(b3) else pd.Series({"pr_auc": np.nan, "recall_at_tau": np.nan})
        lines.append(
            f"| ±{int(ws) // 60} min | {dk:.0f} km | {int(b2['n_pos'])} | {int(b2['n_neg'])} | "
            f"{b2['pr_auc']:.3f} | {b3r['pr_auc']:.3f} | "
            f"{b2['recall_at_tau']:.3f} | {b3r['recall_at_tau']:.3f} |"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rate", type=int, default=1)
    ap.add_argument("--windows", choices=["base", "sweep"], default="base")
    a = ap.parse_args()
    cfg = config.load()
    step = a.rate
    train = [d for d in cfg.splits.train if scores.scores_path(d, cfg, step).exists()]
    test = [d for d in cfg.splits.test if scores.scores_path(d, cfg, step).exists()]
    if not train or not test:
        print(
            f"no scored days at {step} s for train or test; run scripts/score_days.py --rate {step}"
        )
        return 2
    splits.check_dates(train, cfg, "detector thresholds")
    pireps = load_pireps(train + test)
    ctx = Ctx(cfg, step, pireps[pireps["has_tb"]].reset_index(drop=True), size_map())

    print(f"rate {step} s | train {len(train)} days | test {len(test)} days", flush=True)
    clear = {}
    for day in train + test:
        o = pd.read_parquet(scores.scores_path(day, cfg, step), columns=["minute", "lat", "lon"])
        clear[day] = clear_mask(o, ctx.tb, cfg)
    print("  clear masks done", flush=True)
    fit_norms(ctx, train, clear)
    ctr = fa_counters(ctx, train, clear)
    taus = {k: c.threshold_for(cfg.detect.target_fa_per_1000h) for k, c in ctr.items()}
    print("  tau:", {k: round(v, 4) for k, v in taus.items()}, flush=True)
    ctr_test = fa_counters(ctx, test, clear)
    fa_test = {k: float(ctr_test[k].rate_per_1000h(taus[k])[0]) for k in taus}
    specs = window_specs(cfg, a.windows == "sweep")
    w = test_windows(ctx, test, pireps, clear, specs)
    res = evaluate(ctx, w, taus)

    meta: dict[str, object] = {
        "commit": git_rev(),
        "train_range": f"{train[0]} → {train[-1]}",
        "test_range": f"{test[0]} → {test[-1]}",
        "n_train": len(train),
        "n_test": len(test),
        "train_hours": next(iter(ctr.values())).hours,
        "test_hours": next(iter(ctr_test.values())).hours,
    }
    sec = "detector-ablation" + ("" if step == 1 else f"-{step}s")
    replace_section("detector_ablation.md", sec, render(ctx, res, taus, fa_test, meta, w))
    if a.windows == "sweep":
        replace_section(
            "window_sensitivity.md", f"window-sensitivity-{step}s", render_sweep(ctx, res)
        )
    models = config.resolve("models")
    models.mkdir(exist_ok=True)
    (models / f"detector_thresholds{suffix(step)}.json").write_text(
        json.dumps(
            {
                "tau": taus,
                "tau_by_rate": {
                    k: {str(r): c.threshold_for(r) for r in fa_rates(cfg)} for k, c in ctr.items()
                },
                "rate_s": step,
                "target_fa_per_1000h": cfg.detect.target_fa_per_1000h,
                "source": cfg.bench.source,
                "commit": meta["commit"],
                "train_dates": train,
            },
            indent=2,
        )
    )
    if cfg.normalise.method in ctx.norms:
        save_norm(ctx, train)
    out = config.resolve("data/derived/bench")
    out.mkdir(parents=True, exist_ok=True)
    w.to_parquet(out / f"windows_test{suffix(step)}.parquet", index=False)
    res.to_csv(out / f"detector_metrics{suffix(step)}.csv", index=False)
    print(
        res[
            (res["label"] == "MOD+")
            & (res["win_s"] == cfg.match.window_s)
            & (res["dist_km"] == cfg.match.dist_km)
        ][["row", "pr_auc", "roc_auc", "recall_at_tau", "precision_at_recall"]]
        .round(3)
        .to_string(index=False),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
