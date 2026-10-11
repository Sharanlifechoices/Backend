from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core import rate_limit
from app.core.config import settings
from app.core.scheduling import SLOTS
from app.main import app
from app.modules.admin import newsletter as nl
from app.modules.admin.repo import AdminRepo, get_admin_repo, _search_term
from app.modules.auth.backend import get_backend
from app.modules.catalog.repo import get_catalog_repo
from app.modules.jobs.repo import get_email_log
from app.modules.orders import service as order_service
from app.modules.unsubscribe.repo import get_suppression_repo

from tests.test_jobs import FakeLog
from tests.test_orders import FakeCatalog

SECRET = "admin-secret-for-tests"
H = {"X-Admin-Secret": SECRET}


class FakeSupp:
    def __init__(self):
        self.emails = set()

    def suppressed_among(self, emails):
        return {e.lower() for e in emails if e.lower() in self.emails}


class FakeAdminRepo:
    def __init__(self, log):
        self.log = log
        self.calls = []
        self.people = []
        self.orders = {}
        self.profiles = {}
        self.bookings = {"b1": {"id": "b1", "status": "pending"}}
        self.gallery = []
        self.newsletters = {}
        self.availability = {}
        self.deleted_users = []

    # purchases
    def list_orders(self, status, q, since, until, limit, offset):
        self.calls.append(("list_orders", status, q, since, until))
        return list(self.orders.values()), len(self.orders)

    def get_order(self, order_id):
        return self.orders.get(order_id)

    # users
    def list_users(self, q, limit, offset):
        return list(self.profiles.values()), len(self.profiles)

    def get_profile(self, user_id):
        p = self.profiles.get(user_id)
        return dict(p) if p else None

    def user_orders(self, user_id):
        return []

    def user_bookings(self, user_id):
        return []

    def delete_user(self, user_id):
        self.deleted_users.append(user_id)

    # bookings / availability
    def list_bookings(self, status, since, until, limit, offset):
        return list(self.bookings.values()), len(self.bookings)

    def update_booking(self, booking_id, status):
        b = self.bookings.get(booking_id)
        if b:
            b["status"] = status
        return b

    def list_availability(self, since, until):
        return [{"day": d, "blocked_slots": s} for d, s in self.availability.items()]

    def set_availability(self, day, slots):
        if slots:
            self.availability[day] = slots
        else:
            self.availability.pop(day, None)

    # gallery
    def list_gallery(self, kind):
        return [g for g in self.gallery if not kind or g["type"] == kind]

    def add_gallery(self, url, title, kind):
        row = {"id": f"g{len(self.gallery) + 1}", "url": url, "title": title, "type": kind, "created_at": "2026-01-01"}
        self.gallery.append(row)
        return row

    def delete_gallery(self, item_id):
        before = len(self.gallery)
        self.gallery = [g for g in self.gallery if g["id"] != item_id]
        return len(self.gallery) < before

    # newsletters
    def create_newsletter(self, subject, body_html):
        row = {"id": f"n{len(self.newsletters) + 1}", "subject": subject, "body_html": body_html, "status": "draft",
               "audience_count": None, "sent_count": 0, "failed_count": 0, "started_at": None}
        self.newsletters[row["id"]] = row
        return row

    def list_newsletters(self):
        return list(self.newsletters.values())

    def get_newsletter(self, nid):
        return self.newsletters.get(nid)

    def update_draft(self, nid, subject, body_html):
        n = self.newsletters.get(nid)
        if not n or n["status"] != "draft":
            return None
        n.update(subject=subject, body_html=body_html)
        return n

    def delete_draft(self, nid):
        n = self.newsletters.get(nid)
        if n and n["status"] == "draft":
            del self.newsletters[nid]
            return True
        return False

    def claim_send(self, nid, audience_count, stalled_before):
        n = self.newsletters[nid]
        stalled = n["status"] == "sending" and n["started_at"] and n["started_at"] < stalled_before
        if n["status"] in ("draft", "partial") or stalled:
            n.update(status="sending", audience_count=audience_count, started_at=datetime.now(timezone.utc))
            return True
        return False

    def stalled_newsletters(self, stalled_before):
        return [{"id": n["id"]} for n in self.newsletters.values() if n["status"] == "sending" and n["started_at"] and n["started_at"] < stalled_before]

    def finish_newsletter(self, nid, status, sent, failed):
        self.newsletters[nid].update(status=status, sent_count=sent, failed_count=failed)

    def count_log(self, prefix, status):
        return sum(1 for k, v in self.log.rows.items() if k.startswith(prefix) and v["status"] == status)

    def audience(self, since):
        return self.people


class FakeBackend:
    def __init__(self):
        self.passwords = {}

    def set_password(self, user_id, password):
        self.passwords[user_id] = password


