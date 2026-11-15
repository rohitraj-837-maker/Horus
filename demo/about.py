"""Pipeline stages, build status and attributions shown in the demo's About drawer.

Status reflects the real repository state (PROGRESS.md), not the demo.
"""

from __future__ import annotations

from typing import Any

BUILT, CORE, PLANNED = "built", "core built", "planned"

ABOUT: dict[str, Any] = {
    "name": "Horus",
    "tagline": "Turbulence detection and nowcasting from public ADS-B vertical rate, "
    "validated against pilot reports (PIREPs).",
    "disclaimer": "Research prototype. Validated over the continental US only. "
    "Not for operational use. This demo shows synthetic aircraft and synthetic turbulence.",
    "stages": [
        {
            "name": "Ingest",
            "items": [
                {"label": "OpenSky live state vectors (~90 s cadence)", "status": BUILT, "m": "M1"},
                {
                    "label": "OpenSky Trino history (1 Hz)",
                    "status": BUILT,
                    "m": "M1",
                    "note": "query access pending at OpenSky",
                },
                {"label": "adsb.lol daily history (winter 2025–26)", "status": BUILT, "m": "M1"},
                {"label": "PIREPs: IEM archive + AWC", "status": BUILT, "m": "M1"},
                {"label": "Aircraft types (OpenSky DB, ICAO 8643)", "status": BUILT, "m": "M1"},
                {"label": "GFS winds (Open-Meteo)", "status": PLANNED, "m": "M4"},
            ],
        },
        {
            "name": "Detect (per aircraft)",
            "items": [
                {"label": "Level-flight segmentation", "status": CORE, "m": "M2"},
                {
                    "label": "Vertical-rate residual + Sheppard correction (Δ = 64 ft/min)",
                    "status": CORE,
                    "m": "M2",
                },
                {"label": "B0/B1 vertical-acceleration RMS baselines", "status": CORE, "m": "M2"},
                {"label": "Per-aircraft-type normalisation", "status": PLANNED, "m": "M3"},
                {"label": "Calibrated detection threshold", "status": PLANNED, "m": "M3"},
            ],
        },
        {
            "name": "Nowcast (3-D grid)",
            "items": [
                {
                    "label": "H3 res-4 × 2,000 ft cells, log-odds evidence, fitted decay",
                    "status": PLANNED,
                    "m": "M4",
                },
                {"label": "Wind advection + negative evidence", "status": PLANNED, "m": "M5"},
            ],
        },
        {
            "name": "Evaluate",
            "items": [
                {
                    "label": "PIREP matching (±10 min, 40 km, ±2,000 ft)",
                    "status": PLANNED,
                    "m": "M2",
                },
                {
                    "label": "Ablation tables B0–B3, N0–N3; time-split train/test",
                    "status": PLANNED,
                    "m": "M2–M5",
                },
            ],
        },
        {
            "name": "Serve",
            "items": [
                {"label": "This offline demo (synthetic preview)", "status": BUILT, "m": "demo"},
                {"label": "FastAPI + WebSocket live map", "status": PLANNED, "m": "M6"},
            ],
        },
    ],
    "attributions": [
        {
            "name": "OpenSky Network",
            "text": "Live and historical (Trino) state vectors, used under the OpenSky Network "
            "terms of use for academic, non-commercial research. Cite: M. Schäfer, "
            "M. Strohmeier, V. Lenders, I. Martinovic, M. Wilhelm. “Bringing Up OpenSky: "
            "A Large-scale ADS-B Sensor Network for Research.” IPSN 2014, pp. 83–94.",
            "url": "https://opensky-network.org/about/terms-of-use",
        },
        {
            "name": "adsb.lol",
            "text": "Historical traces (adsblol/globe_history), © adsb.lol contributors, "
            "licensed under the Open Database License (ODbL) 1.0.",
            "url": "https://opendatacommons.org/licenses/odbl/1-0/",
        },
        {
            "name": "PIREPs",
            "text": "Iowa Environmental Mesonet PIREP archive (Iowa State University) and "
            "the NOAA Aviation Weather Center Data API.",
            "url": "https://mesonet.agron.iastate.edu/",
        },
        {
            "name": "Natural Earth",
            "text": "State outlines from Natural Earth 1:110m (public domain).",
            "url": "https://www.naturalearthdata.com/",
        },
    ],
    "license": "MIT License · © 2026 Rohit Raj",
}
