import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.core import rate_limit
from app.core.config import settings
from app.core.scheduling import SLOTS, today_sgt
from app.main import app
from app.modules.auth.backend import get_backend
from app.modules.auth.dependencies import CurrentUser, get_current_user, optional_user
from app.modules.cart.repo import get_cart_repo
from app.modules.catalog.repo import get_catalog_repo
from app.modules.orders import service
from app.modules.orders.razorpay import RazorpayError, get_razorpay

KEY_SECRET, WEBHOOK_SECRET = "key-secret", "hook-secret"

CATALOG = [
    {"id": "book-1", "type": "book", "title": "The Numbers Say So", "subtitle": "S1", "price_minor": 2775, "currency": "SGD",
     "pdf_url": "https://secret/1.pdf", "tags": ["book"], "related_ids": ["svc-n"], "route": "/books/1", "requires_schedule": False, "sort_order": 1, "active": True},
    {"id": "book-2", "type": "book", "title": "The Numbers Say So", "subtitle": "S2", "price_minor": 2775, "currency": "SGD",
     "pdf_url": "https://secret/2.pdf", "tags": ["book"], "related_ids": ["book-1"], "route": "/books/2", "requires_schedule": False, "sort_order": 2, "active": True},
    {"id": "svc-n", "type": "service", "title": "Numerology Session", "subtitle": "", "price_minor": 9600, "currency": "SGD",
     "pdf_url": "", "tags": ["service"], "related_ids": [], "route": "/x", "requires_schedule": True, "sort_order": 20, "active": True},
    {"id": "retreat", "type": "retreat", "title": "Retreat", "subtitle": "", "price_minor": None, "currency": "SGD",
     "pdf_url": "", "tags": [], "related_ids": [], "route": "/retreats", "requires_schedule": False, "sort_order": 30, "active": True},
]


class FakeCatalog:
    def all_active(self):
        return CATALOG

    def get(self, item_id):
        return next((i for i in CATALOG if i["id"] == item_id), None)


class FakeCart:
    def __init__(self):
        self.rows = []

    def lines(self, user_id):
        return [r for r in self.rows if r["user_id"] == user_id]


class FakeBackend:
    def profile_by_id(self, user_id):
        return {"user_id": user_id, "full_name": "Asha Rao", "phone": "+65 8922 0656"}


class FakeRazorpay:
    def __init__(self):
        self.calls, self.fail = [], False

    async def create_order(self, amount_minor, currency, receipt, notes):
        if self.fail:
            raise RazorpayError("down")
        self.calls.append({"amount": amount_minor, "currency": currency, "receipt": receipt})
        return {"id": f"order_rzp{len(self.calls)}"}


def future(days=3):
    return (today_sgt() + timedelta(days=days)).isoformat()


def pay_sig(order_id, payment_id, secret=KEY_SECRET):
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