@pytest.fixture
def ctx(monkeypatch):
    rate_limit.reset_all()
    monkeypatch.setattr(settings, "admin_secret", SECRET)
    log, supp, backend, sent = FakeLog(), FakeSupp(), FakeBackend(), []
    repo = FakeAdminRepo(log)

    async def fake_send(**kw):
        if kw["to"].startswith("fail"):
            raise RuntimeError("resend 500")
        sent.append(kw)
        return type("R", (), {"provider_id": "mock"})()

    monkeypatch.setattr(nl, "send_email", fake_send)
    monkeypatch.setattr("app.modules.jobs.service.send_email", fake_send)
    monkeypatch.setattr(order_service, "send_email", fake_send)
    app.dependency_overrides.update({
        get_admin_repo: lambda: repo, get_email_log: lambda: log, get_suppression_repo: lambda: supp,
        get_backend: lambda: backend, get_catalog_repo: lambda: FakeCatalog(),
    })
    c = TestClient(app)
    c.repo, c.log, c.supp, c.backend, c.sent = repo, log, supp, backend, sent
    yield c
    for dep in (get_admin_repo, get_email_log, get_suppression_repo, get_backend, get_catalog_repo):
        app.dependency_overrides.pop(dep, None)


# ---- guard ----------------------------------------------------------------------------
ADMIN_ROUTES = [
    ("GET", "/admin/orders"), ("GET", "/admin/orders/x"), ("POST", "/admin/orders/x/resend-confirmation"),
    ("GET", "/admin/users"), ("GET", "/admin/users/x"), ("PUT", "/admin/users/x/password"), ("DELETE", "/admin/users/x"),
    ("GET", "/admin/bookings"), ("PATCH", "/admin/bookings/x"), ("GET", "/admin/availability"), ("PUT", "/admin/availability/2026-01-01"),
    ("GET", "/admin/gallery"), ("POST", "/admin/gallery"), ("DELETE", "/admin/gallery/x"),
    ("GET", "/admin/newsletter/audience"), ("GET", "/admin/newsletters"), ("POST", "/admin/newsletters"), ("GET", "/admin/newsletters/x"),
    ("PUT", "/admin/newsletters/x"), ("DELETE", "/admin/newsletters/x"), ("POST", "/admin/newsletters/x/test"), ("POST", "/admin/newsletters/x/send"),
]


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_every_admin_route_needs_the_secret(ctx, method, path):
    assert ctx.request(method, path).status_code == 403
    assert ctx.request(method, path, headers={"X-Admin-Secret": "nope"}).status_code == 403


def test_admin_is_closed_when_no_secret_is_configured(ctx, monkeypatch):
    monkeypatch.setattr(settings, "admin_secret", "")
    assert ctx.get("/admin/users", headers={"X-Admin-Secret": ""}).status_code == 403


def test_repeated_bad_secrets_lock_out_even_the_right_one_is_not_needed(ctx):
    codes = [ctx.get("/admin/users", headers={"X-Admin-Secret": "x"}).status_code for _ in range(12)]
    assert codes[0] == 403 and codes[-1] == 429


# ---- purchases ------------------------------------------------------------------------
def test_orders_listing_defaults_to_paid_and_passes_filters(ctx):
    ctx.repo.orders = {"o1": {"id": "o1", "status": "paid"}}
    r = ctx.get("/admin/orders?q=asha&since=2026-01-01&until=2026-01-31", headers=H)
    assert r.status_code == 200 and r.json()["total"] == 1
    assert ctx.repo.calls[-1] == ("list_orders", "paid", "asha", "2026-01-01", "2026-02-01")  # until is inclusive
    assert ctx.get("/admin/orders?status=bogus", headers=H).status_code == 422
    ctx.get("/admin/orders?status=all", headers=H)
    assert ctx.repo.calls[-1][1] is None  # "all" means no status filter
    assert ctx.get("/admin/orders/missing", headers=H).status_code == 404


def test_search_term_cannot_inject_into_the_postgrest_filter():
    assert _search_term("a,or=(id.neq.0)*%") == "aorid.neq.0"
    assert "," not in _search_term("x,y") and "(" not in _search_term("(x)")


def test_resend_confirmation_sends_the_email_again(ctx, orders_repo):
    order = {"id": "o1", "status": "paid", "email": "a@b.c", "customer_name": "Asha", "total_minor": 2775, "currency": "SGD",
             "order_items": [{"item_id": "book-1", "type": "book", "title": "The Numbers Say So", "preferred_date": None, "preferred_time": None}]}
    ctx.repo.orders["o1"] = order
    orders_repo.orders.append({"id": "o1", "confirmation_emailed_at": "earlier"})
    assert ctx.post("/admin/orders/o1/resend-confirmation", headers=H).json() == {"sent": True}
    assert len(ctx.sent) == 1 and ctx.sent[0]["to"] == "a@b.c" and "https://secret/1.pdf" in ctx.sent[0]["html"]
    ctx.repo.orders["o2"] = {**order, "id": "o2", "status": "created"}
    assert ctx.post("/admin/orders/o2/resend-confirmation", headers=H).status_code == 404


