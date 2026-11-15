# CLAUDE.md — TurbCast

Live turbulence detection and nowcasting from ADS-B vertical rate, validated against PIREPs over the continental US. **`SPEC.md` is the source of truth**; the approved build plan is milestone M0 → M7 (SPEC §10).

## Build rules (non-negotiable)
1. **Milestone gates.** Do not start milestone N+1 until `make gate-mN` passes. Never skip, xfail or loosen a test to make a gate pass; fix the code or raise it with Rohit.
2. **`turbcast/core/` is pure.** No file, network, clock or random-state access inside `core/`. Same functions run offline and live. Time and RNG are passed in as arguments.
3. **No mocking core math.** Core tests use synthetic signals built with real formulas (`tests/synth.py`). Network/ingest code is tested against small **recorded real responses** in `tests/fixtures/`, never hand-invented API shapes.
4. **Time-based splits only.** Train/tune on earlier dates, test on later dates (`config.yaml: splits`). `tests/test_leakage.py` must fail if a test date appears in any fitted artefact.
5. **Every modelling choice gets a benchmark row** in `reports/` (ablation tables, data notes, perf). README numbers must match `reports/`.
6. **float32** for bulk arrays; process data by hour/day with Parquet column pruning (8 GB M1).
7. Prefer simple, vectorised NumPy over abstractions. Numba only when `make bench` shows a step misses budget.
8. Parameters live in `config.yaml`, not in code.
9. After each milestone gate is fully green: log it in `PROGRESS.md` and make a **local** `git commit`. **Never `git push`, never create a remote.** Everything stays in `~/turbcast`.
10. **Never read or print `credentials.json` or `.env`.** Code may load credentials at runtime; logs and errors name keys only, never values.
11. Automated tests never hit live APIs (no credits used): network code is tested with httpx `MockTransport` serving recorded real responses from `tests/fixtures/`.

## Canonical units (after ingest, everywhere)
| Field | Unit | Note |
|---|---|---|
| `time` | int64 epoch seconds (UTC) | |
| `baro_alt_ft` | ft, float32 | OpenSky metres × 3.28084 |
| `vr_fpm` | ft/min, float32 | OpenSky m/s × 196.850; ADS-B step Δ = 64 ft/min (= 0.32512 m/s) |
| `gs_kt` | knots, float32 | OpenSky m/s × 1.943844 |
| `track_deg` | degrees true, float32 | |

Track schema: `icao24, time, lat, lon, baro_alt_ft, vr_fpm, gs_kt, track_deg, on_ground, source, rate_hz` (see `turbcast/schema.py`).

## Layout
```
turbcast/ingest/  network + file I/O → canonical Parquet
turbcast/core/    pure algorithms: geo, segment, residual, normalise, detect, grid
turbcast/eval/    PIREP matching, negatives, benchmarks, plots
turbcast/api/     FastAPI + live loop
web/              MapLibre front end
scripts/          CLI entry points (recorder, fetchers, fits)
reports/          benchmark tables + figures (committed)
data/, models/    gitignored (small JSON in models/ allowed)
```

## Commands
```
uv sync                       # install
uv run pytest -q              # all offline tests
uv run pytest -m trino -q     # Trino integration (needs OpenSky research creds)
uv run ruff check .           # lint
uv run mypy turbcast/core     # types (core only)
make gate-m1 … gate-m7        # milestone acceptance gates
make bench                    # performance benchmark
```

## Status
See `PROGRESS.md` (per-milestone gate log) and `reports/data_notes.md` (data findings).
- Trino access: **approved 2026-11-15**. Fetch with `scripts/fetch_trino_history.py` (`Trino.history()` only, never raw SQL, dates ≥ 2023, explicit columns, 1 h chunks). pyopensky config on macOS: `~/Library/Application Support/pyopensky/settings.conf`.
- Plan B sources (live recorder, adsb.lol history) stay in place.
- Live recorder: `make record`, `make recorder-status`; PID in `data/raw/live/recorder.pid`.
