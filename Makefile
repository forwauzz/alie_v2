# ALIE — local dev (PRD §13.2).
#
# `make` is not installed on every dev box. Every target below is a one-line wrapper over
# the `alie` CLI or pytest, so the same thing is reachable without make. The README lists
# the direct commands.

PY := uv run
API_PORT ?= 8471
WEB_PORT ?= 5472
MLFLOW_PORT ?= 5471

.PHONY: help install dev mlflow web test lint fixtures run eval shadow clean

help:
	@echo "install   install python + web deps"
	@echo "dev       start the API on :$(API_PORT) and MLflow on :$(MLFLOW_PORT)"
	@echo "web       start the Vite dev server on :$(WEB_PORT)"
	@echo "test      run the test suite"
	@echo "lint      ruff + tsc"
	@echo "fixtures  regenerate the synthetic fixtures"
	@echo "run       run a fixture end to end and print the chronology (F=hard)"
	@echo "eval      score every gold end to end; non-zero if a must-hold fails (§11)"
	@echo "mlflow    start the tracking server on :$(MLFLOW_PORT) (§11.1)"

install:
	uv venv
	uv pip install -e ".[dev,eval]"
	npm --prefix web install

# Foreground. Starts the app *and* MLflow together (§13.2). Idempotent: a second run reports "already
# running" rather than colliding. MLflow missing is reported, never fatal — the app is the
# source of truth and the harness runs without a recording surface.
dev:
	$(PY) alie dev

# The tracking server on its own, for scoring without the API up.
mlflow:
	$(PY) alie mlflow

web:
	npm --prefix web run dev

test:
	$(PY) pytest -q

lint:
	$(PY) ruff check src tests
	npm --prefix web exec tsc -- -b --force

fixtures:
	$(PY) alie fixtures

F ?= hard
run:
	$(PY) alie run $(F) --rejoin

# `make eval` runs every gold end to end, one MLflow run per fixture, tagged with a shared
# run group (§11.1). The harness is Phase 2 and is not built — fail loudly rather than
# exit 0 on an empty target, which would read as "the golds passed".
# Exits non-zero when a must-hold metric does not hold: groundedness, uncited, coverage
# and truncation are release-blocking, not diagnostic (§11.3).
eval:
	$(PY) alie eval --group $${GROUP:-local}

clean:
	rm -rf var .pytest_cache .ruff_cache web/dist
