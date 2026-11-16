"""Nowcast bench (SPEC §6.3): replay test days through the grid.

    uv run python -m horus.eval.nowcast_bench fit          # train: base rate, half-life
    uv run python -m horus.eval.nowcast_bench run --rows N0,N1

Observations are passes (eval/passes.py) scored with the B3 index; a pass is
"detected" when T >= tau_grid (the B3 threshold for grid.detect_fa_per_1000h, fitted
on train by the detector bench). Every eval.nowcast_step_s the grid is read for
each cell-FL bin that some aircraft enters within the next eval.nowcast_horizon_s;
the target is whether any of those passes is detected (aircraft target), and,
separately, whether a turbulence PIREP in that bin and horizon is MOD+ (PIREP target).

Rows: CLIM (train base rate everywhere), N0 (persistence of the last verdict),
N1 (decay with fitted per-band half-life + positive evidence), N2 (N1 + advection),
N3 (N2 + negative evidence). Metrics: Brier, ECE, PR-AUC, ROC-AUC.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.metrics import average_precision_score, roc_auc_score

from horus import config
from horus.config import Config
from horus.core import geo, grid
from horus.core import normalise as nz
from horus.core.winds import WindField, wind_at
from horus.eval import detector_bench as db
from horus.eval import halflife, passes, scores, splits
from horus.eval import plots as plots_mod
from horus.ingest.pireps import MOD
from horus.reports import replace_section

OBS = ["icao24", "typecode", "minute", "lat", "lon", "alt_ft", "b2"]


def load_norm() -> nz.TypeNorm:
    path = config.resolve("models/type_norm.parquet")
    meta = pq.read_schema(path).metadata
    p = nz.NormParams(meta[b"method"].decode(), float(meta[b"quantile"]), 0)
    return nz.TypeNorm(p, pd.read_parquet(path), {})


def tau_grid(cfg: Config) -> float:
    th = json.loads(config.resolve("models/detector_thresholds.json").read_text())
    return float(th["tau_by_rate"]["B3"][str(cfg.grid.detect_fa_per_1000h)])


def day_passes(
    day: str, cfg: Config, norm: nz.TypeNorm, sizes: dict[str, str], lattice: np.ndarray
) -> pd.DataFrame:
    o = scores.load_scores(day, cfg, OBS)
    o["icao24"] = o["icao24"].astype(str)
    tc = o["typecode"].astype(object).where(o["typecode"].notna(), None)
    o["T"] = nz.index(
        norm,
        o["b2"].to_numpy(float),
        tc.to_numpy(object),
        tc.map(sizes).fillna("UNK").to_numpy(object),
    )
    p = passes.build(o, "T", cfg, lattice)
    p["day"] = day
    return p


def ece(y: np.ndarray, p: np.ndarray, bins: int) -> float:
    b = np.clip((p * bins).astype(int), 0, bins - 1)
    n = np.bincount(b, minlength=bins)
    conf = np.bincount(b, weights=p, minlength=bins)
    acc = np.bincount(b, weights=y.astype(float), minlength=bins)
    ok = n > 0
    return float(np.sum(np.abs(acc[ok] - conf[ok])) / len(y))


def fit_main(cfg: Config) -> int:
    train = [d for d in cfg.splits.train if scores.scores_path(d, cfg).exists()]
    splits.check_dates(train, cfg, "half life")
    norm, sizes = load_norm(), db.size_map()
    lattice = grid.region_cells(
        cfg.region.lat_min,
        cfg.region.lat_max,
        cfg.region.lon_min,
        cfg.region.lon_max,
        cfg.grid.h3_res,
    )
    tau = tau_grid(cfg)
    allp = []
    for d in train:
        allp.append(day_passes(d, cfg, norm, sizes, lattice))
        print(f"  passes {d}: {len(allp[-1]):,}", flush=True)
    p = pd.concat(allp, ignore_index=True)
    det = p["T"].to_numpy(float) >= tau
    base = float(det.mean())
    pr = halflife.pairs(p, det, cfg.grid.half_life_max_dt_s)
    rng = np.random.default_rng(cfg.bench.seed)
    bands: list[dict[str, Any]] = []
    for lo, hi in cfg.grid.half_life_bands_fl:
        b_lo = (lo - cfg.grid.fl_min) // cfg.grid.fl_bin
        b_hi = (hi - cfg.grid.fl_min) // cfg.grid.fl_bin
        sub = pr[(pr["fl"] >= b_lo) & (pr["fl"] < b_hi)]
        in_band = (p["fl"] >= b_lo) & (p["fl"] < b_hi)
        base_b = float(det[in_band.to_numpy()].mean())
        f = halflife.fit(
            sub, base_b, cfg.grid.half_life_bin_s, cfg.grid.half_life_max_dt_s, 200, rng
        )
        bands.append(
            {
                "fl": [lo, hi],
                "t_half_s": f.t_half_s,
                "ci_s": [f.ci_low_s, f.ci_high_s],
                "a": f.a,
                "base": base_b,
                "n_pairs": f.n_pairs,
            }
        )
        print(
            f"  FL{lo}-{hi}: t_half {f.t_half_s / 60:.1f} min (95% "
            f"{f.ci_low_s / 60:.1f}-{f.ci_high_s / 60:.1f}), A {f.a:.3f}, base {base_b:.4f}, "
            f"pairs {f.n_pairs:,}",
            flush=True,
        )
    out = {
        "tau": tau,
        "detect_fa_per_1000h": cfg.grid.detect_fa_per_1000h,
        "base": base,
        "n_passes": len(p),
        "bands": bands,
        "train_dates": train,
        "commit": db.git_rev(),
    }
    config.resolve("models/half_life.json").write_text(json.dumps(out, indent=2))
    lines = [
        f"Fitted by `python -m horus.eval.nowcast_bench fit` at commit `{out['commit']}` on "
        f"{len(train)} train days "
        f"({train[0]} → {train[-1]}), {len(p):,} passes (aircraft × H3 res-{cfg.grid.h3_res} "
        "cell × 2,000 ft bin). "
        f"Detected pass: B3 T ≥ {tau:.3g} (the {cfg.grid.detect_fa_per_1000h:g} FA/1000 h "
        f"threshold); base rate {base:.4f}.",
        "",
        "Model: P(next pass by another aircraft detects | a detection dt ago) = base + "
        "A·exp(−ln2·dt/t½); "
        f"weighted least squares on {cfg.grid.half_life_bin_s // 60}-min bins up to "
        f"{cfg.grid.half_life_max_dt_s // 60} min; 95% interval from 200 day-block bootstraps.",
        "",
        "| FL band | t½ (min) | 95% interval (min) | A | Base rate | Pairs |",
        "|---|---|---|---|---|---|",
    ]
    for b in bands:
        lines.append(
            f"| FL{b['fl'][0]}–{b['fl'][1]} | {b['t_half_s'] / 60:.1f} | "
            f"{b['ci_s'][0] / 60:.1f}–{b['ci_s'][1] / 60:.1f} | {b['a']:.3f} | {b['base']:.4f} "
            f"| {b['n_pairs']:,} |"
        )
    replace_section("half_life.md", "half-life", "\n".join(lines))
    return 0


@dataclass(frozen=True)
class Model:
    name: str
    persistence: bool = False
    clim: bool = False
    advect: bool = False
    negative: bool = False


MODELS = {
    "CLIM": Model("CLIM", clim=True),
    "N0": Model("N0", persistence=True),
    "N1": Model("N1"),
    "N2": Model("N2", advect=True),
    "N3": Model("N3", advect=True, negative=True),
}


def grid_params(cfg: Config, hl: dict[str, Any]) -> tuple[grid.GridParams, float]:
    g = cfg.grid
    n_fl = (g.fl_max - g.fl_min) // g.fl_bin
    t_half = np.full(n_fl, g.half_life_s, dtype=float)
    hits = []
    for b in hl["bands"]:
        lo = (b["fl"][0] - g.fl_min) // g.fl_bin
        hi = (b["fl"][1] - g.fl_min) // g.fl_bin
        t_half[lo:hi] = b["t_half_s"]
        hits.append(b["base"] + b["a"])
    base = float(hl["base"])
    l0 = float(grid.logit(base))
    l_hit = float(grid.logit(np.mean(hits)))
    p = grid.GridParams(
        l0=l0,
        half_life_s=t_half,
        # Clamps follow the train fit (found 2026-11-16 on the test reliability plot,
        # then checked on train days): the floor must sit below the prior (config
        # l_min = -6 was above l0 = logit(0.0009) = -7), and the cap is the fitted
        # single-detection hit probability; P(detect | several detections) was never
        # fitted, so stacked evidence may not claim more.
        l_min=min(g.l_min, l0 - (l_hit - l0)),
        l_max=min(g.l_max, l_hit),
        a=1e6,
        l_pos_max=l_hit - l0,
        tau=float(hl["tau"]),
    )
    return p, l_hit


def negative_params(cfg: Config, p: grid.GridParams, l_hit: float) -> grid.GridParams:
    """N3: two confident smooth passes bring a fresh alert below the display level
    (SPEC §5.5 rule 3). Display level = logit(grid.theta_display), or the prior if
    alerts never reach theta_display at this base rate; never below l_min."""
    l_disp = float(grid.logit(cfg.grid.theta_display))
    if l_hit <= l_disp:
        l_disp = p.l0
    return replace(p, l_neg=(l_hit - l_disp) / 2.0 + 1e-3)


def replay(
    day_p: pd.DataFrame,
    t_start: int,
    m: Model,
    p: grid.GridParams,
    l_hit: float,
    cfg: Config,
    n_cells: int,
    wind: CellWinds | None,
    pireps: pd.DataFrame,
) -> list[pd.DataFrame]:
    g, e = cfg.grid, cfg.eval
    n_fl = (g.fl_max - g.fl_min) // g.fl_bin
    st = grid.GridState.empty(n_cells, n_fl, p.l0, t_start)
    t_adv = float(t_start)
    q = day_p.sort_values("t1", ignore_index=True)
    t1 = q["t1"].to_numpy(np.int64)
    t0s = q.sort_values("t0")
    t0 = t0s["t0"].to_numpy(np.int64)
    det_all = t0s["T"].to_numpy(float) >= p.tau
    out = []
    a = 0
    for t in range(t_start + e.nowcast_step_s, t_start + 86400 + 1, e.nowcast_step_s):
        if m.advect and wind is not None:
            while t_adv + g.advect_interval_s <= t:
                t_adv += g.advect_interval_s
                u, v = wind.at(t_adv)
                grid.advect(st, wind.lattice, u, v, g.advect_interval_s, t_adv, p)
        b = int(np.searchsorted(t1, t, side="right"))
        if b > a and not m.clim:
            batch = q.iloc[a:b]
            c, f = batch["cell"].to_numpy(np.int64), batch["fl"].to_numpy(np.int64)
            T = batch["T"].to_numpy(float)
            if m.persistence:
                grid.update_persistence(st, c, f, T >= p.tau, float(t), p.l0, l_hit)
            else:
                smooth = (T < cfg.grid.tau_smooth_frac * p.tau) if m.negative else None
                grid.update(st, c, f, T, np.ones(len(c)), float(t), p, smooth)
        a = b
        # aircraft target: bins entered in (t, t + H]
        lo, hi = (
            np.searchsorted(t0, t, side="right"),
            np.searchsorted(t0, t + e.nowcast_horizon_s, side="right"),
        )
        if hi > lo:
            fut = pd.DataFrame(
                {
                    "cell": t0s["cell"].to_numpy()[lo:hi],
                    "fl": t0s["fl"].to_numpy()[lo:hi],
                    "y": det_all[lo:hi],
                }
            )
            tgt = fut.groupby(["cell", "fl"], sort=False)["y"].max().reset_index()
            c, f = tgt["cell"].to_numpy(np.int64), tgt["fl"].to_numpy(np.int64)
            if m.clim:
                prob = np.full(len(c), grid.sigmoid(np.array(p.l0)))
            elif m.persistence:
                prob = grid.sigmoid(st.L[c, f])
            else:
                prob = grid.sigmoid(
                    grid.decayed(st.L[c, f], st.t[c, f], float(t), p.l0, p.half_life_s[f])
                )
            out.append(
                pd.DataFrame({"t": t, "target": "aircraft", "y": tgt["y"].to_numpy(), "p": prob})
            )
        if len(pireps):
            pp = pireps[(pireps["time"] > t) & (pireps["time"] <= t + e.nowcast_horizon_s)]
            if len(pp):
                c = pp["cell"].to_numpy(np.int64)
                f = pp["fl"].to_numpy(np.int64)
                if m.clim:
                    prob = np.full(len(c), grid.sigmoid(np.array(p.l0)))
                elif m.persistence:
                    prob = grid.sigmoid(st.L[c, f])
                else:
                    prob = grid.sigmoid(
                        grid.decayed(st.L[c, f], st.t[c, f], float(t), p.l0, p.half_life_s[f])
                    )
                out.append(
                    pd.DataFrame({"t": t, "target": "pirep", "y": pp["y"].to_numpy(), "p": prob})
                )
    return out


@dataclass
class CellWinds:
    """Winds at every cell centre and FL-bin centre, interpolated once per hour."""

    lattice: grid.Lattice
    field: WindField
    alt_ft: np.ndarray  # (n_fl,) bin centres
    _hour: float = -1.0
    _uv: tuple[np.ndarray, np.ndarray] | None = None

    def at(self, t: float) -> tuple[np.ndarray, np.ndarray]:
        hour = float(np.floor(t / 3600.0) * 3600.0)
        if self._uv is None or hour != self._hour:
            n, nf = len(self.lattice.lat), len(self.alt_ft)
            la = np.repeat(self.lattice.lat, nf)
            lo = np.repeat(self.lattice.lon, nf)
            al = np.tile(self.alt_ft, n)
            u, v = wind_at(self.field, np.full(n * nf, hour + 1800.0), la, lo, al)
            self._uv, self._hour = (u.reshape(n, nf), v.reshape(n, nf)), hour
        return self._uv


def pirep_bins(pireps: pd.DataFrame, cfg: Config, lattice: np.ndarray) -> pd.DataFrame:
    """One row per (PIREP, FL bin) the report's altitude range covers, in the lattice."""
    g = cfg.grid
    pr = pireps[pireps["has_tb"]].copy()
    pr["cell"] = grid.cell_index(
        passes.cell_ids(pr["lat"].to_numpy(), pr["lon"].to_numpy(), g.h3_res), lattice
    )
    lo = geo.fl_bin(pr["fl_lo"].to_numpy(float) * 100, g.fl_min, g.fl_max, g.fl_bin)
    hi = geo.fl_bin(pr["fl_hi"].to_numpy(float) * 100, g.fl_min, g.fl_max, g.fl_bin)
    n_fl = (g.fl_max - g.fl_min) // g.fl_bin
    lo = np.where(lo < 0, np.where(pr["fl_lo"].to_numpy(float) * 100 < g.fl_min * 100, 0, -1), lo)
    hi = np.where(
        hi < 0, np.where(pr["fl_hi"].to_numpy(float) * 100 >= g.fl_max * 100, n_fl - 1, -1), hi
    )
    rows = []
    for i, (a, b) in enumerate(zip(lo, hi, strict=True)):
        if a < 0 or b < 0 or pr["cell"].iloc[i] < 0:
            continue
        for f in range(a, b + 1):
            rows.append((pr["time"].iloc[i], pr["cell"].iloc[i], f, pr["tb_max"].iloc[i] >= MOD))
    return pd.DataFrame(rows, columns=["time", "cell", "fl", "y"])