# ---- users ----------------------------------------------------------------------------
def test_admin_sets_a_password_that_meets_the_policy(ctx):
    ctx.repo.profiles["u1"] = {"user_id": "u1", "email": "a@b.c"}
    weak = ctx.put("/admin/users/u1/password", headers=H, json={"password": "short"})
    assert weak.status_code == 422 and "short" not in weak.text and ctx.backend.passwords == {}
    assert ctx.put("/admin/users/u1/password", headers=H, json={"password": "alllowercase!"}).status_code == 422
    ok = ctx.put("/admin/users/u1/password", headers=H, json={"password": "NewStr0ng!pass"})
    assert ok.status_code == 200 and ctx.backend.passwords == {"u1": "NewStr0ng!pass"}
    assert ctx.put("/admin/users/ghost/password", headers=H, json={"password": "NewStr0ng!pass"}).status_code == 404


def test_user_detail_hides_the_unsubscribe_token_and_delete_works(ctx):
    ctx.repo.profiles["u1"] = {"user_id": "u1", "email": "a@b.c", "unsubscribe_token": "secret-token"}
    body = ctx.get("/admin/users/u1", headers=H)
    assert body.status_code == 200 and "secret-token" not in body.text and set(body.json()) == {"user", "orders", "bookings"}
    assert ctx.delete("/admin/users/u1", headers=H).status_code == 204 and ctx.repo.deleted_users == ["u1"]
    assert ctx.delete("/admin/users/ghost", headers=H).status_code == 404


def test_admin_password_never_reaches_the_logs(ctx, caplog):
    import logging

    ctx.repo.profiles["u1"] = {"user_id": "u1", "email": "a@b.c"}
    with caplog.at_level(logging.DEBUG):
        ctx.put("/admin/users/u1/password", headers=H, json={"password": "NewStr0ng!pass"})
    assert "NewStr0ng!pass" not in " ".join(r.getMessage() + str(getattr(r, "data", "")) for r in caplog.records)


# ---- bookings / availability / gallery -------------------------------------------------
def test_booking_status_and_availability_validation(ctx):
    assert ctx.patch("/admin/bookings/b1", headers=H, json={"status": "confirmed"}).json()["status"] == "confirmed"
    assert ctx.patch("/admin/bookings/b1", headers=H, json={"status": "weird"}).status_code == 422
    assert ctx.patch("/admin/bookings/nope", headers=H, json={"status": "confirmed"}).status_code == 404

    assert ctx.put("/admin/availability/2026-03-01", headers=H, json={"blocked_slots": [SLOTS[2], SLOTS[0]]}).json()["blocked_slots"] == [SLOTS[0], SLOTS[2]]
    assert ctx.put("/admin/availability/2026-03-01", headers=H, json={"blocked_slots": ["3:33 AM"]}).status_code == 422
    assert ctx.put("/admin/availability/2026-03-01", headers=H, json={"blocked_slots": []}).status_code == 200 and ctx.repo.availability == {}
    assert ctx.get("/admin/availability?since=2026-01-01&until=2027-01-01", headers=H).status_code == 400


def test_gallery_admin_and_public_listing(ctx):
    assert ctx.post("/admin/gallery", headers=H, json={"url": "http://insecure/x.jpg", "title": "x"}).status_code == 422
    assert ctx.post("/admin/gallery", headers=H, json={"url": "https://res.cloudinary.com/a.jpg", "title": "Sunrise"}).status_code == 201
    ctx.post("/admin/gallery", headers=H, json={"url": "https://res.cloudinary.com/b.jpg", "title": "Retreat", "type": "retreat"})
    public = ctx.get("/gallery?type=retreat").json()["items"]  # no secret needed, no internal fields
    assert [i["title"] for i in public] == ["Retreat"] and "created_at" not in public[0]
    assert ctx.delete("/admin/gallery/g1", headers=H).status_code == 204 and ctx.delete("/admin/gallery/g1", headers=H).status_code == 404


# ---- newsletter -----------------------------------------------------------------------
def people(*emails):
    return [{"user_id": f"u{i}", "email": e, "full_name": "N"} for i, e in enumerate(emails)]


def draft(ctx):
    return ctx.post("/admin/newsletters", headers=H, json={"subject": "October news", "body_html": "<p>Hello</p>"}).json()["id"]


def test_audience_dedupes_and_drops_unsubscribed(ctx):
    ctx.repo.people = people("A@x.com", "a@x.com", "b@x.com", "gone@x.com")
    ctx.supp.emails = {"gone@x.com"}
    body = ctx.get("/admin/newsletter/audience", headers=H).json()
    assert body["count"] == 2 and body["sample"] == ["a@x.com", "b@x.com"] and body["window_days"] == 365


