# Progress

One entry per milestone gate. A milestone is **complete** only when its `make gate-mN` is fully green.

## M0 — Setup ✅ complete (2026-11-15)

- `make gate-m0`: ruff ✔, mypy (core) ✔, pytest 26/26 ✔, imports ✔.
- CLAUDE.md, config.yaml (all SPEC parameters, strict loader), canonical track schema, uv + Python 3.12.

## Decisions log

- **2026-11-15: M2 benchmark data.** Use adsb.lol daily history (winter 2025–26, ~16–20 s irregular cadence) now, and re-run the same code at 1 Hz when Trino access arrives. Every reported number names its source and rate. (Rohit)
- **2026-11-15: Gate-order exception.** M2's data-independent core code (synth, geo, segment, residual + synthetic tests) may start before the M1 gate passes. **No M2 benchmark rows until `make gate-m1` is green.** (Rohit)

- **2026-11-15: Trino access approved.** Fetch **CONUS** (SPEC), not the WMKK/OKKK airspaces in the pasted request: only US PIREPs give labels. **Milestones first:** Trino feeds the B0–B3 rows at 1 Hz (M2/M3); LightGBM stays B4 (optional, classification metrics only). (Rohit)
- **2026-11-15: Trino fetch design.** `Trino.history()` only (no raw SQL); explicit columns; server-side FL180 filter; dates ≥ 2023; `cached=True` with each hour's cache file deleted after our Parquet is written (otherwise the raw cache doubles disk use); **1-hour chunks, not 6-hour** (~9M rows/h at CONUS scale; 6 h would exceed 8 GB RAM); stale rows (`time − lastcontact > 15 s`) dropped; 20 GB free-disk floor. Full download size will be decided from the smoke-test numbers.
- **2026-11-15: First Trino smoke run refused.** The login worked, but OpenSky returned `PERMISSION_DENIED: Cannot execute query` for every query. This is an account-permission problem on OpenSky's side; no data came back. Fixes: the fetcher stops on the first USER_ERROR instead of retrying 4× per hour (each retry triggered another browser login); the log directory is created; `keyring` caches the browser-login token so one login covers every hour; `--forget-login` clears it. Waiting on OpenSky.
- **2026-11-15: adsb.lol history download finished.** 43 of 45 days, 12 GB, ~510 M rows. Dec 25 failed because that release is one `.tar` file, not split parts (fixed and re-fetched). **Dec 31 has no release in either the 2025 or 2026 repo**, so it is a known gap in the train split; it is reported, not filled.

## M1 — Ingestion ⏳ code done, gate waiting on elapsed time

`make gate-m1 DAY=2026-11-14` (2026-11-15 01:5x AEDT): ruff ✔, mypy ✔, **pytest 110 passed** (1 Trino test deselected until access), then:

| Criterion | Status |
|---|---|
| Full UTC day of CONUS tracks | ❌ recorder started 2026-11-14 22:51 UTC. First complete UTC day is 2026-11-15, available after 2026-11-16 00:00 UTC |
| PIREPs for that day | ✔ (re-run `turbcast.ingest.pireps` for 2026-11-15 once the day closes) |
| DuckDB per-aircraft series | ✔ |
| Recorder survives 24 h | ❌ running (PID in `data/raw/live/recorder.pid`); 24 h reached about 2026-11-15 22:52 UTC |
| Quantisation measured (Open Q1) | ✔ 64 ft/min confirmed on real data |

Re-check: `make gate-m1 DAY=2026-11-15` after 2026-11-16 00:05 UTC (11:05 AEDT).

Built: `ingest/live.py` (OpenSky OAuth2 + adsb.lol tiling, credit-aware pacing, backoff, hourly Parquet), `ingest/pireps.py` (IEM + AWC, free-text TB parser), `ingest/aircraft_db.py`, `ingest/historical.py` (Trino, tested offline), `ingest/store.py`, `ingest/quantisation.py`, `ingest/audit.py`, scripts, `reports/data_notes.md`.

Key findings (details in `reports/data_notes.md`):
- Free live sources allow about **90 s** per-aircraft cadence for CONUS, not the SPEC's 5–10 s.
- 64 ft/min vertical-rate quantisation survives OpenSky processing.
- Aircraft DB is 2 years stale; 28% of aircraft at FL200 or above have no type.
- AWC's own PIREP decoder is inconsistent on mixed intensities; we store max and min.
- Also built after the first gate run: `ingest/adsblol_history.py` (streams adsb.lol daily tar releases, CONUS ≥ FL180 to Parquet with bounded memory), `scripts/fetch_adsblol_history.py`. The IEM PIREP ingest is now resumable with retries; an earlier run had silently stopped partway when IEM dropped a connection.
- Study period fixed: 2025-12-02 → 2026-01-15 (most MOD+ PIREPs); train Dec 2–31, test Jan 1–15. History download started 2026-11-15 ~02:13 AEDT (`data/raw/adsblol_hist/_log.csv`).

## M2 — Segmentation, residual, B0–B2 ⏳ core started under the gate-order exception

Done (synthetic tests only; no benchmark rows):
- `core/geo.py` (haversine, ISA pressure ↔ altitude, FL bins, H3), `core/rolling.py` (NaN-aware centred windows), `core/segment.py` (level flight, trims, sparse-data aware), `core/residual.py` (median trend, flicker-weighted variance, Sheppard, B0/B1 acceleration RMS), `tests/synth.py`.
- `make check`: ruff ✔, mypy strict on core ✔ (5 files), **pytest 162 passed**.
- Verified by tests: injected σ ∈ {40, 64, 100, 150} ft/min recovered within 3% after quantisation (Sheppard helps every case); smooth air with 2% flicker stays below every turbulent window; step-climbs never enter segments and never trigger; sparse 20 s data needs wider windows.

Not started (waits for `make gate-m1`): `eval/match.py`, `eval/negatives.py`, `eval/detector_bench.py`, leakage test, B0–B2 rows.
