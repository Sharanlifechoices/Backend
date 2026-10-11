"""Data access for the daily jobs and the email dedupe log (service-role client)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from postgrest.exceptions import APIError

from app.core.supabase_client import get_service_client

_UNIQUE_VIOLATION = "23505"
_STALE_PENDING = timedelta(hours=1)  # a claim older than this is a crashed attempt; allow a retry


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class EmailLogRepo:
    def __init__(self) -> None:
        self._db = get_service_client()

    def claim(self, key: str, kind: str, recipient: str) -> bool:
        """True if the caller may send this email now. False if it was already sent or is in flight.
        Failed (and long-stale pending) attempts can be claimed again."""
        try:
            self._db.table("email_log").insert({"dedupe_key": key, "kind": kind, "recipient": recipient}).execute()
            return True
        except APIError as exc:
            if getattr(exc, "code", "") != _UNIQUE_VIOLATION:
                raise
        now = datetime.now(timezone.utc)
        stale = _iso(now - _STALE_PENDING)
        rows = (
            self._db.table("email_log")
            .update({"status": "pending", "error": None, "updated_at": _iso(now)})
            .eq("dedupe_key", key)
            .or_(f"status.eq.failed,and(status.eq.pending,updated_at.lt.{stale})")
            .execute().data
        )
        return bool(rows)

    def mark_sent(self, key: str, provider_id: str) -> None:
        self._db.table("email_log").update(
            {"status": "sent", "provider_id": provider_id, "updated_at": _iso(datetime.now(timezone.utc))}
        ).eq("dedupe_key", key).execute()

    def mark_failed(self, key: str, error: str) -> None:
        self._db.table("email_log").update(
            {"status": "failed", "error": error[:500], "updated_at": _iso(datetime.now(timezone.utc))}
        ).eq("dedupe_key", key).execute()


class JobsRepo:
    def __init__(self) -> None:
        self._db = get_service_client()

    def paid_orders_between(self, since: datetime, until: datetime) -> list[dict]:
        return (
            self._db.table("orders").select("id, user_id, email, customer_name, paid_at, order_items(title)")
            .eq("status", "paid").gte("paid_at", _iso(since)).lte("paid_at", _iso(until)).execute().data
        )

    def unsubscribed_among(self, user_ids: list[str]) -> set[str]:
        if not user_ids:
            return set()
        rows = self._db.table("profiles").select("user_id").in_("user_id", user_ids).eq("is_unsubscribed", True).execute().data
        return {r["user_id"] for r in rows}

    def birthday_profiles(self, month: int, day: int) -> list[dict]:
        return self._db.rpc("birthday_profiles", {"m": month, "d": day}).execute().data

    def stale_unverified(self, cutoff: datetime) -> list[dict]:
        return (
            self._db.table("profiles").select("user_id, email").eq("email_verified", False)
            .lt("created_at", _iso(cutoff)).execute().data
        )

    def delete_user(self, user_id: str) -> None:
        self._db.auth.admin.delete_user(user_id)


_jobs: Optional[JobsRepo] = None
_log: Optional[EmailLogRepo] = None


def get_jobs_repo() -> JobsRepo:
    global _jobs
    if _jobs is None:
        _jobs = JobsRepo()
    return _jobs


def get_email_log() -> EmailLogRepo:
    global _log
    if _log is None:
        _log = EmailLogRepo()
    return _log
