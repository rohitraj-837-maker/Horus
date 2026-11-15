"""Landing-page content: KPI cards, pipeline stepper, maths highlights, benchmark plan.

KPIs are COMPUTED here from the bundled synthetic scenarios (demo/data), never typed
in. They measure the real core on synthetic truth, so they are labelled "synthetic"
and say what they do and do not show. Real benchmark rows arrive with M2/M3.
Maths parameters come from config.yaml, the same values the detector uses.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from horus import config

DATA = Path(__file__).parent / "data"
TURBULENT = ("jet-cat", "convective", "pirep-replay")


def _load(sid: str) -> dict[str, Any]:
    return dict(json.loads((DATA / f"scenario-{sid}.json").read_text()))


def _hav_km(a1: float, o1: float, a2: float, o2: float) -> float:
    r = math.pi / 180
    h = (
        math.sin((a2 - a1) * r / 2) ** 2
        + math.cos(a1 * r) * math.cos(a2 * r) * math.sin((o2 - o1) * r / 2) ** 2
    )
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def pirep_correlation(scenarios: list[dict[str, Any]]) -> tuple[int, int]:
    """(matched, total) synthetic MOD+ PIREPs with a >= Moderate detection by any
    aircraft inside the SPEC §6.1 window (time, distance, altitude)."""
    matched = total = 0
    for d in scenarios:
        m = d["match"]
        for p in d["pireps"]:
            if p["tb"] not in ("MOD", "SEV"):
                continue
            total += 1
            matched += any(
                abs(t - p["t"]) <= m["window_s"]
                and sev >= 2
                and alt is not None
                and abs(alt - p["fl"] * 100) <= m["alt_ft"]
                and _hav_km(lat, lon, p["lat"], p["lon"]) <= m["dist_km"]
                for a in d["aircraft"]
                for t, sev, alt, lat, lon in zip(
                    a["cols"]["t"],
                    a["cols"]["sev"],
                    a["cols"]["alt"],
                    a["cols"]["lat"],
                    a["cols"]["lon"],
                    strict=True,
                )
            )
    return matched, total


def _col(d: dict[str, Any], key: str) -> np.ndarray:
    return np.array([v for a in d["aircraft"] for v in a["cols"][key] if v is not None], float)


@lru_cache(maxsize=1)
def kpis() -> list[dict[str, Any]]:
    turb = [_load(s) for s in TURBULENT]
    calm = _load("calm")
    matched, total = pirep_correlation(turb)
    ratios = [d["check"]["median_recovered_over_injected"] for d in turb]
    sr, sc = _col(calm, "sr"), _col(calm, "sc")
    light = calm["severity"]["edges"][0]
    fa_corr = float((sc >= light).mean())
    fa_raw = float((sr >= light).mean())
    return [
        {
            "label": "PIREP Correlation Rate",
            "short": "of synthetic pilot reports matched by a detection",
            "value": f"{matched / total:.0%}" if total else "—",
            "detail": f"{matched} / {total} synthetic MOD+ reports matched by a ≥ Moderate "
            f"detection (±{turb[0]['match']['window_s'] // 60} min, "
            f"{turb[0]['match']['dist_km']:.0f} km, ±{turb[0]['match']['alt_ft']:,.0f} ft)",
            "caveat": "Synthetic PIREPs come from the injected field: this checks the matching "
            "pipeline, not real-world skill.",
        },
        {
            "label": "σ_corr Recovery",
            "short": "of the injected turbulence strength recovered",
            "value": f"{min(ratios):.2f}–{max(ratios):.2f}",
            "detail": "median recovered ÷ injected σ in turbulent air, three scenarios",
            "caveat": "Synthetic band-limited turbulence, quantised to 64 ft/min.",
        },
        {
            "label": "σ_corr Calm-Air Baseline",
            "short": "jitter left in smooth air after the correction",
            "value": f"{np.percentile(sc, 95):.0f}",
            "unit": "ft/min p95",
            "detail": f"raw p95 {np.percentile(sr, 95):.0f} · median {np.median(sr):.1f} → "
            f"{np.median(sc):.1f} ft/min after Δ²/12 "
            f"({(np.median(sc) / np.median(sr) - 1):+.0%})",
            "caveat": "Calm Cruise scenario: quantisation, flicker and autopilot wander only.",
        },
        {
            "label": "Calm-Air False-Alarm Rate",
            "short": "of smooth-air samples wrongly flagged",
            "value": f"{fa_corr:.3%}",
            "detail": f"level samples ≥ Light ({light:.0f} ft/min) in calm air; raw σ {fa_raw:.3%}",
            "caveat": "At this threshold Sheppard changes the rate little; its effect is the "
            "calm-air bias reduction in the baseline card. Real false-alarm rates per "
            "1,000 flight-hours come in M2/M3.",
        },
    ]


def maths() -> list[dict[str, Any]]:
    cfg = config.load()
    r, s, step = cfg.residual, cfg.segment, cfg.units.vr_step_fpm
    w = f"{s.window_s:.0f}s"
    return [
        {
            "title": "Level-flight segmentation",
            "formula": f"alt ≥ {s.min_alt_ft:,.0f} ft ∧ range_{w}(alt) ≤ "
            f"{s.max_alt_range_ft:.0f} ft ∧ |median_{w}(vr)| ≤ "
            f"{s.max_abs_median_vr_fpm:.0f} ft/min",
            "note": f"Runs trimmed {s.trim_s:.0f} s each end so windows never straddle a "
            "climb or descent.",
        },
        {
            "title": "Vertical-rate residual",
            "formula": f"r(t) = vr(t) − median_{r.w_trend_s:.0f}s(vr)",
            "note": "A rolling median removes the slow autopilot trend but keeps "
            "turbulence-band jitter.",
        },
        {
            "title": "Flicker-weighted variance",
            "formula": f"σ²_raw = Var_{r.w_stat_s:.0f}s(r; w),  w = {r.flicker_weight} on ±Δ "
            "single-sample flicker",
            "note": "One-sample toggles between adjacent 64 ft/min steps are encoder "
            "noise, so they count half.",
        },
        {
            "title": "Sheppard quantisation correction",
            "formula": f"σ²_corr = max(σ²_raw − Δ²/12, 0),  Δ = {step:.0f} ft/min → "
            f"Δ²/12 = {step**2 / 12:.1f} (ft/min)²",
            "note": "ADS-B vertical rate is rounded to 64 ft/min. Rounding adds variance "
            "Δ²/12; subtracting it removes the bias. Valid for σ ≳ 40 ft/min.",
            "highlight": True,
        },
        {
            "title": "B1 vertical-acceleration RMS",
            "formula": "a_rms = √(mean(a²) − 2Δ²/12/Δt²),  a = Δvr/Δt",
            "note": "Baseline detector: differencing doubles the quantisation noise, so "
            "its share is removed too.",
        },
    ]


PIPELINE = [
    {
        "icon": "antenna",
        "title": "OpenSky",
        "detail": "Live state vectors ~90 s · Trino history 1 Hz",
        "status": "built",
        "note": "Trino query access pending",
    },
    {
        "icon": "archive",
        "title": "adsb.lol history",
        "detail": "Winter 2025–26 · ~20 s cadence",
        "status": "built",
        "note": "44 / 45 days",
    },
    {
        "icon": "table",
        "title": "Parquet store",
        "detail": "Canonical units · hourly partitions · DuckDB",
        "status": "built",
    },
    {
        "icon": "wave",
        "title": "Segment · Residual · Δ²/12",
        "detail": "Level flight, vr residual, Sheppard, B1 accel",
        "status": "core built",
    },
    {
        "icon": "gauge",
        "title": "Horus detector",
        "detail": "Per-type normalised score + calibrated threshold",
        "status": "planned",
        "note": "M3",
    },
]

BENCHMARKS = {
    "columns": ["Row", "Method", "PR-AUC", "Recall @ 1 FA / 1,000 h", "Status"],
    "rows": [
        ["B0", "Vertical-acceleration RMS, fixed threshold", None, None, "M2"],
        ["B1", "B0 + quantisation correction", None, None, "M2"],
        ["B2", "B1 + level segmentation + vr residual (σ_corr)", None, None, "M2"],
        ["B3", "B2 + per-aircraft-type normalisation", None, None, "M3"],
    ],
    "note": "Real PIREP-validated rows (time split: train Dec 2–31, test Jan 1–15) are filled "
    "in after the M1 gate. Cells stay empty until then; no number here is estimated.",
}


PROBLEM = [
    {
        "title": "Invisible to radar",
        "text": "Clear-air turbulence has no clouds or precipitation, so weather radar "
        "cannot see it.",
    },
    {
        "title": "Reports are sparse and late",
        "text": "Crews mostly learn about it from other pilots' verbal reports (PIREPs): sparse, "
        "delayed and subjective.",
    },
    {
        "title": "Every airliner is already a sensor",
        "text": "Each aircraft broadcasts its vertical rate over ADS-B about once a second. "
        "Rough air makes that signal jitter.",
    },
]

READ_GUIDE = [
    {
        "title": "Colour = roughness",
        "text": "Green, yellow, orange, red: the Sheppard-corrected vertical-rate jitter "
        "(σ_corr), on placeholder thresholds until M3. Grey means climbing or descending, "
        "where Horus does not score.",
    },
    {
        "title": "Trail = last 10 minutes",
        "text": "Each trail keeps the colour it had at that moment, so you can see where a "
        "flight entered and left rough air.",
    },
    {
        "title": "Rings = pilot reports",
        "text": "Synthetic PIREPs fade over 30 minutes. The dashed blue ring around the "
        "selected flight is the SPEC matching radius (40 km).",
    },
]

# Status as recorded in PROGRESS.md on 2026-11-15
MILESTONES = [
    {"m": "M0", "title": "Setup", "status": "done", "note": "uv, config, schema, gates"},
    {
        "m": "M1",
        "title": "Ingestion",
        "status": "active",
        "note": "Gate passes after 24 h recorder run",
    },
    {
        "m": "M2",
        "title": "Segment, residual, B0–B2",
        "status": "active",
        "note": "Core built; benchmarks after M1",
    },
    {
        "m": "M3",
        "title": "Type normalisation, B3",
        "status": "planned",
        "note": "Calibrated thresholds",
    },
    {
        "m": "M4",
        "title": "Nowcast grid + winds",
        "status": "planned",
        "note": "Fitted decay half-life",
    },
    {
        "m": "M5",
        "title": "Advection, negative evidence",
        "status": "planned",
        "note": "make bench < 2 s",
    },
    {"m": "M6", "title": "Live API + map", "status": "planned", "note": "< 10 s end to end"},
    {
        "m": "M7",
        "title": "Hardening + demo",
        "status": "planned",
        "note": "CI, README numbers, deploy",
    },
]
MILESTONES_AS_OF = "2026-11-15"

LIMITATIONS = [
    "Everything in this demo is synthetic. Real PIREP-validated numbers arrive after the M1 gate.",
    "Severity thresholds (90 / 180 / 320 ft/min) are placeholders until M3 calibrates them on "
    "the training month.",
    "ADS-B rounds vertical rate to 64 ft/min. Below about 40 ft/min the correction over-corrects, "
    "so small values only mean “near zero”.",
    "Only level flight is scored; climbs and descents are not assessed.",
    "Real feeds are coarser than the demo: live OpenSky about every 90 s, adsb.lol history about "
    "every 20 s. 1 Hz needs OpenSky Trino, where query access is still pending.",
    "Continental US only, because the validation labels (US PIREPs) exist only there.",
]


@lru_cache(maxsize=1)
def scenario_cards() -> list[dict[str, Any]]:
    index = json.loads((DATA / "scenarios.json").read_text())
    out = []
    for s in index:
        d = _load(s["id"])
        rec = d["check"]["median_recovered_over_injected"]
        out.append(
            {
                "id": s["id"],
                "title": s["title"],
                "blurb": s["blurb"],
                "aircraft": len(d["aircraft"]),
                "pireps": len(d["pireps"]),
                "mod_plus": sum(p["tb"] in ("MOD", "SEV") for p in d["pireps"]),
                "patches": len(d["patches"]),
                "recovery": rec,
            }
        )
    return out


def overview() -> dict[str, Any]:
    return {
        "problem": PROBLEM,
        "kpis": kpis(),
        "pipeline": PIPELINE,
        "maths": maths(),
        "scenarios": scenario_cards(),
        "read_guide": READ_GUIDE,
        "benchmarks": BENCHMARKS,
        "milestones": MILESTONES,
        "milestones_as_of": MILESTONES_AS_OF,
        "limitations": LIMITATIONS,
    }
