.PHONY: setup test lint types check gate-m0 gate-m1 record recorder-status trino-smoke

setup:
	uv sync

test:
	uv run pytest -q

lint:
	uv run ruff check .

types:
	uv run mypy horus/core

check: lint types test

gate-m0: check
	uv run python -c "import horus, h3, duckdb, numpy; print('M0 gate: imports OK')"

# Full day from the Plan B archive by default (SPEC §10 allows it); override e.g.
#   make gate-m1 DAY=2026-11-16 SRC=opensky
gate-m1: check
	uv run python scripts/measure_quantisation.py --day $(or $(DAY),2025-12-02) --source $(or $(SRC),adsblol_hist)
	uv run python scripts/gate_m1.py --day $(or $(DAY),2025-12-02) --source $(or $(SRC),adsblol_hist)

record:
	nohup caffeinate -i uv run python scripts/record_live.py > data/raw/live/nohup-opensky.out 2>&1 & echo $$! > data/raw/live/recorder.pid

recorder-status:
	uv run python scripts/check_recorder.py --hours 24

trino-smoke:
	uv run python scripts/fetch_trino_history.py --smoke-test
