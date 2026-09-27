"""structlog configuration.

Every line carries the request id, and — once the bearer token has been decoded — the
tenant id and user id, injected from app.core.context so no call site has to remember
(SPEC 10.1 structured logs; M7-05).
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any

import structlog

from app.core.context import get_request_id, get_tenant_id, get_user_id


def _add_request_context(
    _: Any, __: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key, getter in (
        ("request_id", get_request_id),
        ("tenant_id", get_tenant_id),
        ("user_id", get_user_id),
    ):
        value = getter()
        if value:
            event_dict.setdefault(key, value)
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
            _add_request_context,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
