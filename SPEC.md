# Horus — SPEC

Live turbulence detection and nowcasting from public aircraft broadcasts (ADS-B), validated against pilot reports (PIREPs) over the continental US.

> **Builder note (for Claude Code):** build milestone by milestone (§10). Each milestone has acceptance criteria; do not start the next one until they pass. Prefer simple, explainable, vectorised code over clever abstractions. Every modelling choice must have a benchmark row in `reports/`.

---

## 1. Problem

Turbulence is the leading cause of in-flight injuries (e.g. Singapore Airlines SQ321, May 2024: one death, dozens injured). Clear-air turbulence (CAT) is invisible to weather radar. Crews learn about it mainly from other pilots' verbal reports (PIREPs), which are sparse, late and subjective.

Every airliner broadcasts its vertical rate over ADS-B about once a second. When an aircraft flies through turbulence, that signal jitters. **Horus turns thousands of aircraft into a live turbulence sensor network** and builds a 3D map of *where turbulence is right now and how confident we are*.

**Novelty (checked 2026-11-15):** the FAA is researching ADS-B vertical-rate turbulence (beta planned for 2026) and Aireon has a commercial white paper. No open, real-time, end-to-end system with public benchmarks was found.

## 2. Goals / non-goals

**Goals**
1. Detect turbulence per aircraft from ADS-B vertical rate, robust to quantisation and manoeuvres.
2. Normalise for aircraft type (an A380 and an A320 respond differently to the same air).
3. Maintain a live 3D nowcast grid (lat/lon × flight level) with time decay, wind drift and negative evidence.
4. Benchmark everything against PIREPs over the continental US, at **both** full rate (~1 Hz) and live-snapshot rate (5–10 s).
5. Live web map that updates in **< 10 s** for ~10k aircraft on an 8 GB M1 laptop.

**Non-goals**
- Operational or certified use (research prototype; say so in the README and UI).
- Forecasting turbulence hours ahead from weather models (this is a *nowcast* from observations).
- Running our own radio receivers (optional stretch only).

## 3. Data sources

| Source | Use | Access | Notes |
|---|---|---|---|
| **OpenSky historical (Trino)**, `state_vectors_data4` | Full-rate (~1 Hz) training + evaluation | Research account (apply with Monash credentials) via `pyopensky` | Primary dataset. Contains `vertrate`, `baroaltitude`, `geoaltitude`, `velocity`, `heading`, `onground`, `icao24` |
| **OpenSky live REST API** `/states/all` | Live demo | Free account; check current rate-limit credits | ~5–10 s snapshots; bounding box = CONUS |
| **PIREPs, historical** | Ground truth | Iowa Environmental Mesonet PIREP archive (CSV) | Verify fields: time, lat/lon, flight level, turbulence intensity, aircraft type |
| **PIREPs, live** | Live overlay | aviationweather.gov Data API (`/api/data/pirep`) | Recent window only |
| **GFS upper-level winds** | Advection | NOAA GFS (AWS open data / NOMADS) or Open-Meteo pressure-level API | u/v at 300, 250, 200 hPa; 0.25°; refresh every 6 h |
| **Aircraft type lookup** | Per-type normalisation | OpenSky aircraft database (icao24 → typecode) | Group into size classes when a type has too few samples |

**Plan B (if Trino access is slow or denied):** from day 1, run `scripts/record_live.py` to archive the live API to Parquet; investigate community feeds (adsb.lol, airplanes.live) for higher-rate data. All evaluation code must work on either source.

**Study period:** pick ~30–60 days with many moderate-or-greater (MOD+) PIREPs (e.g. US winter jet-stream months). Split by **time**: train/tune on the earlier days, test on later held-out days. Never split randomly.

## 4. Architecture

