"""Data access for the admin panel and the public gallery (service-role client)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from app.core.supabase_client import get_service_client

_PAGE = 1000  # PostgREST returns at most 1000 rows per request


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _search_term(q: str) -> str:
    """Keep only characters that are safe inside a PostgREST or()/ilike filter."""
    return re.sub(r"[^\w@.+\- ]", "", q)[:64].strip()


class AdminRepo:
    def __init__(self) -> None:
        self._db = get_service_client()

    # -- purchases ------------------------------------------------------------------
    def list_orders(self, status: Optional[str], q: str, since: Optional[str], until: Optional[str], limit: int, offset: int) -> tuple[list[dict], int]:
        query = self._db.table("orders").select("*, order_items(*)", count="exact")
        if status:
            query = query.eq("status", status)
        if since:
            query = query.gte("created_at", since)
        if until:
            query = query.lte("created_at", until)
        term = _search_term(q)
        if term:
            query = query.or_(f"email.ilike.*{term}*,customer_name.ilike.*{term}*")
        res = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
        return res.data, res.count or 0

    def get_order(self, order_id: str) -> Optional[dict]:
        rows = self._db.table("orders").select("*, order_items(*)").eq("id", order_id).limit(1).execute().data
        return rows[0] if rows else None

    # -- users ----------------------------------------------------------------------
    def list_users(self, q: str, limit: int, offset: int) -> tuple[list[dict], int]:
        query = self._db.table("profiles").select(
            "user_id, email, full_name, phone, dob, email_verified, is_unsubscribed, created_at", count="exact"
        )
        term = _search_term(q)
        if term:
            query = query.or_(f"email.ilike.*{term}*,full_name.ilike.*{term}*,phone.ilike.*{term}*")
        res = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
        return res.data, res.count or 0

    def get_profile(self, user_id: str) -> Optional[dict]:
        rows = self._db.table("profiles").select("*").eq("user_id", user_id).limit(1).execute().data
        return rows[0] if rows else None

    def user_orders(self, user_id: str) -> list[dict]:
        return self._db.table("orders").select("*, order_items(*)").eq("user_id", user_id).order("created_at", desc=True).execute().data

    def user_bookings(self, user_id: str) -> list[dict]:
        return self._db.table("bookings").select("*").eq("user_id", user_id).order("booking_date", desc=True).execute().data

    def delete_user(self, user_id: str) -> None:
        self._db.auth.admin.delete_user(user_id)  # orders/bookings keep their rows (user_id set null)

    # -- bookings & availability ----------------------------------------------------
    def list_bookings(self, status: Optional[str], since: Optional[str], until: Optional[str], limit: int, offset: int) -> tuple[list[dict], int]:
        query = self._db.table("bookings").select("*", count="exact")
        if status:
            query = query.eq("status", status)
        if since:
            query = query.gte("booking_date", since)
        if until:
            query = query.lte("booking_date", until)
        res = query.order("booking_date").order("slot").range(offset, offset + limit - 1).execute()
        return res.data, res.count or 0

    def update_booking(self, booking_id: str, status: str) -> Optional[dict]:
        rows = self._db.table("bookings").update({"status": status}).eq("id", booking_id).execute().data
        return rows[0] if rows else None

    def list_availability(self, since: str, until: str) -> list[dict]:
        return self._db.table("availability").select("*").gte("day", since).lte("day", until).order("day").execute().data

    def set_availability(self, day: str, blocked_slots: list[str]) -> None:
        if blocked_slots:
            self._db.table("availability").upsert({"day": day, "blocked_slots": blocked_slots}, on_conflict="day").execute()
        else:
            self._db.table("availability").delete().eq("day", day).execute()

    # -- gallery --------------------------------------------------------------------
    def list_gallery(self, kind: Optional[str]) -> list[dict]:
        query = self._db.table("gallery").select("*")
        if kind:
            query = query.eq("type", kind)
        return query.order("created_at", desc=True).execute().data

    def add_gallery(self, url: str, title: str, kind: str) -> dict:
        return self._db.table("gallery").insert({"url": url, "title": title, "type": kind}).execute().data[0]

    def delete_gallery(self, item_id: str) -> bool:
        return bool(self._db.table("gallery").delete().eq("id", item_id).execute().data)

    # -- newsletters ----------------------------------------------------------------
    def create_newsletter(self, subject: str, body_html: str) -> dict:
        return self._db.table("newsletters").insert({"subject": subject, "body_html": body_html}).execute().data[0]

    def list_newsletters(self) -> list[dict]:
        return self._db.table("newsletters").select("id, subject, status, audience_count, sent_count, failed_count, created_at, started_at, finished_at").order("created_at", desc=True).limit(100).execute().data

    def get_newsletter(self, newsletter_id: str) -> Optional[dict]:
        rows = self._db.table("newsletters").select("*").eq("id", newsletter_id).limit(1).execute().data
        return rows[0] if rows else None

    def update_draft(self, newsletter_id: str, subject: str, body_html: str) -> Optional[dict]:
        rows = (
            self._db.table("newsletters").update({"subject": subject, "body_html": body_html})
            .eq("id", newsletter_id).eq("status", "draft").execute().data
        )
        return rows[0] if rows else None

    def delete_draft(self, newsletter_id: str) -> bool:
        return bool(self._db.table("newsletters").delete().eq("id", newsletter_id).eq("status", "draft").execute().data)

    def claim_send(self, newsletter_id: str, audience_count: int, stalled_before: datetime) -> bool:
        """Atomically move draft/partial (or an interrupted 'sending') to sending. False if someone else holds it."""
        rows = (
            self._db.table("newsletters")
            .update({"status": "sending", "audience_count": audience_count, "started_at": _iso(datetime.now(timezone.utc)), "finished_at": None})
            .eq("id", newsletter_id)
            .or_(f"status.in.(draft,partial),and(status.eq.sending,started_at.lt.{_iso(stalled_before)})")
            .execute().data
        )
        return bool(rows)

    def stalled_newsletters(self, stalled_before: datetime) -> list[dict]:
        return self._db.table("newsletters").select("id").eq("status", "sending").lt("started_at", _iso(stalled_before)).execute().data

    def finish_newsletter(self, newsletter_id: str, status: str, sent: int, failed: int) -> None:
        self._db.table("newsletters").update(
            {"status": status, "sent_count": sent, "failed_count": failed, "finished_at": _iso(datetime.now(timezone.utc))}
        ).eq("id", newsletter_id).execute()

    def count_log(self, prefix: str, status: str) -> int:
        res = self._db.table("email_log").select("id", count="exact").like("dedupe_key", f"{prefix}%").eq("status", status).limit(1).execute()
        return res.count or 0

    # -- newsletter audience --------------------------------------------------------
    def _all(self, build) -> list[dict]:
        rows: list[dict] = []
        while True:
            page = build().range(len(rows), len(rows) + _PAGE - 1).execute().data
            rows.extend(page)
            if len(page) < _PAGE:
                return rows

    def audience(self, since: datetime) -> list[dict]:
        """Verified, subscribed accounts that signed up or paid for something since `since`."""
        cols = "user_id, email, full_name"
        joined = self._all(lambda: self._db.table("profiles").select(cols).eq("email_verified", True).eq("is_unsubscribed", False).gte("created_at", _iso(since)).order("user_id"))
        buyers = self._all(lambda: self._db.table("orders").select("user_id").eq("status", "paid").gte("paid_at", _iso(since)).not_.is_("user_id", "null").order("id"))
        have = {p["user_id"] for p in joined}
        missing = sorted({b["user_id"] for b in buyers} - have)
        for i in range(0, len(missing), 200):
            joined += self._db.table("profiles").select(cols).in_("user_id", missing[i:i + 200]).eq("email_verified", True).eq("is_unsubscribed", False).execute().data
        return joined


_repo: Optional[AdminRepo] = None


def get_admin_repo() -> AdminRepo:
    global _repo
    if _repo is None:
        _repo = AdminRepo()
    return _repo
