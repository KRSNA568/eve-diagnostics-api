# EVE Diagnostics API

Backend service for diagnostic test bookings and simulated payments, built with FastAPI.

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```bash
uv sync                                  # create .venv and install dependencies
cp .env.example .env
uv run uvicorn eve.main:app --reload     # http://localhost:8000/docs
uv run pytest
```
