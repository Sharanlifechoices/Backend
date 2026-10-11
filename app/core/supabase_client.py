"""Supabase client factories (adapted from the tunefry backend).

- ``get_supabase()``       -> anon-key client for user-facing auth operations.
- ``get_service_client()`` -> service-role client (server-only, bypasses RLS).

Every outgoing HTTP call made by these clients is logged via ``http_logging``.
"""

from __future__ import annotations

import logging

import httpx
from supabase import Client, create_client

from app.core.config import settings
from app.core.http_logging import instrument

_log = logging.getLogger(__name__)


def _prepare(client: Client, name: str) -> Client:
    # GoTrue's httpx client defaults to a 5s timeout; widen it.
    client.auth._http_client.timeout = httpx.Timeout(settings.supabase_http_timeout)
    # Attach outgoing-call logging to the auth and PostgREST sessions.
    for label, http in (
        (f"{name}-auth", getattr(client.auth, "_http_client", None)),
        (f"{name}-db", getattr(client.postgrest, "session", None)),
    ):
        try:
            if http is not None:
                instrument(http, label)
        except Exception:  # never let logging setup break the client
            _log.warning("could not instrument %s http client", label)
    return client


def get_supabase() -> Client:
    """Anon client for password/recovery auth calls."""
    return _prepare(create_client(settings.supabase_url, settings.supabase_anon_key), "supabase")


def get_service_client() -> Client:
    """Service-role client. Never expose this key or client to the browser."""
    return _prepare(
        create_client(settings.supabase_url, settings.supabase_service_role_key), "supabase-admin"
    )
