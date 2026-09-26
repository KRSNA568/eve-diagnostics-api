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
from decimal import Decimal
from uuid import UUID

import httpx
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from eve.cli.commands import (
    SeedResult,
    build_payment_event,
    create_admin,
    seed_catalog,
    send_webhook,
)
from eve.core.cache import VersionedCache
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
    async def seed_and_invalidate(session: AsyncSession) -> SeedResult:
        result = await seed_catalog(session)
        redis = Redis.from_url(get_settings().redis_url)
        try:
            await VersionedCache(redis, namespace="catalog", ttl_seconds=1).invalidate()
        finally:
            await redis.aclose()
        return result

    result = asyncio.run(_run(seed_and_invalidate))
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


def _send_webhook(args: argparse.Namespace) -> int:
    event = build_payment_event(
        booking_id=args.booking_id,
        outcome=args.outcome,
        amount=args.amount,
        provider_reference=args.provider_reference,
        event_id=args.event_id,
    )
    secret = get_settings().webhook_secret.get_secret_value()

    async def deliver() -> list[httpx.Response]:
        async with httpx.AsyncClient(timeout=10) as client:
            return await send_webhook(client, args.url, event, secret, repeat=args.repeat)

    sys.stdout.write(f"event_id={event['event_id']} type={event['type']}\n")
    for attempt, response in enumerate(asyncio.run(deliver()), start=1):
        sys.stdout.write(f"delivery {attempt}: HTTP {response.status_code} {response.text}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eve", description="EVE Diagnostics operations")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="insert demo centres, tests and prices (idempotent)")
    admin = commands.add_parser("create-admin", help="create an admin, or promote a user")
    admin.add_argument("--email", required=True)
    hook = commands.add_parser("send-webhook", help="act as the payment provider: send an event")
    hook.add_argument("--booking-id", type=UUID, required=True)
    hook.add_argument("--outcome", choices=["succeeded", "failed"], required=True)
    hook.add_argument("--amount", type=Decimal, required=True)
    hook.add_argument("--provider-reference", help="default: a new provider payment id")
    hook.add_argument("--event-id", help="default: a new event id")
    hook.add_argument("--repeat", type=int, default=1, help="deliver the same event N times")
    hook.add_argument("--url", default="http://localhost:8000/api/v1/payments/webhook/")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(level="WARNING", fmt=settings.log_format)

    if args.command == "seed":
        return _seed()
    if args.command == "send-webhook":
        return _send_webhook(args)
    return _create_admin(args.email)


if __name__ == "__main__":
    raise SystemExit(main())
