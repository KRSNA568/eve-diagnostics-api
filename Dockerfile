# syntax=docker/dockerfile:1.7
# One image, three roles (selected by the command): api (default), worker, migrate.

# ---------------------------------------------------------------- build
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first, in their own layer: only rebuilt when the lockfile changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

# Then the application itself, installed as a regular (non-editable) package.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---------------------------------------------------------------- runtime
FROM python:3.13-slim AS runtime

# pip is never used at runtime - the app runs from /app/.venv, which uv built without it - and
# it vendors its own msgpack and setuptools, both with published CVEs. Drop it.
RUN python -m pip uninstall --yes --root-user-action=ignore pip \
 && groupadd --system app && useradd --system --gid app --home-dir /app app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
# Alembic reads its config from pyproject.toml ([tool.alembic]) and needs the scripts.
COPY --chown=app:app pyproject.toml ./
COPY --chown=app:app migrations ./migrations

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOG_FORMAT=json \
    # Only trust X-Forwarded-For from these proxies; "*" would let any client spoof its
    # IP and evade per-IP rate limits. Set to your load balancer's address in production.
    FORWARDED_ALLOW_IPS=127.0.0.1

USER app
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/v1/health/live/', timeout=2)"]

CMD ["uvicorn", "eve.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-access-log"]
