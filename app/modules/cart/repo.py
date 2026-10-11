"""Cart persistence (service-role Supabase client; ownership is enforced in every query)."""

from __future__ import annotations

from datetime import date
from typing import Optional

from postgrest.exceptions import APIError

from app.core.supabase_client import get_service_client

_UNIQUE_VIOLATION = "23505"


class CartRepo:
    def __init__(self) -> None:
        self._db = get_service_client()

    def lines(self, user_id: str) -> list[dict]:
        return (
            self._db.table("cart_items").select("*").eq("user_id", user_id).order("created_at").execute().data
        )

    def find_line(self, user_id: str, item_id: str, preferred_date: Optional[date], preferred_time: Optional[str]) -> Optional[dict]:
        query = self._db.table("cart_items").select("*").eq("user_id", user_id).eq("item_id", item_id)
        query = query.eq("preferred_date", preferred_date.isoformat()) if preferred_date else query.is_("preferred_date", "null")
        query = query.eq("preferred_time", preferred_time) if preferred_time else query.is_("preferred_time", "null")
        rows = query.limit(1).execute().data
        return rows[0] if rows else None

    def add_line(self, user_id: str, item_id: str, preferred_date: Optional[date], preferred_time: Optional[str]) -> dict:
        """Idempotent: adding the same line twice returns the existing one."""
        existing = self.find_line(user_id, item_id, preferred_date, preferred_time)
        if existing:
            return existing
        try:
            return self._db.table("cart_items").insert(
                {
                    "user_id": user_id,
                    "item_id": item_id,
                    "preferred_date": preferred_date.isoformat() if preferred_date else None,
                    "preferred_time": preferred_time,
                }
            ).execute().data[0]
        except APIError as exc:
            if getattr(exc, "code", "") == _UNIQUE_VIOLATION:  # concurrent insert of the same line
                return self.find_line(user_id, item_id, preferred_date, preferred_time)  # type: ignore[return-value]
            raise

    def update_line(self, user_id: str, line_id: str, preferred_date: date, preferred_time: str) -> Optional[dict]:
        try:
            rows = (
                self._db.table("cart_items")
                .update({"preferred_date": preferred_date.isoformat(), "preferred_time": preferred_time})
                .eq("id", line_id).eq("user_id", user_id).execute().data
            )
        except APIError as exc:
            if getattr(exc, "code", "") == _UNIQUE_VIOLATION:
                return {"conflict": True}
            raise
        return rows[0] if rows else None

    def get_line(self, user_id: str, line_id: str) -> Optional[dict]:
        rows = self._db.table("cart_items").select("*").eq("id", line_id).eq("user_id", user_id).limit(1).execute().data
        return rows[0] if rows else None

    def remove_line(self, user_id: str, line_id: str) -> bool:
        rows = self._db.table("cart_items").delete().eq("id", line_id).eq("user_id", user_id).execute().data
        return bool(rows)

    def clear(self, user_id: str) -> None:
        self._db.table("cart_items").delete().eq("user_id", user_id).execute()


_repo: CartRepo | None = None


def get_cart_repo() -> CartRepo:
    global _repo
    if _repo is None:
        _repo = CartRepo()
    return _repo
