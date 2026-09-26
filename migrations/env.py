"""Alembic environment.

Migrations run over a synchronous psycopg connection. The URL comes from application
settings (MIGRATIONS_DATABASE_URL, falling back to DATABASE_URL) unless a caller such as
the test suite injects one through `config.attributes["database_url"]`.
"""

import logging
from typing import Any

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy import create_engine, pool

import eve.models  # noqa: F401  (registers every model on Base.metadata)
from eve.core.config import get_settings
from eve.core.db import Base, UTCDateTime

config = context.config
target_metadata = Base.metadata

logging.basicConfig(level=logging.INFO, format="%(levelname)-5.5s [%(name)s] %(message)s")


def _database_url() -> str:
    injected: str | None = config.attributes.get("database_url")
    return injected or get_settings().migrations_url


def render_item(type_: str, obj: Any, autogen_context: AutogenContext) -> str | bool:
    """Render app-level column types as plain SQLAlchemy types.

    Migrations must not import application code: it changes over time and would break old
    revisions. UTCDateTime is only ORM behaviour on top of `timestamptz`.
    """
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it (`alembic upgrade head --sql`)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(
        _database_url(),
        poolclass=pool.NullPool,
        # Safe behind any pooler, including Supavisor transaction mode.
        connect_args={"prepare_threshold": None},
    )
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_item=render_item
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
