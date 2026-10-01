"""Redacted structured logging for session and mission lifecycle events."""

from __future__ import annotations

import logging as stdlib_logging
from collections.abc import Mapping
from typing import Any, cast

import structlog

_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "cookies",
    "gemini_api_key",
    "password",
    "request_body",
    "response_body",
    "secret",
    "set_cookie",
}
_SENSITIVE_KEY_PARTS = ("access_token", "refresh_token", "page_text", "user_text")
_MAX_STRING_LENGTH = 240


def configure_logging(level: str = "INFO") -> None:
    """Configure JSON logs once for the current process.

    The processor redacts known credential/body fields and truncates arbitrary
    strings so logging metadata cannot accidentally become a page-content dump.
    Callers should still log identifiers and counts rather than raw page data.
    """

    normalized_level = getattr(stdlib_logging, level.upper(), stdlib_logging.INFO)
    stdlib_logging.basicConfig(level=normalized_level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_sensitive,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(normalized_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structured logger bound to a module name."""

    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))


def redact_sensitive(
    logger: Any,
    method_name: str,
    event_dict: Mapping[str, Any],
) -> dict[str, Any]:
    """Redact credentials and bound the size of values in one log event."""

    del logger, method_name
    return {key: _sanitize_value(key, value) for key, value in event_dict.items()}


def _sanitize_value(key: str, value: Any) -> Any:
    normalized = key.casefold().replace("-", "_")
    if normalized in _SENSITIVE_KEYS or any(part in normalized for part in _SENSITIVE_KEY_PARTS):
        return _REDACTED
    if isinstance(value, Mapping):
        return {str(child_key): _sanitize_value(str(child_key), child_value) for child_key, child_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(key, item) for item in value[:20]]
    if isinstance(value, str) and len(value) > _MAX_STRING_LENGTH:
        return f"{value[:_MAX_STRING_LENGTH]}…"
    return value
