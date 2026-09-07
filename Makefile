.PHONY: dev test migrate lint type-check ci help

help: ## Show this help message
	@echo "Available commands:"
	@echo "  make dev          - Start all services in development mode"
	@echo "  make test         - Run both API and web test suites"
	@echo "  make migrate      - Apply database migrations"
	@echo "  make lint         - Run linters (ruff, eslint)"
	@echo "  make type-check   - Run type checkers (mypy, tsc)"
	@echo "  make ci           - Run full CI pipeline"
	@echo "  make clean        - Remove containers and volumes"

dev: ## Start all services in development mode
	docker-compose -f infra/docker-compose.yml up --build

test: ## Run both API and web test suites
	docker-compose -f infra/docker-compose.yml run --rm api pytest
	docker-compose -f infra/docker-compose.yml run --rm web npm run test

migrate: ## Apply database migrations
	docker-compose -f infra/docker-compose.yml exec api alembic upgrade head

lint: ## Run linters (ruff, eslint)
	docker-compose -f infra/docker-compose.yml run --rm api ruff check .
	docker-compose -f infra/docker-compose.yml run --rm web eslint .

type-check: ## Run type checkers (mypy, tsc)
	docker-compose -f infra/docker-compose.yml run --rm api mypy app
	docker-compose -f infra/docker-compose.yml run --rm web tsc --noEmit

ci: lint type-check test docker-build ## Run full CI pipeline
docker-build: ## Build Docker images without running
	docker-compose -f infra/docker-compose.yml build

clean: ## Remove containers and volumes
	docker-compose -f infra/docker-compose.yml down -v