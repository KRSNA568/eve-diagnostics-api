from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import DateTime, Dialect, MetaData, TypeDecorator, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from uuid_utils.compat import uuid7

from eve.core.config import Settings

# Deterministic constraint names keep Alembic autogenerate output stable and reviewable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UTCDateTime(TypeDecorator[datetime]):
    """`timestamptz` that refuses naive datetimes and always returns UTC.

    Postgres renders timestamptz in the *session* time zone, so without this the API's
    output would depend on the server's locale. Setting the session time zone at connect
    time is not an option behind Supabase's transaction pooler, so normalise here.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("naive datetimes are not allowed; use timezone-aware values")
        return value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: UTCDateTime}


class UUIDPrimaryKeyMixin:
    # UUIDv7 is time-ordered: unguessable like v4, but inserts stay append-only in the index.
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7, sort_order=-100)


class TimestampMixin:
    # sort_order keeps `id` first and timestamps last in generated tables.
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), sort_order=100)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), sort_order=101
    )


def violated_constraint(exc: IntegrityError) -> str | None:
    """Name of the constraint behind an IntegrityError (psycopg exposes it via `diag`).

    Lets services translate a specific constraint violation into a domain error, which is
    race-safe unlike a check-then-insert.
    """
    diag = getattr(exc.orig, "diag", None)
    name: str | None = getattr(diag, "constraint_name", None)
    return name


def create_engine(settings: Settings) -> AsyncEngine:
    connect_args: dict[str, Any] = {}
    if settings.db_disable_prepared_statements:
        connect_args["prepare_threshold"] = None

    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_pre_ping=True,
        echo=settings.db_echo,
        connect_args=connect_args,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