```
            OFFLINE (research)                          ONLINE (live demo)
OpenSky Trino ─► ingest/historical.py ─► Parquet     OpenSky REST ─► ingest/live.py (asyncio)
IEM PIREPs ───► ingest/pireps.py ─────► Parquet                         │
GFS winds ────► ingest/winds.py ──────► Zarr/NPZ                         ▼
                      │                                   in-memory AircraftState ring buffers
                      ▼                                                  │
        ┌───────── core (shared by offline + online) ─────────┐◄────────┘
        │ segment.py   level-flight segmentation              │
        │ residual.py  trend removal + quantisation correction│
        │ normalise.py per-type sensitivity model             │
        │ detect.py    turbulence index + event extraction    │
        │ grid.py      log-odds nowcast grid (decay, advect,  │
        │              positive/negative evidence)            │
        └─────────────────────────────────────────────────────┘
                      │                                   │
             eval/ benchmarks ─► reports/          api/ FastAPI (REST + WebSocket)
                                                          │
                                                   web/ MapLibre live map
```

**Key rule:** the `core/` package is pure, deterministic and identical for offline and live. No I/O inside `core/`.

**Storage:** Parquet + DuckDB for offline analysis (laptop-friendly, no server). Live state stays in memory (NumPy arrays). No Postgres/Redis unless profiling proves it is needed.

## 5. Core algorithms

### 5.1 Level-flight segmentation (`segment.py`)
- Resample each aircraft track to a uniform time base (1 s offline; native rate live).
- A segment is "level cruise" if, over a sliding 120 s window: altitude ≥ FL200, altitude range ≤ 300 ft, |median vertical rate| ≤ 200 ft/min, not on ground.
- Drop the first/last 30 s of each segment (step-climb transitions).
- Parameters live in `config.yaml`. Report the share of flight time that qualifies.

### 5.2 Residual + quantisation correction (`residual.py`)
- Intended path = rolling median of vertical rate (window `W_trend`, default 31 s).
- Residual `r(t) = vr(t) − trend(t)`.
- Turbulence statistic over window `W_stat` (default 15 s): `σ²_r` = variance of `r`.
- **Quantisation correction (Sheppard):** ADS-B vertical rate has step Δ = 64 ft/min, so subtract Δ²/12 from `σ²_r` (floor at 0). Ablate with and without it.
- **Flicker suppression:** single-sample ±1-step toggles are down-weighted (count them; report how often they occur).
- Also compute a vertical-acceleration RMS proxy (`diff(vr)/dt`) as the "textbook formula" baseline.

### 5.3 Per-type normalisation (`normalise.py`)
- For each aircraft type (or size class if fewer than `N_min` = 200 smooth segments): fit the distribution of `σ_r` in **smooth air**, i.e. segments with no PIREP within ±2 h / 150 km / ±4,000 ft in busy airspace.
- Turbulence index `T` = how extreme `σ_r` is relative to that type's smooth-air distribution (robust z-score from median/MAD, or empirical percentile).
- Store per-type parameters in `models/type_norm.parquet`.

### 5.4 Detection + events (`detect.py`)
- Aircraft-level detection: `T ≥ τ_detect` (tuned on the train split for a target false-alarm rate).
- **Sensitivity weight** per observation, `w = f(type sensitivity, data rate)`; e.g. lower for heavy aircraft and for 5–10 s snapshot data. Used by the grid.
- Optional **phase 2:** a LightGBM classifier trained on weak labels (confident residual detections + PIREP matches) to also cover climb/descent. Must beat the residual detector on the held-out split to be kept.

