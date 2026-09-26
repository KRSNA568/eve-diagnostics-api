"""Structured logging.

structlog renders every log line - ours and third-party ones from uvicorn, SQLAlchemy,
Alembic - through a single stdlib handler, so all output shares one format and carries
context bound with `structlog.contextvars` (e.g. `request_id`).
"""

import logging
import sys
from typing import Literal

import structlog
from structlog.types import EventDict, Processor, WrappedLogger

LogFormat = Literal["json", "console"]

_HANDLER_NAME = "eve"


def _drop_color_message(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Uvicorn attaches an ANSI-coloured duplicate of each message; it is noise in logs."""
    event_dict.pop("color_message", None)
    return event_dict


def configure_logging(*, level: str = "INFO", fmt: LogFormat = "json") -> None:
    """Configure structlog and the stdlib root logger. Safe to call more than once."""
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.ExtraAdder(),
        _drop_color_message,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.processors.StackInfoRenderer(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderers: list[Processor] = (
        [structlog.processors.dict_tracebacks, structlog.processors.JSONRenderer()]
        if fmt == "json"
        else [structlog.dev.ConsoleRenderer()]
    )
    final_processors = [structlog.stdlib.ProcessorFormatter.remove_processors_meta, *renderers]

    handler = logging.StreamHandler(sys.stdout)
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=final_processors,
        )
    )

    root = logging.getLogger()
    # Replace only our own handler, so repeated calls don't duplicate output and handlers
    # installed by others (e.g. pytest's log capture) survive.
    for existing in [h for h in root.handlers if h.get_name() == _HANDLER_NAME]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Route uvicorn's and arq's own logs through our formatter (both CLIs install their own
    # handlers, which would print every line twice); drop uvicorn's access log because the
    # request middleware emits a richer `http.request` event.
    for name in ("uvicorn", "uvicorn.error", "arq"):
        framework_logger = logging.getLogger(name)
        framework_logger.handlers.clear()
        framework_logger.propagate = True
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False
