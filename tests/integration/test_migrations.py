"""Guards on the schema itself, run against the migrated test database."""

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from eve.core.config import Settings
from tests.conftest import PROJECT_ROOT


async def test_every_table_has_row_level_security_enabled(db_session: AsyncSession) -> None:
    """On Supabase, a table without RLS is readable and writable through the public Data
    API. Fails if a migration forgets `ENABLE ROW LEVEL SECURITY`."""
    result = await db_session.execute(
        text(
            "SELECT relname FROM pg_class "
            "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' "
            "AND NOT relrowsecurity"
        )
    )

    assert result.scalars().all() == []


def test_models_and_migrations_are_in_sync(settings: Settings, migrated_database: None) -> None:
    """Fails if a model changed without a matching migration (`alembic check`)."""
    config = Config(toml_file=str(PROJECT_ROOT / "pyproject.toml"))
    config.attributes["database_url"] = settings.migrations_url

    command.check(config)
