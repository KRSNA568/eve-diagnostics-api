.DEFAULT_GOAL := help
.PHONY: help install dev worker migrate seed test lint format typecheck check up down logs clean

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- local development
install: ## Install dependencies and git hooks
	uv sync
	uv run pre-commit install

dev: ## Run the API with auto-reload (http://localhost:8000/docs)
	uv run uvicorn eve.main:create_app --factory --reload

worker: ## Run the background worker
	uv run arq eve.worker.settings.WorkerSettings

migrate: ## Apply database migrations
	uv run alembic upgrade head

seed: ## Load demo centres, tests and prices
	uv run eve seed

# ---------------------------------------------------------------- quality
test: ## Run the test suite with coverage (starts Postgres/Redis containers)
	uv run pytest --cov

lint: ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

format: ## Auto-format and fix lint
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## Static type checking
	uv run mypy

check: lint typecheck test ## Everything CI runs

# ---------------------------------------------------------------- docker
up: ## Build and start the full stack (API, worker, Postgres, Redis)
	docker compose up --build --detach
	@echo "API: http://localhost:8000/docs"

down: ## Stop the stack
	docker compose down

logs: ## Follow API and worker logs
	docker compose logs --follow api worker

clean: ## Stop the stack and delete its data volumes
	docker compose down --volumes
