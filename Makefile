.PHONY: help build up down logs run run-gdoc sync-regulatory lint test test-integration clean

# ── Config ─────────────────────────────────────────────────────────────────────
COMPOSE = docker compose
ENV_FILE = .env
PYTHON = $(shell test -x ../.venv/bin/python && echo ../.venv/bin/python || echo python)

help:
	@echo ""
	@echo "  make build           Build the pipeline image"
	@echo "  make up              Start postgres + minio (detached)"
	@echo "  make down            Stop and remove containers"
	@echo "  make run             Process files in ./sample_contracts/"
	@echo "  make sync-regulatory Fetch/update Europa datasets for active contracts"
	@echo "  make lint            Run ruff format check + lint"
	@echo "  make lint-fix        Auto-fix ruff formatting and lint issues"
	@echo "  make logs            Tail pipeline container logs"
	@echo "  make test            Smoke tests (no Docker required)"
	@echo "  make test-integration  Integration tests (requires make up first)"
	@echo "  make clean           Remove containers, volumes, and built images"
	@echo ""

build:
	$(COMPOSE) build

up:
	@cp -n $(ENV_FILE).example $(ENV_FILE) 2>/dev/null || true
	$(COMPOSE) up -d postgres minio minio-init
	@echo "Waiting for services to be healthy..."
	@$(COMPOSE) ps

down:
	$(COMPOSE) down

run:
	@cp -n $(ENV_FILE).example $(ENV_FILE) 2>/dev/null || true
	$(COMPOSE) run --rm pipeline \
		python -m src.agent.pipeline --input-dir /app/input

run-gdoc:
	@cp -n $(ENV_FILE).example $(ENV_FILE) 2>/dev/null || true
	$(COMPOSE) run --rm pipeline \
		python -m src.agent.pipeline --gdoc-ids $(GDOC_IDS)

sync-regulatory:
	@cp -n $(ENV_FILE).example $(ENV_FILE) 2>/dev/null || true
	$(COMPOSE) run --rm pipeline \
		python -m src.regulatory.sync
logs:
	$(COMPOSE) logs -f pipeline

# ── Lint ───────────────────────────────────────────────────────────────────────

lint:
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m ruff check .

lint-fix:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

# ── Tests ──────────────────────────────────────────────────────────────────────

test:
	@$(PYTHON) -m pip install -q -r requirements.txt
	$(PYTHON) -m pytest tests/test_extractor.py tests/test_storage.py tests/test_regulatory.py -v

test-integration:
	@cp -n $(ENV_FILE).example $(ENV_FILE) 2>/dev/null || true
	POSTGRES_HOST_PORT=55432 MINIO_HOST_PORT=59000 MINIO_CONSOLE_HOST_PORT=59001 \
		$(COMPOSE) up -d --force-recreate postgres minio minio-init
	@echo "Waiting for healthy services (15s)..."
	@sleep 15
	POSTGRES_HOST=localhost POSTGRES_PORT=55432 MINIO_ENDPOINT=localhost:59000 \
		$(PYTHON) -m pytest tests/test_integration.py -v -m integration

clean:
	$(COMPOSE) down -v --rmi local
