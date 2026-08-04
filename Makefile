# ALIE — local dev (PRD §13.2).
#
# `make` is not installed on every dev box. Every target below is a one-line wrapper over
# the `alie` CLI or pytest, so the same thing is reachable without make. The README lists
# the direct commands.

PY := uv run
API_PORT ?= 8471
WEB_PORT ?= 5472

.PHONY: help install dev web test lint fixtures run eval clean

help:
	@echo "install   install python + web deps"
	@echo "dev       start the API on :$(API_PORT) (idempotent, fixed port)"
	@echo "web       start the Vite dev server on :$(WEB_PORT)"
	@echo "test      run the test suite"
	@echo "lint      ruff + tsc"
	@echo "fixtures  regenerate the synthetic fixtures"
	@echo "run       run a fixture end to end and print the chronology (F=hard)"
	@echo "eval      score every gold end to end; non-zero if a must-hold fails (§11)"

install:
	uv venv
	uv pip install -e ".[dev]"
	npm --prefix web install

# Foreground. Idempotent: a second run reports "already running" rather than failing on a
# port collision, and a foreign holder is named rather than silently worked around.
#
# §13.2 asks this to start the app *and* MLflow together. MLflow arrives with the eval
# harness in Phase 2; until then this starts the app only, and says so rather than
# pretending otherwise.
dev:
	$(PY) alie dev

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