def run_main(cfg: Config, rows: list[str], plots: bool = False, split: str = "test") -> int:
    days = cfg.splits.test if split == "test" else cfg.splits.train[-5:]
    test = [d for d in days if scores.scores_path(d, cfg).exists()]
    sfx = "" if split == "test" else "-train"
    hl = json.loads(config.resolve("models/half_life.json").read_text())
    splits.check_dates(hl["train_dates"], cfg, "half_life.json")
    norm, sizes = load_norm(), db.size_map()
    lattice = grid.region_cells(
        cfg.region.lat_min,
        cfg.region.lat_max,
        cfg.region.lon_min,
        cfg.region.lon_max,
        cfg.grid.h3_res,
    )
    p, l_hit = grid_params(cfg, hl)
    cw = None
    if any(MODELS[r].advect for r in rows):
        from horus.ingest import winds as winds_io

        wf = winds_io.load(
            winds_io.path(
                cfg,
                date.fromisoformat(cfg.splits.train_start),
                date.fromisoformat(cfg.splits.test_end),
            )
        )
        g = cfg.grid
        centres = (np.arange(g.fl_min, g.fl_max, g.fl_bin) + g.fl_bin / 2) * 100.0
        cw = CellWinds(grid.Lattice.build(lattice), wf, centres)
    p_neg = negative_params(cfg, p, l_hit)
    pir = pirep_bins(db.load_pireps(test), cfg, lattice)
    res: dict[str, list[pd.DataFrame]] = {r: [] for r in rows}
    for d in test:
        dp = day_passes(d, cfg, norm, sizes, lattice)
        t_start = int(pd.Timestamp(date.fromisoformat(d), tz="UTC").timestamp())
        pday = pir[
            (pir["time"] > t_start) & (pir["time"] <= t_start + 86400 + cfg.eval.nowcast_horizon_s)
        ]
        for r in rows:
            pr_ = p_neg if MODELS[r].negative else p
            res[r] += replay(dp, t_start, MODELS[r], pr_, l_hit, cfg, len(lattice), cw, pday)
        print(f"  {d}: {len(dp):,} passes", flush=True)
    table = []
    for r in rows:
        df = pd.concat(res[r], ignore_index=True)
        for tg, g in df.groupby("target"):
            y, pr_ = g["y"].to_numpy(bool), g["p"].to_numpy(float)
            table.append(
                {
                    "row": r,
                    "target": tg,
                    "n": len(g),
                    "base": float(y.mean()),
                    "brier": float(np.mean((pr_ - y) ** 2)),
                    "ece": ece(y, pr_, cfg.eval.ece_bins),
                    "pr_auc": float(average_precision_score(y, pr_)) if y.any() else np.nan,
                    "roc_auc": float(roc_auc_score(y, pr_)) if 0 < y.sum() < len(y) else np.nan,
                }
            )
    t = pd.DataFrame(table)
    lines = [
        f"Generated by `python -m horus.eval.nowcast_bench run` at commit `{db.git_rev()}`. "
        f"{split.capitalize()} {test[0]} → {test[-1]} ({len(test)} days). "
        f"Snapshot every {cfg.eval.nowcast_step_s // 60} min; horizon "
        f"{cfg.eval.nowcast_horizon_s // 60} min. "
        "Aircraft target: a pass entering the cell-bin in the horizon detects (B3 T ≥ "
        f"{p.tau:.3g}). "
        "PIREP target: a turbulence PIREP in the cell-bin in the horizon is MOD+. "
        "Grid parameters (base rate, hit probability, per-band t½) fitted on train only "
        "(reports/half_life.md).",
        "",
        "| Row | Target | n | Base rate | Brier | ECE | PR-AUC | ROC-AUC |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for _, r in t.iterrows():
        lines.append(
            f"| {r['row']} | {r['target']} | {int(r['n']):,} | {r['base']:.4f} | "
            f"{r['brier']:.5f} | {r['ece']:.5f} | {r['pr_auc']:.3f} | {r['roc_auc']:.3f} |"
        )
    replace_section("nowcast_ablation.md", "nowcast-ablation" + sfx, "\n".join(lines))
    out_dir = config.resolve("data/derived/bench")
    out_dir.mkdir(parents=True, exist_ok=True)
    for r in rows:
        df = pd.concat(res[r], ignore_index=True)
        df.to_parquet(out_dir / f"nowcast_{r}{sfx}.parquet", index=False)
        if plots:
            a = df[df["target"] == "aircraft"]
            plots_mod.reliability(
                a["y"].to_numpy(),
                a["p"].to_numpy(),
                f"{r}: aircraft target, {split} {test[0]} to {test[-1]}",
                config.resolve(f"reports/figures/reliability_{r}{sfx}.png"),
            )
    print(t.round(4).to_string(index=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=["fit", "run", "plots"])
    ap.add_argument("--rows", default="CLIM,N0,N1")
    ap.add_argument("--plots", action="store_true", help="reliability figures")
    ap.add_argument(
        "--split",
        choices=["test", "train"],
        default="test",
        help="train = last 5 train days (calibration check, no fitting)",
    )
    a = ap.parse_args()
    cfg = config.load()
    if a.stage == "fit":
        return fit_main(cfg)
    if a.stage == "plots":  # redraw from the saved replay results
        test = [d for d in cfg.splits.test if scores.scores_path(d, cfg).exists()]
        for r in a.rows.split(","):
            df = pd.read_parquet(config.resolve(f"data/derived/bench/nowcast_{r}.parquet"))
            df = df[df["target"] == "aircraft"]
            plots_mod.reliability(
                df["y"].to_numpy(),
                df["p"].to_numpy(),
                f"{r}: aircraft target, test {test[0]} to {test[-1]}",
                config.resolve(f"reports/figures/reliability_{r}.png"),
            )
        return 0
    return run_main(cfg, a.rows.split(","), a.plots, a.split)


if __name__ == "__main__":
    raise SystemExit(main())
