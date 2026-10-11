"""Outgoing-call logging: httpx event hooks for Supabase, Resend and Razorpay.

Attach with `instrument(client, "resend")`. Every outbound request and response is
logged (redacted) with the originating inbound request-id for correlation.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.core.logging_setup import log_event, redact_headers, redact_text_body

_log = logging.getLogger("outgoing")
_START_KEY = "_sharan_started_at"


def _request_hook(service: str):
    def hook(request: httpx.Request) -> None:
        request.extensions[_START_KEY] = time.perf_counter()
        body = None
        try:
            body = redact_text_body(request.content, request.headers.get("content-type", ""))
        except httpx.RequestNotRead:
            body = "<streaming>"
        log_event(
            _log,
            f"-> {service} {request.method} {request.url.host}{request.url.path}",
            {
                "direction": "outgoing_request",
                "service": service,
                "method": request.method,
                "url": _safe_url(request.url),
                "headers": redact_headers(dict(request.headers)),
                "body": body,
            },
        )

    return hook


def _log_response(service: str, response: httpx.Response) -> None:
    """Log a response whose body has already been read."""
    request = response.request
    started = request.extensions.get(_START_KEY)
    duration_ms = round((time.perf_counter() - started) * 1000, 1) if started else None
    try:
        body = redact_text_body(response.content, response.headers.get("content-type", ""))
    except httpx.HTTPError:
        body = "<unreadable>"
    level = logging.WARNING if response.status_code >= 400 else logging.INFO
    log_event(
        _log,
        f"<- {service} {response.status_code} {request.method} {request.url.host}{request.url.path}",
        {
            "direction": "outgoing_response",
            "service": service,
            "status": response.status_code,
            "duration_ms": duration_ms,
            "body": body,
        },
        level=level,
    )


def _response_hook(service: str):
    def hook(response: httpx.Response) -> None:
        try:
            response.read()
        except httpx.HTTPError:
            pass
        _log_response(service, response)

    return hook


def _async_request_hook(service: str):
    sync = _request_hook(service)

    async def hook(request: httpx.Request) -> None:
        sync(request)

    return hook


def _async_response_hook(service: str):
    async def hook(response: httpx.Response) -> None:
        try:
            await response.aread()
        except httpx.HTTPError:
            pass
        _log_response(service, response)

    return hook


def _safe_url(url: httpx.URL) -> str:
    """Drop the query string: it can carry tokens (e.g. Supabase token_hash)."""
    return f"{url.scheme}://{url.host}{url.path}" + (" ?<query-redacted>" if url.query else "")


def hooks_for(service: str) -> dict[str, list[Any]]:
    """Hooks for a sync httpx.Client."""
    return {"request": [_request_hook(service)], "response": [_response_hook(service)]}


def async_hooks_for(service: str) -> dict[str, list[Any]]:
    """Hooks for an httpx.AsyncClient (which awaits every hook, so sync hooks crash it)."""
    return {"request": [_async_request_hook(service)], "response": [_async_response_hook(service)]}


def instrument(client: httpx.Client | httpx.AsyncClient, service: str) -> None:
    """Add logging hooks to an existing httpx client (sync or async)."""
    hooks = async_hooks_for(service) if isinstance(client, httpx.AsyncClient) else hooks_for(service)
    for event, funcs in hooks.items():
        client.event_hooks[event].extend(funcs)
