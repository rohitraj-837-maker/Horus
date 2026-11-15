"""Synthetic demo scenarios, scored by the real Horus core.

Nothing here is real flight or PIREP data. Each scenario is one hour of synthetic
aircraft at 1 Hz flying through a synthetic turbulence field (moving elliptical
patches with a known injected sigma). Vertical rate = band-limited turbulence noise
scaled by the local injected sigma + slow autopilot wander + step climbs, quantised
to the 64 ft/min ADS-B step with single-sample flicker, the same physics as
`tests/synth.py`.

Every score shown in the UI comes from the production code path:
`segment.level_flight` -> `segment.mask_outside` -> `residual.residual_stats`
(Sheppard-corrected sigma) and `residual.accel_rms` (B1). Severity colours use
PLACEHOLDER thresholds on sigma_corr until M3 calibrates them on train data.

    uv run python -m demo.scenarios          # regenerate demo/data/scenario-*.json
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from scipy import signal

from horus import config
from horus.core import rolling, segment
from horus.core.residual import ResidualParams, accel_rms, residual_stats
from horus.core.segment import SegmentParams

DATA_DIR = Path(__file__).parent / "data"
DURATION_S = 3600
STRIDE_S = 10  # output stride (core runs at 1 Hz; the UI gets every 10th second)
STEP_FPM = 64.0
KM_PER_DEG = 111.195

# PLACEHOLDER severity edges on sigma_corr (ft/min) -- not calibrated (M3 does that)
SEVERITY_EDGES = (90.0, 180.0, 320.0)
SEVERITY_LABELS = ("Smooth", "Light", "Moderate", "Severe")
BACKGROUND_SIGMA = 22.0  # smooth-air turbulence-band sigma (ft/min)
WANDER_SIGMA = 35.0  # slow autopilot altitude-hold wander (ft/min), removed by the trend
TURB_BAND_HZ = (0.1, 0.4)
WANDER_BAND_HZ = (0.005, 0.05)
SMOOTH_FOR_COLOUR_S = 31  # colour uses a 31 s rolling median of sigma_corr


@dataclass(frozen=True)
class Patch:
    lat: float
    lon: float
    a_km: float  # semi-axis along `angle_deg` (bearing, deg true)
    b_km: float
    angle_deg: float
    fl_lo: float
    fl_hi: float
    sigma: float  # peak injected sigma (ft/min)
    u_kmh: float = 0.0  # eastward drift
    v_kmh: float = 0.0  # northward drift


@dataclass
class Spec:
    id: str
    title: str
    blurb: str
    patches: list[Patch]
    seed: int
    n_aircraft: int = 26
    crossers: float = 0.6  # share of aircraft routed through a patch
    pirep_rate: float = 0.5  # chance an aircraft reports when it meets >= LGT
    neg_reports: int = 3
    extra: dict[str, Any] = field(default_factory=dict)


SPECS: list[Spec] = [
    Spec(
        "jet-cat",
        "Clear-air turbulence — jet stream",
        "A curved jet-stream core from the Great Basin to the Ohio Valley, with shear-driven "
        "CAT bands at FL310–FL390 drifting east.",
        [
            Patch(40.8, -111.0, 260, 70, 100, 310, 390, 240, 55, -5),
            Patch(39.6, -101.5, 300, 80, 98, 320, 390, 330, 55, -5),
            Patch(38.6, -92.0, 260, 70, 80, 310, 380, 280, 55, 5),
            Patch(39.4, -84.5, 200, 60, 65, 320, 380, 200, 55, 10),
        ],
        seed=11,
    ),
    Spec(
        "convective",
        "Convective corridor",
        "A line of thunderstorm cells from central Texas to Tennessee; deep, intense, "
        "compact turbulence FL200–FL410 drifting northeast.",
        [
            Patch(31.0, -97.6, 55, 45, 30, 200, 410, 420, 30, 25),
            Patch(32.4, -94.6, 70, 50, 40, 210, 400, 380, 30, 25),
            Patch(33.6, -91.6, 60, 45, 45, 200, 410, 450, 30, 25),
            Patch(34.9, -88.8, 65, 50, 50, 220, 390, 340, 30, 25),
            Patch(35.9, -86.2, 50, 40, 50, 230, 380, 300, 30, 25),
        ],
        seed=23,
    ),
    Spec(
        "calm",
        "Calm cruise",
        "Smooth air everywhere: the detector should stay quiet. Only quantisation, flicker "
        "and autopilot wander are present.",
        [],
        seed=37,
        crossers=0.0,
        neg_reports=8,
    ),
    Spec(
        "pirep-replay",
        "Synthetic PIREP replay",
        "Mountain-wave turbulence over the Colorado Rockies plus a weaker patch downstream, "
        "with many synthetic pilot reports to exercise the PIREP correlation window.",
        [
            Patch(39.6, -106.2, 120, 70, 0, 240, 380, 410, 15, 0),
            Patch(38.9, -102.4, 110, 60, 20, 280, 360, 210, 25, 0),
        ],
        seed=41,
        crossers=0.75,
        pirep_rate=0.9,
        neg_reports=5,
    ),
]

# Sanity anchors for random routes (lat, lon): CONUS interior
CONUS_BOX = (28.0, 47.0, -121.0, -73.0)


# ------------------------------------------------------------------ helpers


def _band_noise(n: int, band: tuple[float, float], rng: np.random.Generator) -> np.ndarray:
    sos = signal.butter(4, band, btype="band", fs=1.0, output="sos")
    x = signal.sosfiltfilt(sos, rng.normal(size=n))
    return np.asarray(x / x.std())


def _offset(
    lat: float, lon: float, d_east_km: np.ndarray, d_north_km: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    lat2 = lat + d_north_km / KM_PER_DEG
    lon2 = lon + d_east_km / (KM_PER_DEG * math.cos(math.radians(lat)))
    return lat2, lon2


def patch_sigma(
    p: Patch, t: np.ndarray, lat: np.ndarray, lon: np.ndarray, fl: np.ndarray
) -> np.ndarray:
    """Injected sigma (ft/min) of one patch at (t, lat, lon, FL)."""
    clat, clon = _offset(p.lat, p.lon, p.u_kmh * t / 3600.0, p.v_kmh * t / 3600.0)
    dx = (lon - clon) * KM_PER_DEG * np.cos(np.radians(clat))
    dy = (lat - clat) * KM_PER_DEG
    th = math.radians(p.angle_deg)
    along = dx * math.sin(th) + dy * math.cos(th)
    across = dx * math.cos(th) - dy * math.sin(th)
    r2 = (along / p.a_km) ** 2 + (across / p.b_km) ** 2
    # soft vertical edges, 1,000 ft taper
    vert = np.clip(np.minimum(fl - p.fl_lo, p.fl_hi - fl) / 10.0 + 1.0, 0.0, 1.0)
    return np.asarray(p.sigma * np.exp(-0.5 * r2 * 2.0) * vert)


def severity(sigma: np.ndarray) -> np.ndarray:
    """-1 = not assessed (outside level flight), 0..3 = Smooth..Severe (placeholder)."""
    s = np.asarray(sigma, dtype=float)
    out = np.digitize(s, SEVERITY_EDGES).astype(int)
    return np.where(np.isfinite(s), out, -1)


# ------------------------------------------------------------------ build


def _route(spec: Spec, i: int, rng: np.random.Generator) -> dict[str, float]:
    gs = float(rng.uniform(420, 495))
    if spec.patches and rng.random() < spec.crossers:
        p = spec.patches[int(rng.integers(len(spec.patches)))]
        hdg = float((p.angle_deg + rng.choice([0, 180]) + rng.normal(0, 35)) % 360)
        t_cross = float(rng.uniform(600, 3000))
        # where the patch centre will be at t_cross; start so we arrive there then
        clat, clon = _offset(
            p.lat, p.lon, np.array(p.u_kmh * t_cross / 3600), np.array(p.v_kmh * t_cross / 3600)
        )
        d = gs * 1.852 * t_cross / 3600.0
        lat_s, lon_s = _offset(
            float(clat),
            float(clon),
            np.array(-d * math.sin(math.radians(hdg))),
            np.array(-d * math.cos(math.radians(hdg))),
        )
        fl = float(np.round(rng.uniform(p.fl_lo + 10, p.fl_hi - 10) / 10) * 10)
        return {"lat0": float(lat_s), "lon0": float(lon_s), "hdg": hdg, "gs": gs, "fl": fl}
    lat0 = float(rng.uniform(CONUS_BOX[0] + 3, CONUS_BOX[1] - 3))
    lon0 = float(rng.uniform(CONUS_BOX[2] + 4, CONUS_BOX[3] - 4))
    hdg = float((rng.choice([90, 270]) + rng.normal(0, 30)) % 360)
    fl = (
        float(rng.choice([200, 220, 240, 260]))
        if rng.random() < 0.15
        else float(rng.choice(np.arange(300, 420, 10)))
    )
    return {"lat0": lat0, "lon0": lon0, "hdg": hdg, "gs": gs, "fl": fl}


def _aircraft(spec: Spec, i: int, rng: np.random.Generator, cfg: config.Config) -> dict[str, Any]:
    n = DURATION_S
    t = np.arange(n, dtype=float)
    r = _route(spec, i, rng)
    # heading: gentle wander
    hdg = r["hdg"] + 2.0 * _band_noise(n, (0.0005, 0.005), rng)
    gs_kt = r["gs"] + 3.0 * _band_noise(n, (0.001, 0.02), rng)
    step_km = gs_kt * 1.852 / 3600.0
    d_e = np.cumsum(step_km * np.sin(np.radians(hdg)))
    d_n = np.cumsum(step_km * np.cos(np.radians(hdg)))
    lat, lon = _offset(r["lat0"], r["lon0"], d_e, d_n)

    # altitude profile: cruise + optional step climb/descent
    alt = np.full(n, r["fl"] * 100.0)
    vr_profile = np.zeros(n)
    if rng.random() < 0.25:
        start = float(rng.uniform(600, 2800))
        delta = float(rng.choice([-2000.0, 2000.0]))
        rate = 1500.0
        dur = abs(delta) / rate * 60.0
        m = (t >= start) & (t < start + dur)
        vr_profile[m] = math.copysign(rate, delta)
        alt = alt + np.clip(t - start, 0, dur) / 60.0 * math.copysign(rate, delta)

    fl = alt / 100.0
    truth = np.full(n, BACKGROUND_SIGMA)
    for p in spec.patches:
        truth = np.maximum(truth, patch_sigma(p, t, lat, lon, fl))

    turb = _band_noise(n, TURB_BAND_HZ, rng) * truth
    wander = WANDER_SIGMA * _band_noise(n, WANDER_BAND_HZ, rng)
    vr = vr_profile + turb + wander
    alt_obs = 25.0 * np.round((alt + np.cumsum(turb + wander) / 60.0) / 25.0)
    vr_q = STEP_FPM * np.round(vr / STEP_FPM)
    # single-sample +-64 flicker where neighbours are equal
    j = np.arange(1, n - 1)
    flat = j[(vr_q[j - 1] == vr_q[j]) & (vr_q[j + 1] == vr_q[j])]
    pick = flat[rng.random(len(flat)) < 0.01]
    vr_q[pick] += STEP_FPM * rng.choice([-1.0, 1.0], len(pick))
    gs_kt = gs_kt + (truth / 80.0) * _band_noise(n, (0.02, 0.2), rng)

    # ---- production core path
    sp, rp = SegmentParams.from_config(cfg), ResidualParams.from_config(cfg)
    seg = segment.level_flight(alt_obs, vr_q, np.zeros(n, bool), 1.0, sp)
    vr_m = segment.mask_outside(vr_q, seg.seg_id)
    rs = residual_stats(vr_m, 1.0, rp)
    sigma_corr = np.sqrt(rs.var)
    sigma_raw = np.sqrt(rs.var_raw)
    b1 = accel_rms(t, vr_m, 1.0, rp.w_stat_s, rp.min_samples_stat, quant_step_fpm=STEP_FPM)
    colour_sigma = rolling.median(sigma_corr, SMOOTH_FOR_COLOUR_S, 5)

    # descriptive-only telemetry (not used by Horus's detector)
    alt_dev = alt_obs - rolling.median(alt_obs, 121, 30)
    hdg_rate = np.gradient(np.unwrap(np.radians(hdg))) * 180 / math.pi
    gs_sd = np.sqrt(np.maximum(rolling.mean_square(gs_kt - rolling.mean(gs_kt, 61, 30), 61, 30), 0))
    flick = rs.flicker.astype(float)
    flick_win = rolling.mean(flick, 61, 1) * 61

    k = slice(0, n, STRIDE_S)

    def r_(x: np.ndarray, d: int) -> list[float | None]:
        return [None if not np.isfinite(v) else round(float(v), d) for v in x[k]]

    return {
        "callsign": f"SYN{100 + (spec.seed * 7 + i * 13) % 900}",
        "size": "medium_jet" if r["fl"] >= 280 else "medium_prop",
        "cols": {
            "t": [int(x) for x in t[k]],
            "lat": r_(lat, 4),
            "lon": r_(lon, 4),
            "alt": r_(alt_obs, 0),
            "vr": r_(vr_q, 0),
            "gs": r_(gs_kt, 1),
            "hdg": r_(np.mod(hdg, 360), 1),
            "sc": r_(sigma_corr, 1),
            "res": r_(rs.resid, 0),
            "sr": r_(sigma_raw, 1),
            "b1": r_(b1, 3),
            "sev": [int(x) for x in severity(colour_sigma)[k]],
            "seg": [int(x >= 0) for x in seg.seg_id[k]],
            "truth": r_(truth, 1),
            "flk": r_(flick_win, 0),
            "adev": r_(alt_dev, 0),
            "hrate": r_(hdg_rate, 3),
            "gsd": r_(gs_sd, 2),
        },
        "_full": {"sigma_corr": sigma_corr, "truth": truth, "lat": lat, "lon": lon, "fl": fl},
    }


def _pireps(spec: Spec, ac: list[dict[str, Any]], rng: np.random.Generator) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    tb_names = ["NEG", "LGT", "MOD", "SEV"]
    for a in ac:
        f = a["_full"]
        sev_truth = np.digitize(f["truth"], SEVERITY_EDGES)
        hit = np.flatnonzero(sev_truth >= 1)
        if len(hit) and rng.random() < spec.pirep_rate:
            # report from the bumpiest second, with real-world-like position/time error
            i = int(hit[np.argmax(f["truth"][hit])])
            out.append(_pirep(a, f, i, tb_names[int(sev_truth[i])], rng))
    for _ in range(spec.neg_reports):
        a = ac[int(rng.integers(len(ac)))]
        f = a["_full"]
        smooth = np.flatnonzero(f["truth"] <= BACKGROUND_SIGMA + 1)
        if len(smooth):
            out.append(_pirep(a, f, int(rng.choice(smooth)), "NEG", rng))
    return sorted(out, key=lambda p: p["t"])


def _pirep(
    a: dict[str, Any], f: dict[str, Any], i: int, tb: str, rng: np.random.Generator
) -> dict[str, Any]:
    lat, lon = _offset(
        float(f["lat"][i]),
        float(f["lon"][i]),
        np.array(rng.normal(0, 8)),
        np.array(rng.normal(0, 8)),
    )
    t = int(min(DURATION_S - 1, i + rng.uniform(30, 240)))  # reported a little late
    fl = int(round(float(f["fl"][i]) / 10) * 10)
    ns, ew = ("N" if lat >= 0 else "S"), ("W" if lon < 0 else "E")
    text = (
        f"SYNTHETIC UA /OV {abs(float(lat)):.1f}{ns} {abs(float(lon)):.1f}{ew} "
        f"/TM +{t // 60:02d}:{t % 60:02d} /FL{fl:03d} /TP {a['size'].upper()} /TB {tb}"
    )
    return {
        "t": t,
        "lat": round(float(lat), 3),
        "lon": round(float(lon), 3),
        "fl": fl,
        "tb": tb,
        "by": a["callsign"],
        "text": text,
    }


def build(spec: Spec, cfg: config.Config | None = None) -> dict[str, Any]:
    cfg = cfg or config.load()
    rng = np.random.default_rng(spec.seed)
    ac = [_aircraft(spec, i, rng, cfg) for i in range(spec.n_aircraft)]
    pireps = _pireps(spec, ac, rng)

    # synthetic self-check: recovered vs injected sigma where truth is clearly turbulent
    ratios = np.concatenate(
        [
            a["_full"]["sigma_corr"][a["_full"]["truth"] >= 150]
            / a["_full"]["truth"][a["_full"]["truth"] >= 150]
            for a in ac
        ]
    )
    ratios = ratios[np.isfinite(ratios)]
    smooth = np.concatenate([a["_full"]["sigma_corr"][a["_full"]["truth"] <= 30] for a in ac])
    smooth = smooth[np.isfinite(smooth)]
    for a in ac:
        a.pop("_full")
    m = cfg.match
    return {
        "id": spec.id,
        "title": spec.title,
        "blurb": spec.blurb,
        "synthetic": True,
        "duration_s": DURATION_S,
        "stride_s": STRIDE_S,
        "severity": {
            "edges": list(SEVERITY_EDGES),
            "labels": list(SEVERITY_LABELS),
            "placeholder": True,
            "colour_median_s": SMOOTH_FOR_COLOUR_S,
        },
        "match": {"window_s": m.window_s, "dist_km": m.dist_km, "alt_ft": m.alt_ft},
        "core": {
            "w_trend_s": cfg.residual.w_trend_s,
            "w_stat_s": cfg.residual.w_stat_s,
            "step_fpm": STEP_FPM,
            "sheppard": cfg.residual.sheppard,
        },
        "patches": [p.__dict__ for p in spec.patches],
        "aircraft": ac,
        "pireps": pireps,
        "check": {
            "turbulent_samples": int(len(ratios)),
            "median_recovered_over_injected": round(float(np.median(ratios)), 3)
            if len(ratios)
            else None,
            "smooth_samples": int(len(smooth)),
            "smooth_p95_sigma_corr": round(float(np.percentile(smooth, 95)), 1)
            if len(smooth)
            else None,
        },
    }


def write_all(out_dir: Path = DATA_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for spec in SPECS:
        path = out_dir / f"scenario-{spec.id}.json"
        path.write_text(json.dumps(build(spec), separators=(",", ":")))
        paths.append(path)
    index = [{"id": s.id, "title": s.title, "blurb": s.blurb} for s in SPECS]
    (out_dir / "scenarios.json").write_text(json.dumps(index, indent=1))
    return paths


if __name__ == "__main__":
    for p in write_all():
        d = json.loads(p.read_text())
        print(
            f"{p.name}: {p.stat().st_size / 1e6:.2f} MB, {len(d['aircraft'])} aircraft, "
            f"{len(d['pireps'])} PIREPs, check {d['check']}"
        )
