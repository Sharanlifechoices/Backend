import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core import rate_limit
from app.core.config import settings
from app.main import app
from app.modules.jobs import service
from app.modules.jobs.repo import get_email_log, get_jobs_repo
from app.modules.admin.repo import get_admin_repo
from app.modules.unsubscribe.repo import get_suppression_repo

NOW = datetime(2026, 10, 11, 4, 0, tzinfo=timezone.utc)


class FakeLog:
    def __init__(self):
        self.rows = {}

    def claim(self, key, kind, recipient):
        row = self.rows.get(key)
        if row is None:
            self.rows[key] = {"status": "pending", "kind": kind, "to": recipient}
            return True
        if row["status"] == "failed":
            row["status"] = "pending"
            return True
        return False

    def mark_sent(self, key, provider_id):
        self.rows[key]["status"] = "sent"

    def mark_failed(self, key, error):
        self.rows[key]["status"] = "failed"


class FakeSuppressions:
    def __init__(self):
        self.emails = set()

    def suppressed_among(self, emails):
        return {e.lower() for e in emails if e.lower() in self.emails}


class FakeJobsRepo:
    def __init__(self):
        self.orders, self.birthdays, self.stale, self.deleted = [], {}, [], []
        self.guest_birthdays = {}

    def paid_orders_between(self, since, until):
        return [o for o in self.orders if since <= o["paid_at"] <= until]

    def birthday_profiles(self, month, day):
        return self.birthdays.get((month, day), [])

    def birthday_guest_buyers(self, month, day):
        return self.guest_birthdays.get((month, day), [])

    def stale_unverified(self, cutoff):
        return [s for s in self.stale if s["created_at"] < cutoff]

    def delete_user(self, user_id):
        if user_id == "boom":
            raise RuntimeError("auth down")
        self.deleted.append(user_id)


@pytest.fixture
def env(monkeypatch):
    sent = []

    async def fake_send(**kw):
        if kw["to"] == "fail@x.com":
            raise RuntimeError("resend 500")
        sent.append(kw)
        return type("R", (), {"provider_id": "mock"})()

    monkeypatch.setattr(service, "send_email", fake_send)
    monkeypatch.setattr(service, "today_sgt", lambda: date(2026, 10, 11))
    return FakeJobsRepo(), FakeLog(), sent, FakeSuppressions()


def order(oid, days_ago, email="a@b.c", user="u1"):
    return {"id": oid, "user_id": user, "email": email, "customer_name": "Asha", "paid_at": NOW - timedelta(days=days_ago, minutes=5),
            "order_items": [{"title": "The Numbers Say So"}]}


def run(repo, log, supp=None, **kw):
    return asyncio.run(service.run_daily(repo, log, supp or FakeSuppressions(), now=NOW, **kw))


def test_followup_goes_out_once_for_orders_a_week_old(env):
    repo, log, sent, supp = env
    repo.orders = [order("o7", 7), order("o3", 3), order("o30", 30)]
    out = run(repo, log)
    assert out["followups"]["sent"] == 1 and [s["kind"] for s in sent] == ["followup"] and "followup:o7" in log.rows
    assert "Chronicle" in sent[0]["html"] and "?book=1" in sent[0]["html"]
    run(repo, log)  # cron retry the same day
    assert len(sent) == 1


def test_followup_catches_up_a_missed_day_but_not_older(env):
    repo, log, sent, supp = env
    repo.orders = [order("o8", 8), order("o89", 8.9), order("o10", 10), order("o12", 12)]
    assert run(repo, log)["followups"]["sent"] == 2


def test_unsubscribed_users_are_skipped_but_guest_orders_are_not(env):
    repo, log, sent, supp = env
    supp.emails = {"a@b.c"}
    repo.orders = [order("o1", 7, user="u1"), order("o2", 7, email="guest@x.com", user=None), order("o3", 7, email="Gone@X.com", user=None)]
    supp.emails.add("gone@x.com")  # a guest purchaser who unsubscribed by link
    out = run(repo, log, supp)["followups"]
    assert out["sent"] == 1 and out["skipped"] == 2 and sent[0]["to"] == "guest@x.com"


def test_failed_send_is_retried_next_run(env):
    repo, log, sent, supp = env
    repo.orders = [order("o1", 7, email="fail@x.com")]
    assert run(repo, log)["followups"]["failed"] == 1 and log.rows["followup:o1"]["status"] == "failed"
    repo.orders[0]["email"] = "ok@x.com"
    assert run(repo, log)["followups"]["sent"] == 1 and log.rows["followup:o1"]["status"] == "sent"


def test_birthdays_once_per_year(env):
    repo, log, sent, supp = env
    repo.birthdays = {(10, 11): [{"user_id": "u1", "email": "a@b.c", "full_name": "Asha Rao"}]}
    assert run(repo, log)["birthdays"]["sent"] == 1 and sent[0]["kind"] == "birthday" and "Namaste, Asha," in sent[0]["html"]
    run(repo, log)
    assert len(sent) == 1 and "birthday:u1:2026" in log.rows


