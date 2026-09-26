import json
import logging

import pytest
import structlog

from eve.core.logging import configure_logging


def _our_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if h.get_name() == "eve"]


def test_configure_logging_is_idempotent_and_keeps_foreign_handlers() -> None:
    foreign = logging.NullHandler()
    logging.getLogger().addHandler(foreign)
    try:
        configure_logging(fmt="json")
        configure_logging(fmt="json")

        assert len(_our_handlers()) == 1
        assert foreign in logging.getLogger().handlers
    finally:
        logging.getLogger().removeHandler(foreign)


def test_json_format_renders_bound_context_as_one_json_object(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(fmt="json")
    structlog.contextvars.bind_contextvars(request_id="req-1")
    try:
        structlog.get_logger("test").info("booking.created", booking_id="b-1")
    finally:
        structlog.contextvars.clear_contextvars()

    line = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(line)
    assert record["event"] == "booking.created"
    assert record["booking_id"] == "b-1"
    assert record["request_id"] == "req-1"
    assert record["level"] == "info"
    assert "timestamp" in record


def test_stdlib_loggers_share_the_structured_format(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(fmt="json")

    logging.getLogger("sqlalchemy.engine").warning("pool exhausted")

    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert record["event"] == "pool exhausted"
    assert record["logger"] == "sqlalchemy.engine"


def test_uvicorn_color_message_is_dropped(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(fmt="json")

    logging.getLogger("uvicorn.error").info(
        "Started server", extra={"color_message": "\x1b[36mStarted server\x1b[0m"}
    )

    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert record["event"] == "Started server"
    assert "color_message" not in record