def test_send_needs_the_confirmed_audience_size_then_sends_once_to_each(ctx):
    ctx.repo.people = people("a@x.com", "b@x.com")
    nid = draft(ctx)
    wrong = ctx.post(f"/admin/newsletters/{nid}/send", headers=H, json={"confirm_audience": 5})
    assert wrong.status_code == 409 and wrong.json()["detail"]["audience"] == 2 and ctx.sent == []
    ok = ctx.post(f"/admin/newsletters/{nid}/send", headers=H, json={"confirm_audience": 2})
    assert ok.status_code == 202
    assert sorted(m["to"] for m in ctx.sent) == ["a@x.com", "b@x.com"]
    done = ctx.get(f"/admin/newsletters/{nid}", headers=H).json()
    assert done["status"] == "sent" and done["sent_count"] == 2 and done["failed_count"] == 0
    for mail in ctx.sent:
        assert "Unsubscribe" in mail["html"] and "List-Unsubscribe" in mail["headers"] and "October news" == mail["subject"]
    assert ctx.post(f"/admin/newsletters/{nid}/send", headers=H, json={"confirm_audience": 2}).status_code == 409  # already sent


def test_partial_failure_is_resumable_without_double_sending(ctx):
    ctx.repo.people = people("a@x.com", "fail@x.com")
    nid = draft(ctx)
    ctx.post(f"/admin/newsletters/{nid}/send", headers=H, json={"confirm_audience": 2})
    row = ctx.get(f"/admin/newsletters/{nid}", headers=H).json()
    assert row["status"] == "partial" and row["sent_count"] == 1 and row["failed_count"] == 1
    ctx.repo.people = people("a@x.com", "fixed@x.com")  # the bad address was corrected / replaced
    ctx.post(f"/admin/newsletters/{nid}/send", headers=H, json={"confirm_audience": 2})
    assert sorted(m["to"] for m in ctx.sent) == ["a@x.com", "fixed@x.com"]  # a@x.com was NOT mailed again


def test_an_in_flight_send_cannot_be_started_twice_but_a_stalled_one_can_resume(ctx):
    ctx.repo.people = people("a@x.com")
    nid = draft(ctx)
    ctx.repo.newsletters[nid].update(status="sending", started_at=datetime.now(timezone.utc))
    assert ctx.post(f"/admin/newsletters/{nid}/send", headers=H, json={"confirm_audience": 1}).status_code == 409
    ctx.repo.newsletters[nid]["started_at"] = datetime.now(timezone.utc) - timedelta(hours=1)  # process died an hour ago
    assert ctx.post(f"/admin/newsletters/{nid}/send", headers=H, json={"confirm_audience": 1}).status_code == 202
    assert [m["to"] for m in ctx.sent] == ["a@x.com"]


def test_daily_job_finishes_a_stalled_newsletter(ctx):
    import asyncio

    from tests.test_jobs import FakeJobsRepo, FakeSuppressions

    ctx.repo.people = people("a@x.com", "b@x.com")
    nid = draft(ctx)
    ctx.repo.newsletters[nid].update(status="sending", started_at=datetime.now(timezone.utc) - timedelta(hours=2))
    from app.modules.jobs import service

    out = asyncio.run(service.run_daily(FakeJobsRepo(), ctx.log, ctx.supp, admin=ctx.repo))
    assert out["newsletters"] == {"resumed": 1} and ctx.repo.newsletters[nid]["status"] == "sent" and len(ctx.sent) == 2


def test_only_drafts_can_be_edited_or_deleted_and_test_send_works(ctx):
    nid = draft(ctx)
    assert ctx.put(f"/admin/newsletters/{nid}", headers=H, json={"subject": "New subject", "body_html": "<p>x</p>"}).json()["subject"] == "New subject"
    assert ctx.post(f"/admin/newsletters/{nid}/test", headers=H, json={"to": "me@x.com"}).json() == {"sent": True}
    assert ctx.sent[-1]["to"] == "me@x.com" and ctx.sent[-1]["subject"].startswith("[TEST]") and ctx.log.rows == {}  # not recorded
    ctx.repo.newsletters[nid]["status"] = "sent"
    assert ctx.put(f"/admin/newsletters/{nid}", headers=H, json={"subject": "s", "body_html": "b"}).status_code == 409
    assert ctx.delete(f"/admin/newsletters/{nid}", headers=H).status_code == 409
    assert ctx.post("/admin/newsletters/nope/test", headers=H, json={"to": "me@x.com"}).status_code == 404
    assert ctx.post("/admin/newsletters", headers=H, json={"subject": "", "body_html": "x"}).status_code == 422