### 5.5 Nowcast grid (`grid.py`): log-odds occupancy grid
- Cells: H3 resolution 4 (~22 km edge) × flight-level bins of 2,000 ft from FL200 to FL450.
- Each cell stores log-odds `L` that turbulence (MOD+) is present, plus last-update time.
- **Time decay (rule 1):** `L ← L₀ + (L − L₀)·exp(−ln2 · Δt / t½)`. Fit `t½` from data (how quickly a detection stops predicting the next aircraft's detection in the same cell). Starting value 12 min. Report the fitted value per altitude band.
- **Wind advection (rule 2):** every `advect_interval` (60 s), shift cell contents along GFS winds at the matching pressure level (FL300≈300 hPa, FL340≈250 hPa, FL390≈200 hPa) using a semi-Lagrangian step. Cells over major mountain ranges can be flagged `terrain_locked` (no advection). Ablate on vs off.
- **Positive evidence:** `L += w · ℓ_pos(T)`.
- **Negative evidence (rule 3):** a smooth pass through a cell gives `L −= w · ℓ_neg`. Two or more confident smooth passes should bring an alert below the display threshold, but never hard-delete it.
- Clamp `L` to `[L_min, L_max]`. Output `p = sigmoid(L)` per cell.
- Optional comparison model: local Gaussian process over (lat, lon, FL, t). Keep only if it beats the grid on calibration and is fast enough.

## 6. Evaluation (`eval/`)

### 6.1 PIREP matching
- Positive = PIREP with intensity ≥ MOD (also report LGT+ separately).
- Match window: ±10 min, ±40 km, ±2,000 ft (configurable).
- **Event-level scoring:** a PIREP counts as detected if any detection or grid cell with `p ≥ θ` falls inside its window.
- **Negatives:** space-time windows in busy airspace (≥ N aircraft passes) with no PIREP of any intensity within ±2 h / 150 km. Note in the README that absence of a PIREP is weak evidence.
- **Window sensitivity:** rerun all headline metrics at ±5 / ±10 / ±20 min and ±20 / ±40 / ±80 km.

### 6.2 Detector ablation table (held-out test split)

| ID | Method |
|---|---|
| B0 | Textbook: RMS vertical acceleration + fixed threshold |
| B1 | B0 + quantisation correction |
| B2 | B1 + level-flight segmentation + residual |
| B3 | B2 + per-type normalisation |
| B4 | B3 + weak-label classifier (optional) |

Metrics: PR-AUC, recall at 1 false alarm per 1,000 flight-hours, precision at fixed recall. Run every row at **full rate** and at **downsampled 5 s and 10 s** (simulating live), to quantify the data-rate gap.

### 6.3 Nowcast ablation table

| ID | Method |
|---|---|
| N0 | Persistence: last detection in cell, no decay |
| N1 | + exponential decay (fitted t½) |
| N2 | + wind advection |
| N3 | + negative evidence |
| N4 | Local GP (optional comparison) |

Metrics, scored against **future** aircraft and PIREPs (predict at time t, check t … t+15 min): Brier score, reliability diagram / expected calibration error, PR-AUC.

### 6.4 Performance
`make bench` reports: ingest throughput (aircraft/s), detection time per update, grid update time, end-to-end live map latency (p50/p95). **Target: < 10 s map update for 10k aircraft on an M1 8 GB.**

## 7. API + front end

- **FastAPI**
  - `GET /api/grid?fl=350` → cells with `p` (GeoJSON or compact array)
  - `GET /api/aircraft` → current aircraft with turbulence index
  - `GET /api/pireps?since=` → recent PIREPs
  - `WS /ws/live` → push grid diffs + aircraft every update
  - `GET /api/replay/{event_id}` → historical full-rate replay
  - `GET /api/health`, `/api/metrics` (latency counters)
- **Web (MapLibre GL, plain TS or vanilla JS):**
  - Flight-level slider (FL200–FL450); cells coloured by `p`
  - Aircraft dots coloured by turbulence index; PIREP markers overlaid
  - Replay mode for curated historical events, at full rate
  - Banner: "Research prototype. Validated over continental US only. Not for operational use."

## 8. Repository layout

```
horus/
  SPEC.md  README.md  config.yaml  pyproject.toml  Makefile  docker-compose.yml
  horus/
    ingest/   historical.py live.py pireps.py winds.py aircraft_db.py
    core/     segment.py residual.py normalise.py detect.py grid.py geo.py
    eval/     match.py negatives.py detector_bench.py nowcast_bench.py plots.py
    api/      main.py schemas.py live_loop.py
  web/        index.html app.js style.css
  scripts/    record_live.py fetch_study_period.py make_replays.py
  models/     (fitted params, gitignored except small JSON)
  data/       (gitignored: raw/, parquet/, winds/)
  reports/    detector_ablation.md nowcast_ablation.md perf.md figures/
  tests/      unit tests per core module + synthetic-signal tests
```

**Tooling:** Python 3.12, `uv`, NumPy, pandas/Polars, DuckDB, SciPy, Numba (only where profiling says so), h3, xarray (winds), LightGBM (phase 2), FastAPI, uvicorn, httpx, pytest, ruff, mypy (core only), GitHub Actions CI.

## 9. Testing

- **Synthetic tests for `core/`:** generate a level track, inject known turbulence (band-limited noise of known variance), quantise to 64 ft/min, and assert that the detector recovers it. Assert that step-climbs and quantisation flicker alone do *not* trigger.
- **Grid tests:** decay halves `L − L₀` after exactly `t½`; advection moves a blob the expected distance; two smooth passes push an alert below threshold.
- **Leakage test:** fail CI if any test-split date appears in tuning data.
- **Live loop:** a recorded 10-minute replay through `live_loop.py` produces identical output to the offline pipeline on the same data.

## 10. Milestones (7 weeks)

| Wk | Deliverable | Acceptance criteria |
|---|---|---|
| 1 | Ingestion: OpenSky historical (or Plan B archive), PIREPs, aircraft DB → Parquet. Live recorder running. | One full day of CONUS tracks + PIREPs loaded; DuckDB query returns per-aircraft 1 Hz series; recorder survives 24 h |
| 2 | `segment.py`, `residual.py`, B0–B2 + PIREP matching | Synthetic tests pass; B0/B1/B2 rows in `detector_ablation.md` |
| 3 | `normalise.py`, B3, rate-downsampling eval, window sensitivity | B3 row at 1 Hz / 5 s / 10 s; sensitivity table; per-type params saved |
| 4 | `grid.py` N0–N1 + fitted half-life; GFS ingestion | N0/N1 rows; fitted t½ reported per altitude band |
| 5 | Advection (N2) + negative evidence (N3); `make bench` | N2/N3 rows + reliability plots; grid update < 2 s for 10k aircraft |
| 6 | FastAPI + WebSocket live loop + MapLibre UI + replay mode | Live CONUS map updating < 10 s end-to-end (p95); 3 curated replays |
| 7 | Hardening: CI, README with figures, demo video, deploy | One-command setup (`make up`); README headline numbers match `reports/`; public demo link |

**Stretch (only after week 7 criteria pass):** B4 weak-label classifier, N4 local GP, terrain-locked cells, RTL-SDR Melbourne feed, OpenSky Symposium write-up.

## 11. Risks

| Risk | Mitigation |
|---|---|
| Trino access delayed or denied | Plan B archive from day 1; all code source-agnostic |
| Train/serve mismatch (1 Hz vs 5–10 s) | Benchmark both rates; live UI states which model runs; replay shows full rate |
| PIREP noise and position error | Event-level windows, sensitivity analysis, honest caveats |
| Absence of a PIREP ≠ smooth air | Negatives only from busy airspace; report as a limitation |
| Laptop memory (8 GB) | Process by day and region; Parquet column pruning; float32 everywhere |
| Live API rate limits | Single CONUS bounding-box poll; backoff; cache last state |
| Scope creep | Stretch list is frozen until week 7 passes |

## 12. Ethics and licensing
Public data only; respect each provider's terms and rate limits; cite OpenSky as its terms require. Aircraft identifiers are already public, but the UI shows no personal data beyond callsign/type. Clear "not for operational use" disclaimer.

## 13. Headline results to report (README template)
- Detector: PR-AUC and recall at 1 FA / 1,000 flight-hours for B0 → B3 at 1 Hz and 10 s.
- Nowcast: Brier score and calibration error for N0 → N3; fitted decay half-life.
- Live system: aircraft processed, p95 map latency.
- Honest limitations section.

## 14. Open questions (resolve during week 1)
1. Exact column names and units of `vertrate` in `state_vectors_data4` (m/s vs ft/min), and whether the 64 ft/min quantisation survives OpenSky's processing. Measure the step size empirically from the data.
2. The IEM PIREP archive's field format for turbulence intensity and altitude ranges (e.g. "MOD CAT FL330-370").
3. OpenSky live API rate limits for a registered account at CONUS scale.
