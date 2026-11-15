.PHONY: setup test lint types check gate-m0 gate-m1 record recorder-status trino-smoke

setup:
	uv sync

test:
	uv run pytest -q

lint:
	uv run ruff check .

types:
	uv run mypy turbcast/core

check: lint types test

gate-m0: check
	uv run python -c "import turbcast, h3, duckdb, numpy; print('M0 gate: imports OK')"

# DAY defaults to yesterday (UTC); override: make gate-m1 DAY=2026-11-15
gate-m1: check
	uv run python scripts/measure_quantisation.py --day $(or $(DAY),$(shell date -u -v-1d +%F))
	uv run python scripts/gate_m1.py $(if $(DAY),--day $(DAY),)

record:
	nohup caffeinate -i uv run python scripts/record_live.py > data/raw/live/nohup-opensky.out 2>&1 & echo $$! > data/raw/live/recorder.pid

recorder-status:
	uv run python scripts/check_recorder.py --hours 24

trino-smoke:
	uv run python scripts/fetch_trino_history.py --smoke-test
