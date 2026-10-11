"""Order persistence (service-role Supabase client; every user query is scoped by user_id)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.core.supabase_client import get_service_client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrdersRepo:
    def __init__(self) -> None:
        self._db = get_service_client()

    # -- creation ---------------------------------------------------------------------
    def create(self, order: dict, items: list[dict]) -> dict:
        row = self._db.table("orders").insert(order).execute().data[0]
        rows = self._db.table("order_items").insert([{**i, "order_id": row["id"]} for i in items]).execute().data
        return {**row, "items": rows}

    def attach_razorpay_order(self, order_id: str, razorpay_order_id: str) -> None:
        self._db.table("orders").update({"razorpay_order_id": razorpay_order_id}).eq("id", order_id).execute()

    def mark_failed(self, order_id: str) -> None:
        self._db.table("orders").update({"status": "failed"}).eq("id", order_id).eq("status", "created").execute()

    # -- lookup -----------------------------------------------------------------------
    def items(self, order_id: str) -> list[dict]:
        return self._db.table("order_items").select("*").eq("order_id", order_id).execute().data

    def by_razorpay_id(self, razorpay_order_id: str) -> Optional[dict]:
        rows = self._db.table("orders").select("*").eq("razorpay_order_id", razorpay_order_id).limit(1).execute().data
        return rows[0] if rows else None

    def for_user(self, user_id: str, limit: int = 100) -> list[dict]:
        return (
            self._db.table("orders").select("*, order_items(*)").eq("user_id", user_id).eq("status", "paid")
            .order("paid_at", desc=True).limit(limit).execute().data
        )

    def owned_item_ids(self, user_id: str) -> list[str]:
        rows = (
            self._db.table("orders").select("order_items(item_id, type)").eq("user_id", user_id).eq("status", "paid")
            .execute().data
        )
        return [i["item_id"] for r in rows for i in r["order_items"] if i["type"] == "book"]

    # -- payment fulfilment (idempotent) ---------------------------------------------
    def mark_paid(self, razorpay_order_id: str, payment_id: str) -> Optional[dict]:
        """Atomically flips created/failed -> paid. Returns the row only for the caller that won
        the flip; a replay (verify + webhook, or a retry) gets None."""
        rows = (
            self._db.table("orders")
            .update({"status": "paid", "razorpay_payment_id": payment_id, "paid_at": _now()})
            .eq("razorpay_order_id", razorpay_order_id).neq("status", "paid").execute().data
        )
        return rows[0] if rows else None

    def ensure_bookings(self, order: dict, items: list[dict]) -> int:
        """Create one pending booking per scheduled line. Unique(order_item_id) makes this safe to repeat."""
        bookings = [
            {
                "order_item_id": i["id"], "user_id": order.get("user_id"), "email": order["email"],
                "name": order.get("customer_name", ""), "phone": order.get("phone", ""),
                "service_id": i["item_id"], "service_title": i["title"],
                "booking_date": i["preferred_date"], "slot": i["preferred_time"],
            }
            for i in items if i["type"] == "service" and i.get("preferred_date") and i.get("preferred_time")
        ]
        if bookings:
            self._db.table("bookings").upsert(bookings, on_conflict="order_item_id", ignore_duplicates=True).execute()
        return len(bookings)

    def remove_cart_lines(self, user_id: str, items: list[dict]) -> None:
        """Remove only the purchased lines, so anything added to the cart mid-payment survives."""
        for i in items:
            q = self._db.table("cart_items").delete().eq("user_id", user_id).eq("item_id", i["item_id"])
            q = q.eq("preferred_date", i["preferred_date"]) if i.get("preferred_date") else q.is_("preferred_date", "null")
            q = q.eq("preferred_time", i["preferred_time"]) if i.get("preferred_time") else q.is_("preferred_time", "null")
            q.execute()

    def claim_confirmation_email(self, order_id: str) -> bool:
        rows = (
            self._db.table("orders").update({"confirmation_emailed_at": _now()})
            .eq("id", order_id).is_("confirmation_emailed_at", "null").execute().data
        )
        return bool(rows)

    def release_confirmation_email(self, order_id: str) -> None:
        self._db.table("orders").update({"confirmation_emailed_at": None}).eq("id", order_id).execute()

    # -- accounts ---------------------------------------------------------------------
    def link_guest_orders(self, user_id: str, email: str) -> int:
        rows = (
            self._db.table("orders").update({"user_id": user_id})
            .is_("user_id", "null").ilike("email", email).execute().data
        )
        return len(rows)

    # -- availability -----------------------------------------------------------------
    def unavailable_slots(self, day: str) -> list[str]:
        blocked = self._db.table("availability").select("blocked_slots").eq("day", day).limit(1).execute().data
        taken = (
            self._db.table("bookings").select("slot").eq("booking_date", day).eq("status", "confirmed").execute().data
        )
        return sorted({*(blocked[0]["blocked_slots"] if blocked else []), *(b["slot"] for b in taken)})


_repo: OrdersRepo | None = None


def get_orders_repo() -> OrdersRepo:
    global _repo
    if _repo is None:
        _repo = OrdersRepo()
    return _repo
