"""Supabase-facing operations for auth (the only module that talks to GoTrue / profiles).

Synchronous (supabase-py is sync); the router runs these via `run_in_threadpool`.
Exposed through the `get_backend` dependency so tests can swap in a fake.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from supabase_auth.errors import AuthApiError

from app.core.supabase_client import get_service_client, get_supabase

_log = logging.getLogger(__name__)


class DuplicateUser(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class EmailNotVerified(Exception):
    pass


class InvalidRefreshToken(Exception):
    pass


def _session_payload(result) -> dict[str, Any]:
    session = result.session
    return {
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
        "expires_in": session.expires_in,
        "token_type": "bearer",
    }


class AuthBackend:
    def __init__(self) -> None:
        self._admin = get_service_client()

    # -- profiles ---------------------------------------------------------------------
    def profile_by_email(self, email: str) -> Optional[dict]:
        rows = self._admin.table("profiles").select("*").eq("email", email).limit(1).execute().data
        return rows[0] if rows else None

    def profile_by_id(self, user_id: str) -> Optional[dict]:
        rows = self._admin.table("profiles").select("*").eq("user_id", user_id).limit(1).execute().data
        return rows[0] if rows else None

    def update_profile(self, user_id: str, fields: dict) -> dict:
        payload = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in fields.items()}
        rows = self._admin.table("profiles").update(payload).eq("user_id", user_id).execute().data
        return rows[0]

    # -- users ------------------------------------------------------------------------
    def create_unconfirmed_user(self, email: str, password: str, meta: dict) -> str:
        try:
            result = self._admin.auth.admin.create_user(
                {"email": email, "password": password, "email_confirm": False, "user_metadata": meta}
            )
        except AuthApiError as exc:
            if getattr(exc, "code", "") in ("email_exists", "user_already_exists") or "already" in str(exc).lower():
                raise DuplicateUser() from exc
            raise
        return result.user.id

    def update_pending_user(self, user_id: str, password: str, meta: dict) -> None:
        self._admin.auth.admin.update_user_by_id(user_id, {"password": password, "user_metadata": meta})
        self._admin.table("profiles").update(
            {"full_name": meta.get("full_name", ""), "phone": meta.get("phone", ""), "dob": meta.get("dob") or None}
        ).eq("user_id", user_id).execute()

    def is_confirmed(self, user_id: str) -> bool:
        user = self._admin.auth.admin.get_user_by_id(user_id).user
        return bool(user and user.email_confirmed_at)

    def confirm_email(self, user_id: str) -> None:
        self._admin.auth.admin.update_user_by_id(user_id, {"email_confirm": True})

    def set_password(self, user_id: str, password: str) -> None:
        self._admin.auth.admin.update_user_by_id(user_id, {"password": password})

    # -- sessions ---------------------------------------------------------------------
    def login(self, email: str, password: str) -> dict[str, Any]:
        try:
            result = get_supabase().auth.sign_in_with_password({"email": email, "password": password})
        except AuthApiError as exc:
            code = getattr(exc, "code", "") or ""
            if code == "email_not_confirmed" or "not confirmed" in str(exc).lower():
                raise EmailNotVerified() from exc
            raise InvalidCredentials() from exc
        if not result.session:
            raise InvalidCredentials()
        return {**_session_payload(result), "user_id": result.user.id}

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        try:
            result = get_supabase().auth.refresh_session(refresh_token)
        except AuthApiError as exc:
            raise InvalidRefreshToken() from exc
        if not result.session:
            raise InvalidRefreshToken()
        return _session_payload(result)

    def sign_out(self, access_token: str) -> None:
        try:
            self._admin.auth.admin.sign_out(access_token)
        except Exception:  # best effort: the token expires on its own
            _log.warning("sign_out failed (ignored)")


_backend: AuthBackend | None = None


def get_backend() -> AuthBackend:
    global _backend
    if _backend is None:
        _backend = AuthBackend()
    return _backend
