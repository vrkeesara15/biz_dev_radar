"""structlog configuration; request id is injected from app.core.context."""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any

import structlog

from app.core.context import get_request_id


def _add_request_id(
    _: Any, __: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    rid = get_request_id()
    if rid:
        event_dict.setdefault("request_id", rid)
    return event_dict


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    logging.basicConfig(level=level.upper(), format="%(message)s")
    renderer: Any = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _add_request_id,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
