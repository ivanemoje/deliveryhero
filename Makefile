.PHONY: help build up down logs run run-gdoc lint test test-integration clean

# ── Config ─────────────────────────────────────────────────────────────────────
COMPOSE = docker compose
ENV_FILE = .env

help:
	@echo ""
	@echo "  make build           Build the pipeline image"
	@echo "  make up              Start postgres + minio (detached)"
	@echo "  make down            Stop and remove containers"
	@echo "  make run             Process files in ./sample_contracts/"
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
logs:
	$(COMPOSE) logs -f pipeline

# ── Lint ───────────────────────────────────────────────────────────────────────

lint:
	ruff format --check .
	ruff check .

lint-fix:
	ruff format .
	ruff check --fix .

# ── Tests ──────────────────────────────────────────────────────────────────────

test:
	@pip install -q -r requirements.txt
	pytest tests/test_extractor.py tests/test_storage.py -v

test-integration:
	@cp -n $(ENV_FILE).example $(ENV_FILE) 2>/dev/null || true
	$(COMPOSE) up -d postgres minio minio-init
	@echo "Waiting for healthy services (15s)..."
	@sleep 15
	POSTGRES_HOST=localhost MINIO_ENDPOINT=localhost:9000 \
		pytest tests/test_integration.py -v -m integration

clean:
	$(COMPOSE) down -v --rmi local