@pytest.fixture
def ctx(monkeypatch, orders_repo):
    rate_limit.reset_all()
    for name, value in (("razorpay_key_id", "rzp_test_x"), ("razorpay_key_secret", KEY_SECRET), ("razorpay_webhook_secret", WEBHOOK_SECRET)):
        monkeypatch.setattr(settings, name, value)
    cart, razorpay, sent = FakeCart(), FakeRazorpay(), []

    async def fake_send(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(service, "send_email", fake_send)
    app.dependency_overrides.update(
        {
            get_catalog_repo: lambda: FakeCatalog(), get_cart_repo: lambda: cart, get_backend: lambda: FakeBackend(),
            get_razorpay: lambda: razorpay, get_current_user: lambda: CurrentUser(id="user-1", email="a@b.c", access_token="t"),
        }
    )
    client = TestClient(app)
    client.cart, client.razorpay, client.sent, client.repo = cart, razorpay, sent, orders_repo
    yield client
    app.dependency_overrides.clear()


def fill_cart(client, *lines):
    client.cart.rows = [{"user_id": "user-1", "id": f"l{n}", "preferred_date": None, "preferred_time": None, **line} for n, line in enumerate(lines)]


def start(client):
    fill_cart(client, {"item_id": "book-1"}, {"item_id": "svc-n", "preferred_date": future(), "preferred_time": SLOTS[1]})
    r = client.post("/checkout")
    assert r.status_code == 200, r.text
    return r.json()


# ---- checkout -------------------------------------------------------------------------
def test_checkout_total_is_computed_server_side(ctx):
    body = start(ctx)
    assert body["amount"] == 2775 + 9600 and body["currency"] == "SGD"
    assert ctx.razorpay.calls == [{"amount": 12375, "currency": "SGD", "receipt": body["order_id"]}]
    assert body["prefill"]["name"] == "Asha Rao" and body["key_id"] == "rzp_test_x"
    assert "key_secret" not in json.dumps(body) and KEY_SECRET not in json.dumps(body)
    assert ctx.repo.orders[0]["status"] == "created" and ctx.repo.orders[0]["razorpay_order_id"] == body["razorpay_order_id"]


def test_checkout_ignores_client_supplied_amounts(ctx):
    fill_cart(ctx, {"item_id": "book-1"})
    assert ctx.post("/checkout", json={"amount": 1, "total_minor": 1}).json()["amount"] == 2775


def test_empty_cart_rejected(ctx):
    assert ctx.post("/checkout").status_code == 400


def test_stale_cart_line_rejected_and_nothing_created(ctx):
    fill_cart(ctx, {"item_id": "book-1"}, {"item_id": "svc-n", "preferred_date": (today_sgt() - timedelta(days=1)).isoformat(), "preferred_time": SLOTS[0]})
    assert ctx.post("/checkout").status_code == 400
    assert ctx.repo.orders == [] and ctx.razorpay.calls == []


def test_blocked_slot_is_a_conflict(ctx):
    fill_cart(ctx, {"item_id": "svc-n", "preferred_date": future(), "preferred_time": SLOTS[1]})
    ctx.repo.blocked[future()] = [SLOTS[1]]
    assert ctx.post("/checkout").status_code == 409
    assert ctx.repo.orders == []


def test_razorpay_outage_marks_order_failed(ctx):
    fill_cart(ctx, {"item_id": "book-1"})
    ctx.razorpay.fail = True
    assert ctx.post("/checkout").status_code == 502
    assert ctx.repo.orders[0]["status"] == "failed"


def test_direct_session_checkout_validates_and_skips_cart(ctx):
    ok = ctx.post("/checkout/session", json={"item_id": "svc-n", "preferred_date": future(), "preferred_time": SLOTS[0]})
    assert ok.status_code == 200 and ok.json()["amount"] == 9600 and ctx.repo.orders[0]["source"] == "direct"
    assert ctx.post("/checkout/session", json={"item_id": "book-1", "preferred_date": future(), "preferred_time": SLOTS[0]}).status_code == 400
    assert ctx.post("/checkout/session", json={"item_id": "svc-n", "preferred_date": future(), "preferred_time": "3:33 AM"}).status_code == 400


# ---- verify ---------------------------------------------------------------------------
def verify(ctx, body, payment_id="pay_1", signature=None):
    return ctx.post("/checkout/verify", json={
        "razorpay_order_id": body["razorpay_order_id"], "razorpay_payment_id": payment_id,
        "razorpay_signature": signature or pay_sig(body["razorpay_order_id"], payment_id),
    })


def test_bad_signature_does_not_mark_paid(ctx):
    body = start(ctx)
    assert verify(ctx, body, signature="0" * 64).status_code == 400
    assert ctx.repo.orders[0]["status"] == "created" and ctx.sent == []
    # a signature made with the wrong secret is rejected too
    assert verify(ctx, body, signature=pay_sig(body["razorpay_order_id"], "pay_1", "other")).status_code == 400


def test_verify_marks_paid_books_booking_clears_cart_and_emails_once(ctx):
    body = start(ctx)
    r = verify(ctx, body)
    assert r.status_code == 200 and r.json()["status"] == "paid"
    assert len(ctx.repo.bookings) == 1 and next(iter(ctx.repo.bookings.values()))["status"] == "pending"
    assert {i["item_id"] for i in ctx.repo.removed_cart_lines} == {"book-1", "svc-n"}
    assert len(ctx.sent) == 1 and ctx.sent[0]["to"] == "a@b.c" and "https://secret/1.pdf" in ctx.sent[0]["html"]
    assert [i["pdf_url"] for i in r.json()["items"] if i["type"] == "book"] == ["https://secret/1.pdf"]


def test_replaying_verify_is_idempotent(ctx):
    body = start(ctx)
    verify(ctx, body)
    assert verify(ctx, body).status_code == 200
    assert len(ctx.repo.bookings) == 1 and len(ctx.sent) == 1 and len(ctx.repo.removed_cart_lines) == 2


def test_cannot_verify_someone_elses_order(ctx):
    body = start(ctx)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="user-2", email="x@y.z", access_token="t")
    assert verify(ctx, body).status_code == 404
    assert ctx.repo.orders[0]["status"] == "created"