def test_leap_day_birthdays_are_celebrated_on_feb_28_in_common_years():
    assert service.birthday_dates(date(2027, 2, 28)) == [(2, 28), (2, 29)]
    assert service.birthday_dates(date(2028, 2, 28)) == [(2, 28)]
    assert service.birthday_dates(date(2026, 10, 11)) == [(10, 11)]


def test_cleanup_deletes_only_stale_unverified_and_survives_errors(env):
    repo, log, _, supp = env
    repo.stale = [
        {"user_id": "old", "email": "o@x.com", "created_at": NOW - timedelta(days=8)},
        {"user_id": "boom", "email": "b@x.com", "created_at": NOW - timedelta(days=9)},
        {"user_id": "new", "email": "n@x.com", "created_at": NOW - timedelta(days=2)},
    ]
    assert run(repo, log)["cleanup"] == {"deleted": 1, "failed": 1} and repo.deleted == ["old"]


def test_one_crashing_job_does_not_stop_the_others(env):
    repo, log, sent, supp = env
    repo.birthday_profiles = lambda m, d: (_ for _ in ()).throw(RuntimeError("db down"))
    repo.orders = [order("o7", 7)]
    out = run(repo, log)
    assert out["birthdays"] == {"error": "RuntimeError"} and out["followups"]["sent"] == 1


def test_time_budget_stops_early_and_next_run_finishes(env):
    repo, log, sent, supp = env
    repo.orders = [order(f"o{i}", 7) for i in range(3)]
    out = run(repo, log, budget_seconds=-1)
    assert out["followups"].get("stopped_early") and len(sent) == 0
    assert run(repo, log)["followups"]["sent"] == 3


# ---- endpoint ---------------------------------------------------------------------------
@pytest.fixture
def client(env, monkeypatch):
    rate_limit.reset_all()
    repo, log, _, supp = env
    monkeypatch.setattr(settings, "jobs_secret", "s3cret-jobs")
    app.dependency_overrides[get_jobs_repo] = lambda: repo
    app.dependency_overrides[get_email_log] = lambda: log
    app.dependency_overrides[get_suppression_repo] = lambda: supp
    app.dependency_overrides[get_admin_repo] = lambda: type("A", (), {"stalled_newsletters": lambda self, before: []})()
    yield TestClient(app)
    app.dependency_overrides.pop(get_jobs_repo, None)
    app.dependency_overrides.pop(get_email_log, None)
    app.dependency_overrides.pop(get_suppression_repo, None)
    app.dependency_overrides.pop(get_admin_repo, None)


def test_daily_endpoint_requires_the_secret(client):
    assert client.post("/jobs/daily").status_code == 403
    assert client.post("/jobs/daily", headers={"X-Jobs-Secret": "wrong"}).status_code == 403
    r = client.post("/jobs/daily", headers={"X-Jobs-Secret": "s3cret-jobs"})
    assert r.status_code == 200 and {"birthdays", "followups", "cleanup", "duration_ms"} <= set(r.json())


def test_endpoint_rejects_everything_when_no_secret_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "jobs_secret", "")
    assert client.post("/jobs/daily", headers={"X-Jobs-Secret": ""}).status_code == 403


def test_repeated_bad_secrets_lock_out(client):
    codes = [client.post("/jobs/daily", headers={"X-Jobs-Secret": "x"}).status_code for _ in range(12)]
    assert codes[0] == 403 and codes[-1] == 429


def test_marketing_mails_carry_a_working_unsubscribe_link_and_headers(env):
    from app.core.unsubscribe import parse_token

    repo, log, sent, supp = env
    repo.orders = [order("o1", 7, email="Guest@X.com", user=None)]
    repo.birthdays = {(10, 11): [{"user_id": "u1", "email": "a@b.c", "full_name": "Asha"}]}
    run(repo, log)
    assert len(sent) == 2
    for mail in sent:
        assert "Unsubscribe" in mail["html"] and mail["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        url = mail["headers"]["List-Unsubscribe"].strip("<>")
        assert parse_token(url.split("token=")[1].replace("%2E", ".")) == mail["to"].lower()


def test_birthday_goes_to_buyers_without_an_account_unless_unsubscribed(env):
    repo, log, sent, supp = env
    repo.guest_birthdays = {(10, 11): [
        {"email": "guest@x.com", "customer_name": "Gita"},
        {"email": "optout@x.com", "customer_name": "Omar"},
    ]}
    supp.emails = {"optout@x.com"}
    out = run(repo, log, supp)["birthdays"]
    assert out["sent"] == 1 and out["skipped"] == 1 and sent[0]["to"] == "guest@x.com" and "Gita" in sent[0]["html"]
    run(repo, log, supp)
    assert len(sent) == 1 and "birthday:guest@x.com:2026" in log.rows  # once per year
