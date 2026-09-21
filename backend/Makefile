# HHGoa Fraud Investigation Backend
PY := .venv/Scripts/python.exe

.PHONY: help install env health inspect schema load test test-unit test-tg test-mcp api benchmark lint clean

help:
	@echo "install     Create venv and install dependencies"
	@echo "env         Copy .env.example to .env (does not overwrite)"
	@echo "health      Run the environment healthcheck"
	@echo "inspect     Profile the dataset -> docs/dataset-analysis.md"
	@echo "schema      Deploy the GSQL schema to TigerGraph"
	@echo "load        Load the dataset into TigerGraph"
	@echo "test        Run the full test suite"
	@echo "test-unit   Run offline unit tests only"
	@echo "api         Start the FastAPI server"
	@echo "benchmark   Run the 20-case benchmark"

install:
	py -3.11 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

env:
	@test -f .env || cp .env.example .env
	@echo ".env ready - fill in your credentials"

health:
	$(PY) scripts/healthcheck.py

inspect:
	$(PY) scripts/inspect_dataset.py

schema:
	$(PY) scripts/create_schema.py

load:
	$(PY) scripts/load_data.py

test:
	$(PY) -m pytest -v

test-unit:
	$(PY) -m pytest tests/unit -v

test-tg:
	$(PY) -m pytest tests/tigergraph -v -m tigergraph

verify-tg:
	$(PY) scripts/test_tigergraph.py

test-mcp:
	$(PY) scripts/test_mcp.py

api:
	$(PY) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

benchmark:
	$(PY) scripts/run_benchmark.py

lint:
	$(PY) -m ruff check app tests scripts

clean:
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +

fetch:
	$(PY) scripts/fetch_dataset.py
