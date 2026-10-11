"""Shared fakes. FakeOrdersRepo is installed for every test so nothing ever reaches a real Supabase."""

from __future__ import annotations

import pytest

from app.main import app
from app.modules.orders.repo import get_orders_repo


class FakeOrdersRepo:
    def __init__(self) -> None:
        self.orders: list[dict] = []
        self.order_items: list[dict] = []
        self.bookings: dict[str, dict] = {}
        self.removed_cart_lines: list[dict] = []
        self.blocked: dict[str, list[str]] = {}
        self.linked: list[tuple[str, str]] = []
        self.n = 0

    def create(self, order, items):
        self.n += 1
        row = {**order, "id": f"order-{self.n}", "status": "created", "razorpay_order_id": None, "razorpay_payment_id": None,
               "paid_at": None, "confirmation_emailed_at": None, "created_at": "2026-01-01T00:00:00Z"}
        self.orders.append(row)
        rows = [{**i, "id": f"oi-{self.n}-{k}", "order_id": row["id"]} for k, i in enumerate(items)]
        self.order_items.extend(rows)
        return {**row, "items": rows}

    def _order(self, **match):
        return next((o for o in self.orders if all(o.get(k) == v for k, v in match.items())), None)

    def attach_razorpay_order(self, order_id, rzp_id):
        self._order(id=order_id)["razorpay_order_id"] = rzp_id

    def mark_failed(self, order_id):
        o = self._order(id=order_id)
        if o["status"] == "created":
            o["status"] = "failed"

    def items(self, order_id):
        return [i for i in self.order_items if i["order_id"] == order_id]

    def by_razorpay_id(self, rzp_id):
        o = self._order(razorpay_order_id=rzp_id)
        return dict(o) if o else None

    def for_user(self, user_id, limit=100):
        return [{**o, "order_items": self.items(o["id"])} for o in self.orders if o["user_id"] == user_id and o["status"] == "paid"]

    def owned_item_ids(self, user_id):
        return [i["item_id"] for o in self.orders if o["user_id"] == user_id and o["status"] == "paid" for i in self.items(o["id"]) if i["type"] == "book"]

    def mark_paid(self, rzp_id, payment_id):
        o = self._order(razorpay_order_id=rzp_id)
        if not o or o["status"] == "paid":
            return None
        o.update(status="paid", razorpay_payment_id=payment_id, paid_at="2026-01-01T00:05:00Z")
        return dict(o)

    def ensure_bookings(self, order, items):
        made = [i for i in items if i["type"] == "service"]
        for i in made:
            self.bookings.setdefault(i["id"], {"date": i["preferred_date"], "slot": i["preferred_time"], "service": i["item_id"], "status": "pending"})
        return len(made)

    def remove_cart_lines(self, user_id, items):
        self.removed_cart_lines.extend(items)

    def claim_confirmation_email(self, order_id):
        o = self._order(id=order_id)
        if o["confirmation_emailed_at"]:
            return False
        o["confirmation_emailed_at"] = "now"
        return True

    def release_confirmation_email(self, order_id):
        self._order(id=order_id)["confirmation_emailed_at"] = None

    def link_guest_orders(self, user_id, email):
        self.linked.append((user_id, email))
        return 0

    def unavailable_slots(self, day):
        return sorted(self.blocked.get(day, []))


@pytest.fixture(autouse=True)
def orders_repo():
    repo = FakeOrdersRepo()
    app.dependency_overrides[get_orders_repo] = lambda: repo
    yield repo
    app.dependency_overrides.pop(get_orders_repo, None)
