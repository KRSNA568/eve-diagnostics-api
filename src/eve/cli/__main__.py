"""`uv run eve <command>` - operational commands against the configured database.

uv run eve seed                               # demo centres, tests and prices
uv run eve create-admin --email ops@example.com # password from ADMIN_PASSWORD or a prompt
"""

import argparse
import asyncio
import getpass
import os
import sys
from collections.abc import Awaitable, Callable

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from eve.cli.commands import create_admin, seed_catalog
from eve.core.config import get_settings
from eve.core.db import create_engine, create_session_factory
from eve.core.logging import configure_logging


async def _run[T](command: Callable[[AsyncSession], Awaitable[T]]) -> T:
    engine = create_engine(get_settings())
    try:
        async with create_session_factory(engine)() as session:
            return await command(session)
    finally:
        await engine.dispose()


def _seed() -> int:
    result = asyncio.run(_run(seed_catalog))
    sys.stdout.write(
        f"Seeded catalog: {result.tests} tests, {result.centres} centres, "
        f"{result.offerings} offerings (existing rows untouched).\n"
    )
    return 0


def _create_admin(email: str) -> int:
    password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Admin password: ")
    try:
        user, created = asyncio.run(_run(lambda s: create_admin(s, email, password)))
    except ValidationError as exc:
        sys.stderr.write(f"Invalid input: {exc.errors()[0]['msg']}\n")
        return 2
    action = "Created admin" if created else "Promoted existing user to admin"
    sys.stdout.write(f"{action}: {user.email}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eve", description="EVE Diagnostics operations")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="insert demo centres, tests and prices (idempotent)")
    admin = commands.add_parser("create-admin", help="create an admin, or promote a user")
    admin.add_argument("--email", required=True)
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(level="WARNING", fmt=settings.log_format)

    if args.command == "seed":
        return _seed()
    return _create_admin(args.email)


if __name__ == "__main__":
    raise SystemExit(main())
