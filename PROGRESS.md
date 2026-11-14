# Progress

One entry per milestone gate. A milestone is **complete** only when its `make gate-mN` is fully green.

## M0 — Setup ✅ complete (2026-11-15)

- `make gate-m0`: ruff ✔, mypy (core) ✔, pytest 26/26 ✔, imports ✔.
- CLAUDE.md, config.yaml (all SPEC parameters, strict loader), canonical track schema, uv + Python 3.12.

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