def test_failed_email_does_not_fail_payment_and_is_retried(ctx, monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("resend down")

    monkeypatch.setattr(service, "send_email", boom)
    body = start(ctx)
    assert verify(ctx, body).status_code == 200
    assert ctx.repo.orders[0]["status"] == "paid" and ctx.repo.orders[0]["confirmation_emailed_at"] is None
    monkeypatch.setattr(service, "send_email", lambda **kw: ctx.sent.append(kw) or _done())
    verify(ctx, body)  # replay (or the webhook) resends
    assert len(ctx.sent) == 1


async def _done():
    return None


# ---- webhook --------------------------------------------------------------------------
def hook(ctx, body, event="payment.captured", amount=None, currency="SGD", secret=WEBHOOK_SECRET, payment_id="pay_1"):
    payload = {"event": event, "payload": {"payment": {"entity": {
        "id": payment_id, "order_id": body["razorpay_order_id"], "amount": amount if amount is not None else body["amount"], "currency": currency}}}}
    raw = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return ctx.post("/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"})


def test_webhook_settles_order_when_customer_closed_the_tab(ctx):
    body = start(ctx)
    assert hook(ctx, body).json() == {"status": "ok"}
    assert ctx.repo.orders[0]["status"] == "paid" and len(ctx.repo.bookings) == 1 and len(ctx.sent) == 1


def test_webhook_after_verify_does_not_duplicate(ctx):
    body = start(ctx)
    verify(ctx, body)
    hook(ctx, body)
    hook(ctx, body)
    assert len(ctx.sent) == 1 and len(ctx.repo.bookings) == 1


def test_webhook_rejects_bad_or_missing_signature(ctx):
    body = start(ctx)
    assert hook(ctx, body, secret="wrong").status_code == 400
    assert ctx.post("/webhooks/razorpay", json={"event": "payment.captured"}).status_code == 400
    assert ctx.repo.orders[0]["status"] == "created"


def test_webhook_amount_mismatch_is_not_fulfilled(ctx):
    body = start(ctx)
    assert hook(ctx, body, amount=100).json() == {"status": "amount_mismatch"}
    assert hook(ctx, body, currency="USD").json() == {"status": "amount_mismatch"}
    assert ctx.repo.orders[0]["status"] == "created"


def test_webhook_ignores_other_events_and_unknown_orders(ctx):
    body = start(ctx)
    assert hook(ctx, body, event="refund.processed").json() == {"status": "ignored"}
    assert hook(ctx, {**body, "razorpay_order_id": "order_nope"}).json() == {"status": "unknown_order"}
    assert ctx.repo.orders[0]["status"] == "created"


# ---- history, availability, recommendations ------------------------------------------
def test_my_orders_lists_only_paid_orders_of_this_user(ctx):
    body = start(ctx)
    assert ctx.get("/orders").json()["orders"] == []
    verify(ctx, body)
    orders = ctx.get("/orders").json()["orders"]
    assert len(orders) == 1 and orders[0]["total_minor"] == 12375
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="user-2", email="x@y.z", access_token="t")
    assert ctx.get("/orders").json()["orders"] == []


def test_orders_require_login():
    app.dependency_overrides.pop(get_current_user, None)
    c = TestClient(app)
    assert c.get("/orders").status_code == 401 and c.post("/checkout").status_code == 401
    assert c.post("/checkout/verify", json={"razorpay_order_id": "a", "razorpay_payment_id": "b", "razorpay_signature": "c"}).status_code == 401


def test_availability_reports_blocked_slots(ctx):
    ctx.repo.blocked[future()] = [SLOTS[0]]
    slots = ctx.get(f"/availability?date={future()}").json()["slots"]
    assert [s["time"] for s in slots] == SLOTS and slots[0]["available"] is False and all(s["available"] for s in slots[1:])
    assert ctx.get(f"/availability?date={future(90)}").status_code == 400
    assert ctx.get("/availability?date=2001-01-01").status_code == 400


def test_recommendations_skip_books_the_user_owns(ctx):
    app.dependency_overrides[optional_user] = lambda: CurrentUser(id="user-1", email="a@b.c", access_token="t")
    body = start(ctx)  # cart: book-1 + svc-n; buy it so book-1 is owned
    verify(ctx, body)
    ids = [r["item"]["id"] for r in ctx.post("/cart/recommendations", json={"item_ids": ["book-2"]}).json()["recommendations"]]
    assert "book-1" not in ids
    app.dependency_overrides.pop(optional_user)
    guest_ids = [r["item"]["id"] for r in ctx.post("/cart/recommendations", json={"item_ids": ["book-2"]}).json()["recommendations"]]
    assert "book-1" in guest_ids

