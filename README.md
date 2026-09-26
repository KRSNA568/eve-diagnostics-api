# EVE Diagnostics API

Backend service for diagnostic test bookings and simulated payments, built with FastAPI.

## Development

Requires [uv](https://docs.astral.sh/uv/), Python 3.13 and Docker (for the test suite).

```bash
uv sync                                  # create .venv and install dependencies
cp .env.example .env                     # then set DATABASE_URL (Supabase or local)
uv run alembic upgrade head              # apply database migrations
uv run eve seed                          # demo centres, tests and prices (idempotent)
ADMIN_PASSWORD=... uv run eve create-admin --email you@example.com
uv run uvicorn eve.main:create_app --factory --reload   # http://localhost:8000/docs
uv run arq eve.worker.settings.WorkerSettings           # background worker (needs Redis)
uv run pytest                            # starts a throwaway Postgres via testcontainers
```
