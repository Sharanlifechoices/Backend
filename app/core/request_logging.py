"""Pure-ASGI middleware: logs every incoming request and outgoing response.

Pure ASGI (not BaseHTTPMiddleware) so request bodies can be tapped without
breaking downstream body reads. Bodies are captured up to a small cap, redacted,
and logged together with a request-id (also returned as the X-Request-ID header).
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.rate_limit import forwarded_client_ip
from app.core.logging_setup import (
    log_event,
    redact,
    redact_headers,
    redact_text_body,
    request_id_var,
    user_id_var,
)

_log = logging.getLogger("http")
_BODY_CAP = 16_384
_SKIP_PATHS = {"/health"}


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in _SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request_id_var.set(request_id)
        user_id_var.set("-")
        started = time.perf_counter()

        client = scope.get("client")
        req_chunks: list[bytes] = []
        req_size = 0
        res_chunks: list[bytes] = []
        res_size = 0
        status_code = 500
        res_content_type = ""

        async def receive_wrapper() -> Message:
            nonlocal req_size
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                req_size += len(body)
                if sum(len(c) for c in req_chunks) < _BODY_CAP:
                    req_chunks.append(body)
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, res_size, res_content_type
            if message["type"] == "http.response.start":
                status_code = message["status"]
                res_headers = Headers(raw=message["headers"])
                res_content_type = res_headers.get("content-type", "")
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                res_size += len(body)
                if sum(len(c) for c in res_chunks) < _BODY_CAP:
                    res_chunks.append(body)
            await send(message)

        # The request is logged once its body has been read by the handler (below),
        # but we also log a line up front so hung requests are still visible.
        log_event(
            _log,
            f"--> {scope['method']} {scope['path']}",
            {
                "direction": "incoming_request",
                "method": scope["method"],
                "path": scope["path"],
                "query": redact(dict(_parse_query(scope.get("query_string", b"")))),
                "client_ip": _client_ip(headers, client),
                "headers": redact_headers(dict(headers)),
            },
        )

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception:
            log_event(
                _log,
                f"!!! {scope['method']} {scope['path']} unhandled exception",
                {"direction": "error", "duration_ms": _ms(started)},
                level=logging.ERROR,
            )
            _log.exception("unhandled exception")
            raise
        finally:
            req_body = redact_text_body(b"".join(req_chunks), headers.get("content-type", ""))
            res_body = redact_text_body(b"".join(res_chunks), res_content_type)
            level = logging.ERROR if status_code >= 500 else logging.WARNING if status_code >= 400 else logging.INFO
            log_event(
                _log,
                f"<-- {status_code} {scope['method']} {scope['path']} ({_ms(started)} ms)",
                {
                    "direction": "outgoing_response",
                    "status": status_code,
                    "duration_ms": _ms(started),
                    "request_body": req_body,
                    "request_bytes": req_size,
                    "response_body": res_body,
                    "response_bytes": res_size,
                },
                level=level,
            )


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _parse_query(raw: bytes):
    from urllib.parse import parse_qsl

    return parse_qsl(raw.decode("utf-8", errors="replace"))


def _client_ip(headers: Headers, client) -> str:
    return forwarded_client_ip(headers.get("x-forwarded-for"), client[0] if client else None)
