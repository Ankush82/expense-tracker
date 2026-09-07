# Expense Tracker

Multi-user expense tracking app: manual entry, automatic email/statement import, AI categorization, budgets, group splitting, and analytics.

## Tech Stack

- **API**: FastAPI (Python 3.11+)
- **Web**: React + Vite + TypeScript
- **Database**: PostgreSQL 15
- **Cache/Queue**: Redis 7
- **Email (dev)**: Mailhog

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Make

### Setup

1. Clone the repository
2. Copy `.env.example` to `.env` and configure:
   ```bash
   cp .env.example .env
   ```
3. Start all services:
   ```bash
   make dev
   ```

Services will be available at:
- API: http://localhost:8000
- Web: http://localhost:5173
- Mailhog (SMTP UI): http://localhost:8025

### Available Commands

| Command | Description |
|---------|-------------|
| `make dev` | Start all services in development mode |
| `make test` | Run both API and web test suites |
| `make migrate` | Apply database migrations |
| `make lint` | Run linters (ruff, eslint) |
| `make type-check` | Run type checkers (mypy, tsc) |
| `make ci` | Run full CI pipeline |
| `make clean` | Remove containers and volumes |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SECRET_KEY` | **Yes** | — | Secret key for JWT token signing. Generate a secure random string. |
| `POSTGRES_SERVER` | **Yes** | — | PostgreSQL host address. In Docker, use the service name `postgres`. |
| `POSTGRES_USER` | **Yes** | — | PostgreSQL username. |
| `POSTGRES_PASSWORD` | **Yes** | — | PostgreSQL password. |
| `POSTGRES_DB` | **Yes** | — | PostgreSQL database name. |
| `REDIS_HOST` | **Yes** | — | Redis host address. In Docker, use the service name `redis`. |
| `REDIS_PORT` | No | `6379` | Redis port number. |
| `REDIS_PASSWORD` | No | `None` | Redis password (if authentication is enabled). |
| `REDIS_DB` | No | `0` | Redis database number. |
| `MAILHOG_HOST` | No | `mailhog` | Mailhog SMTP host (local development only). |
| `MAILHOG_PORT` | No | `1025` | Mailhog SMTP port (local development only). |
| `MAILHOG_USER` | No | `None` | Mailhog authentication username (optional). |
| `MAILHOG_PASSWORD` | No | `None` | Mailhog authentication password (optional). |

### Local Development

For local development outside of Docker:

1. Install dependencies:
   ```bash
   # API
   cd api && pip install -r requirements.txt
   
   # Web
   cd web && npm install
   ```

2. Configure your `.env` file with local service addresses (e.g., `localhost` instead of service names).

3. Run services individually:
   ```bash
   # API
   cd api && uvicorn app.main:app --reload --port 8000
   
   # Web
   cd web && npm run dev
   ```

## Architecture

```
├── api/              # FastAPI backend
│   ├── app/
│   │   ├── core/     # Core configuration and utilities
│   │   ├── models/   # Database models
│   │   ├── routers/  # API routes
│   │   └── schemas/  # Pydantic schemas
│   ├── alembic/      # Database migrations
│   └── tests/        # API tests
├── web/              # React frontend
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── hooks/
│   │   └── services/
│   └── tests/
├── infra/            # Infrastructure
│   └── docker-compose.yml
└── docs/             # Documentation
```

## CI/CD

All pull requests must pass:
1. **Lint**: `ruff` (Python), `eslint` (TypeScript)
2. **Type Check**: `mypy` (Python), `tsc --noEmit` (TypeScript)
3. **Tests**: Unit and integration tests
4. **Docker Build**: Verify Dockerfile builds successfully

A failing CI pipeline blocks merge.
