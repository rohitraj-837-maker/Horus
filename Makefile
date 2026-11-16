.PHONY: setup test lint types check gate-m0 gate-m1 gate-m2 gate-m3 gate-m4 gate-m5 gate-m6 serve replays latency watch cases watch-sample up readme gif gate-m7 scores bench-detector bench-rates winds bench-nowcast bench record recorder-status trino-smoke demo demo-data bundle gate-demo

setup:
	uv sync

# M7: one-command setup for a fresh checkout: env + the demo day's data (only what is missing)
up:
	uv sync --frozen
	uv run python scripts/setup_data.py

test:
	uv run pytest -q

lint:
	uv run ruff check .

types:
	uv run mypy horus/core horus/eval

check: lint types test

gate-m0: check
	uv run python -c "import horus, h3, duckdb, numpy; print('M0 gate: imports OK')"

# Full day from the Plan B archive by default (SPEC §10 allows it); override e.g.
#   make gate-m1 DAY=2026-11-16 SRC=opensky
gate-m1: check
	uv run python scripts/measure_quantisation.py --day $(or $(DAY),2025-12-02) --source $(or $(SRC),adsblol_hist)
	uv run python scripts/gate_m1.py --day $(or $(DAY),2025-12-02) --source $(or $(SRC),adsblol_hist)

# M2: per-minute detector scores for the study window, then the B0-B2 ablation
scores:
	uv run python scripts/score_days.py --start $(or $(START),2025-12-02) --end $(or $(END),2026-01-15) --workers $(or $(WORKERS),3)

bench-detector:
	uv run python -m horus.eval.detector_bench

gate-m2: check
	uv run python scripts/gate_m2.py

# M3: rate rows (5 s / 10 s scores are cheap: scored on a coarser grid) + window sweep
bench-rates:
	uv run python scripts/score_days.py --start 2025-12-02 --end 2026-01-15 --workers 2 --rate 5
	uv run python scripts/score_days.py --start 2025-12-02 --end 2026-01-15 --workers 2 --rate 10
	uv run python -m horus.eval.detector_bench --rate 1 --windows sweep
	uv run python -m horus.eval.detector_bench --rate 5
	uv run python -m horus.eval.detector_bench --rate 10

gate-m3: check
	uv run python scripts/gate_m3.py

# M4/M5: GFS winds, half-life fit, nowcast replays, grid performance
winds:
	uv run python -m horus.ingest.winds --start 2025-12-02 --end 2026-01-15

bench-nowcast:
	uv run python -m horus.eval.nowcast_bench fit
	uv run python -m horus.eval.nowcast_bench run --rows CLIM,N0,N1,N2,N3 --plots

bench:
	uv run python -m horus.eval.perf_bench

gate-m4: check
	uv run python scripts/gate_nowcast.py m4

gate-m5: check bench
	uv run python scripts/gate_nowcast.py m5

# M6: text stream (in-process live loop), JSON/WS API, curated replays, live latency
#   make watch ARGS="--fl 340"   options: --fl --speed --fast --minutes --loop --day
watch:
	uv run python -m horus.watch $(ARGS)

cases:
	uv run python -m horus.watch --cases

watch-sample:
	uv run python -m horus.watch --fast --minutes 60 --no-color > docs/watch-sample.txt
	uv run python -m horus.watch --cases --fast --no-color > docs/cases-sample.txt

serve:
	uv run uvicorn horus.api.main:app --host 127.0.0.1 --port 8000

replays:
	uv run python scripts/make_replays.py --n 3

latency:
	uv run python scripts/measure_latency.py


gate-m6: check
	uv run python scripts/gate_m6.py

# M7: README numbers regenerated from reports/ (after `bench` refreshes perf.md), demo GIF
readme:
	uv run python scripts/readme_numbers.py --write

gif:
	uv run python scripts/make_demo_gif.py

gate-m7: check bench readme
	uv run python scripts/gate_m7.py

record:
	nohup caffeinate -i uv run python scripts/record_live.py > data/raw/live/nohup-opensky.out 2>&1 & echo $$! > data/raw/live/recorder.pid

recorder-status:
	uv run python scripts/check_recorder.py --hours 24

trino-smoke:
	uv run python scripts/fetch_trino_history.py --smoke-test

# Offline demo (synthetic data, real core); prints the localhost URL
demo:
	uv run python -m demo.server $(if $(PORT),--port $(PORT),)

# Regenerate the bundled synthetic scenarios (deterministic)
demo-data:
	uv run python -m demo.scenarios

# Single-file push bundle: dist/horus-push-bundle.tar.gz + dist/PUSH_MANIFEST.txt
bundle:
	uv run python scripts/package_release.py

# Demo gate: lint, types, demo endpoint/offline tests, bundle + clean-extraction test, clean tree
gate-demo: lint
	uv run mypy horus/core demo scripts/package_release.py
	uv run pytest -q tests/demo
	uv run pytest -q -m bundle
	uv run python scripts/package_release.py
	@test -z "$$(git status --porcelain)" || { echo "gate-demo: FAIL (uncommitted changes)"; git status --short; exit 1; }
	@echo "gate-demo: PASS"
