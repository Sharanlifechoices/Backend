import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core import rate_limit
from app.core.config import settings
from app.main import app
from app.modules.jobs import service
from app.modules.jobs.repo import get_email_log, get_jobs_repo

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


class FakeJobsRepo:
    def __init__(self):
        self.orders, self.unsub, self.birthdays, self.stale, self.deleted = [], set(), {}, [], []

    def paid_orders_between(self, since, until):
        return [o for o in self.orders if since <= o["paid_at"] <= until]

    def unsubscribed_among(self, ids):
        return {i for i in ids if i in self.unsub}

    def birthday_profiles(self, month, day):
        return self.birthdays.get((month, day), [])

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
    return FakeJobsRepo(), FakeLog(), sent


def order(oid, days_ago, email="a@b.c", user="u1"):
    return {"id": oid, "user_id": user, "email": email, "customer_name": "Asha", "paid_at": NOW - timedelta(days=days_ago, minutes=5),
            "order_items": [{"title": "The Numbers Say So"}]}


def run(repo, log, **kw):
    return asyncio.run(service.run_daily(repo, log, now=NOW, **kw))


def test_followup_goes_out_once_for_orders_a_week_old(env):
    repo, log, sent = env
    repo.orders = [order("o7", 7), order("o3", 3), order("o30", 30)]
    out = run(repo, log)
    assert out["followups"]["sent"] == 1 and [s["kind"] for s in sent] == ["followup"] and "followup:o7" in log.rows
    assert "The Numbers Say So" in sent[0]["html"]
    run(repo, log)  # cron retry the same day
    assert len(sent) == 1


def test_followup_catches_up_a_missed_day_but_not_older(env):
    repo, log, sent = env
    repo.orders = [order("o8", 8), order("o89", 8.9), order("o10", 10), order("o12", 12)]
    assert run(repo, log)["followups"]["sent"] == 2


def test_unsubscribed_users_are_skipped_but_guest_orders_are_not(env):
    repo, log, sent = env
    repo.unsub = {"u1"}
    repo.orders = [order("o1", 7, user="u1"), order("o2", 7, email="guest@x.com", user=None)]
    out = run(repo, log)["followups"]
    assert out["sent"] == 1 and out["skipped"] == 1 and sent[0]["to"] == "guest@x.com"


def test_failed_send_is_retried_next_run(env):
    repo, log, sent = env
    repo.orders = [order("o1", 7, email="fail@x.com")]
    assert run(repo, log)["followups"]["failed"] == 1 and log.rows["followup:o1"]["status"] == "failed"
    repo.orders[0]["email"] = "ok@x.com"
    assert run(repo, log)["followups"]["sent"] == 1 and log.rows["followup:o1"]["status"] == "sent"


def test_birthdays_once_per_year(env):
    repo, log, sent = env
    repo.birthdays = {(10, 11): [{"user_id": "u1", "email": "a@b.c", "full_name": "Asha Rao"}]}
    assert run(repo, log)["birthdays"]["sent"] == 1 and sent[0]["kind"] == "birthday" and "Asha Rao" in sent[0]["html"]
    run(repo, log)
    assert len(sent) == 1 and "birthday:u1:2026" in log.rows


def test_leap_day_birthdays_are_celebrated_on_feb_28_in_common_years():
    assert service.birthday_dates(date(2027, 2, 28)) == [(2, 28), (2, 29)]
    assert service.birthday_dates(date(2028, 2, 28)) == [(2, 28)]
    assert service.birthday_dates(date(2026, 10, 11)) == [(10, 11)]


def test_cleanup_deletes_only_stale_unverified_and_survives_errors(env):
    repo, log, _ = env
    repo.stale = [
        {"user_id": "old", "email": "o@x.com", "created_at": NOW - timedelta(days=8)},
        {"user_id": "boom", "email": "b@x.com", "created_at": NOW - timedelta(days=9)},
        {"user_id": "new", "email": "n@x.com", "created_at": NOW - timedelta(days=2)},
    ]
    assert run(repo, log)["cleanup"] == {"deleted": 1, "failed": 1} and repo.deleted == ["old"]


def test_one_crashing_job_does_not_stop_the_others(env):
    repo, log, sent = env
    repo.birthday_profiles = lambda m, d: (_ for _ in ()).throw(RuntimeError("db down"))
    repo.orders = [order("o7", 7)]
    out = run(repo, log)
    assert out["birthdays"] == {"error": "RuntimeError"} and out["followups"]["sent"] == 1


def test_time_budget_stops_early_and_next_run_finishes(env):
    repo, log, sent = env
    repo.orders = [order(f"o{i}", 7) for i in range(3)]
    out = run(repo, log, budget_seconds=-1)
    assert out["followups"].get("stopped_early") and len(sent) == 0
    assert run(repo, log)["followups"]["sent"] == 3


# ---- endpoint ---------------------------------------------------------------------------
@pytest.fixture
def client(env, monkeypatch):
    rate_limit.reset_all()
    repo, log, _ = env
    monkeypatch.setattr(settings, "jobs_secret", "s3cret-jobs")
    app.dependency_overrides[get_jobs_repo] = lambda: repo
    app.dependency_overrides[get_email_log] = lambda: log
    yield TestClient(app)
    app.dependency_overrides.pop(get_jobs_repo, None)
    app.dependency_overrides.pop(get_email_log, None)


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
