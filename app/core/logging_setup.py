"""Structured JSON logging with secret redaction and request-id correlation.

Every log record is one JSON line (stdout for Render + rotating file locally).
`redact()` must be applied to any payload before it is logged.
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import os
import re
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="-")

# Keys whose values are never logged (matched case-insensitively as substrings,
# except the exact-match set which would otherwise over-match, e.g. "code").
_SENSITIVE_SUBSTRINGS = (
    "password", "passwd", "token", "secret", "authorization", "signature",
    "api_key", "apikey", "cookie", "otp",
)
_SENSITIVE_EXACT = {"code", "pin", "key"}
REDACTED = "***REDACTED***"
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return lowered in _SENSITIVE_EXACT or any(s in lowered for s in _SENSITIVE_SUBSTRINGS)


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Return a copy of `value` with sensitive keys masked and long strings truncated."""
    if _depth > 8:
        return "<max-depth>"
    if isinstance(value, dict):
        return {
            str(k): (REDACTED if _is_sensitive(str(k)) else redact(v, _depth=_depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v, _depth=_depth + 1) for v in value[:50]]
    if isinstance(value, str):
        value = _BEARER_RE.sub("Bearer " + REDACTED, value)
        limit = settings.log_body_max_chars
        return value if len(value) <= limit else value[:limit] + f"...<+{len(value) - limit} chars>"
    return value


def redact_text_body(raw: bytes, content_type: str = "") -> Any:
    """Parse a body as JSON when possible (then redact), otherwise return a truncated string."""
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    if "json" in content_type.lower() or text.lstrip().startswith(("{", "[")):
        try:
            return redact(json.loads(text))
        except ValueError:
            pass
    if "x-www-form-urlencoded" in content_type.lower():
        from urllib.parse import parse_qsl

        return redact(dict(parse_qsl(text)))
    return redact(text)


def redact_headers(headers: dict[str, str]) -> dict[str, Any]:
    return {k: (REDACTED if _is_sensitive(k) else v) for k, v in headers.items()}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
            "user_id": user_id_var.get(),
        }
        extra = getattr(record, "data", None)
        if extra is not None:
            payload["data"] = extra
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_sharan_configured", False):
        return
    root.setLevel(settings.log_level.upper())
    formatter = JsonFormatter()

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if settings.log_file:
        os.makedirs(os.path.dirname(settings.log_file) or ".", exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            settings.log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Our middleware logs requests; silence uvicorn's duplicate access log.
    logging.getLogger("uvicorn.access").disabled = True
    # httpx logs full URLs at INFO; our own hooks log outgoing calls with redaction.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    root._sharan_configured = True  # type: ignore[attr-defined]


def log_event(logger: logging.Logger, msg: str, data: Any = None, level: int = logging.INFO) -> None:
    logger.log(level, msg, extra={"data": redact(data) if data is not None else None})
