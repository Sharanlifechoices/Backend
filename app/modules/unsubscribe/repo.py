"""Marketing-email suppression list (service-role client)."""

from __future__ import annotations

from typing import Iterable, Optional

from app.core.supabase_client import get_service_client


class SuppressionRepo:
    def __init__(self) -> None:
        self._db = get_service_client()

    def suppress(self, email: str) -> None:
        email = email.lower()
        self._db.table("email_suppressions").upsert({"email": email}, on_conflict="email").execute()
        self._db.table("profiles").update({"is_unsubscribed": True}).eq("email", email).execute()

    def resubscribe(self, email: str) -> None:
        email = email.lower()
        self._db.table("email_suppressions").delete().eq("email", email).execute()
        self._db.table("profiles").update({"is_unsubscribed": False}).eq("email", email).execute()

    def suppressed_among(self, emails: Iterable[str]) -> set[str]:
        """Which of these addresses may not get marketing email (suppression list or profile flag)."""
        wanted = sorted({e.lower() for e in emails if e})
        if not wanted:
            return set()
        listed = self._db.table("email_suppressions").select("email").in_("email", wanted).execute().data
        flagged = self._db.table("profiles").select("email").in_("email", wanted).eq("is_unsubscribed", True).execute().data
        return {r["email"] for r in (*listed, *flagged)}


_repo: Optional[SuppressionRepo] = None


def get_suppression_repo() -> SuppressionRepo:
    global _repo
    if _repo is None:
        _repo = SuppressionRepo()
    return _repo
