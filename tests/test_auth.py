import json
import logging
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core import rate_limit
from app.core.config import settings
from app.main import app
from app.modules.auth import backend as be
from app.modules.auth.backend import get_backend
from app.modules.auth.otp import OtpCooldown, OtpError, OtpRecord, OtpService
from app.modules.auth.router import get_otp_service

GOOD = {"full_name": "Asha Rao", "email": "Asha@Example.com", "dob": "1990-05-17", "phone": "+65 8922 0656", "password": "Str0ng!pass"}


class MemoryOtpStore:
    def __init__(self):
        self.rows: list[OtpRecord] = []
        self.n = 0

    def latest(self, email, purpose):
        rows = [r for r in self.rows if r.email == email and r.purpose == purpose]
        return rows[-1] if rows else None

    def invalidate_active(self, email, purpose):
        for r in self.rows:
            if r.email == email and r.purpose == purpose and r.consumed_at is None:
                r.consumed_at = datetime.now(timezone.utc)

    def insert(self, email, purpose, code_hash, expires_at):
        self.n += 1
        rec = OtpRecord(str(self.n), email, purpose, code_hash, 0, expires_at, None, datetime.now(timezone.utc))
        self.rows.append(rec)
        return rec

    def record_attempt(self, otp_id):
        next(r for r in self.rows if r.id == otp_id).attempts += 1

    def consume(self, otp_id):
        next(r for r in self.rows if r.id == otp_id).consumed_at = datetime.now(timezone.utc)


class FakeBackend:
    def __init__(self):
        self.users: dict[str, dict] = {}  # email -> {id, password, confirmed, meta}

    def _by_id(self, uid):
        return next(u for u in self.users.values() if u["id"] == uid)

    def profile_by_email(self, email):
        u = self.users.get(email)
        return {"user_id": u["id"], "email": email, **u["meta"]} if u else None

    def profile_by_id(self, uid):
        u = self._by_id(uid)
        return {"user_id": uid, "email": next(e for e, x in self.users.items() if x is u), **u["meta"]}

    def update_profile(self, uid, fields):
        self._by_id(uid)["meta"].update({k: str(v) for k, v in fields.items()})
        return self.profile_by_id(uid)

    def create_unconfirmed_user(self, email, password, meta):
        if email in self.users:
            raise be.DuplicateUser()
        self.users[email] = {"id": f"uid-{len(self.users) + 1}", "password": password, "confirmed": False, "meta": dict(meta)}
        return self.users[email]["id"]

    def update_pending_user(self, uid, password, meta):
        u = self._by_id(uid)
        u["password"], u["meta"] = password, dict(meta)

    def is_confirmed(self, uid):
        return self._by_id(uid)["confirmed"]

    def confirm_email(self, uid):
        self._by_id(uid)["confirmed"] = True

    def set_password(self, uid, password):
        self._by_id(uid)["password"] = password

    def login(self, email, password):
        u = self.users.get(email)
        if not u or u["password"] != password:
            raise be.InvalidCredentials()
        if not u["confirmed"]:
            raise be.EmailNotVerified()
        return {"access_token": "acc", "refresh_token": "ref-token-123", "expires_in": 3600, "token_type": "bearer", "user_id": u["id"]}

    def refresh(self, token):
        if token != "ref-token-123":
            raise be.InvalidRefreshToken()
        return {"access_token": "acc2", "refresh_token": "ref-token-456", "expires_in": 3600, "token_type": "bearer"}

    def sign_out(self, token):
        pass


@pytest.fixture
def ctx(monkeypatch):
    rate_limit.reset_all()
    monkeypatch.setattr(settings, "email_mode", "mock")
    backend, store = FakeBackend(), MemoryOtpStore()
    clock = {"now": datetime.now(timezone.utc)}
    otp = OtpService(store, now=lambda: clock["now"])
    app.dependency_overrides[get_backend] = lambda: backend
    app.dependency_overrides[get_otp_service] = lambda: otp
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record):
            data = getattr(record, "data", None) or {}
            if data.get("kind", "").startswith("otp"):
                records.append(re.search(r">(\d{6})<", data["html"]).group(1))

    handler = Capture()
    logging.getLogger("email").addHandler(handler)
    logging.getLogger("email").setLevel(logging.INFO)
    yield TestClient(app), backend, store, clock, records
    logging.getLogger("email").removeHandler(handler)
    app.dependency_overrides.clear()


def advance(clock, seconds):
    clock["now"] += timedelta(seconds=seconds)


# ---- OTP rules ------------------------------------------------------------------------
def test_otp_roundtrip_and_single_use():
    svc = OtpService(MemoryOtpStore())
    code = svc.issue("a@b.c", "signup")
    assert re.fullmatch(r"\d{6}", code)
    svc.verify("a@b.c", "signup", code)
    with pytest.raises(OtpError):
        svc.verify("a@b.c", "signup", code)  # consumed


def test_otp_purposes_are_isolated():
    svc = OtpService(MemoryOtpStore())
    code = svc.issue("a@b.c", "signup")
    with pytest.raises(OtpError):
        svc.verify("a@b.c", "reset", code)


def test_otp_expiry_and_cooldown_and_attempts():
    clock = {"now": datetime.now(timezone.utc)}
    svc = OtpService(MemoryOtpStore(), now=lambda: clock["now"])
    code = svc.issue("a@b.c", "signup")
    with pytest.raises(OtpCooldown):
        svc.issue("a@b.c", "signup")
    advance(clock, settings.otp_ttl_seconds + 1)
    with pytest.raises(OtpError) as e:
        svc.verify("a@b.c", "signup", code)
    assert e.value.kind == "invalid_or_expired"

    code = svc.issue("a@b.c", "signup")
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(settings.otp_max_attempts - 1):
        with pytest.raises(OtpError) as e:
            svc.verify("a@b.c", "signup", wrong)
        assert e.value.kind == "invalid_code"
    with pytest.raises(OtpError) as e:
        svc.verify("a@b.c", "signup", wrong)
    assert e.value.kind == "too_many_attempts"
    with pytest.raises(OtpError):
        svc.verify("a@b.c", "signup", code)  # correct code is dead after the burn


def test_new_code_invalidates_old_one():
    clock = {"now": datetime.now(timezone.utc)}
    svc = OtpService(MemoryOtpStore(), now=lambda: clock["now"])
    first = svc.issue("a@b.c", "signup")
    advance(clock, settings.otp_resend_cooldown_seconds + 1)
    second = svc.issue("a@b.c", "signup")
    if first != second:
        with pytest.raises(OtpError):
            svc.verify("a@b.c", "signup", first)
    svc.verify("a@b.c", "signup", second)


# ---- HTTP flows -----------------------------------------------------------------------
def test_signup_verify_login_flow(ctx):
    client, backend, store, clock, codes = ctx
    r = client.post("/auth/signup", json=GOOD)
    assert r.status_code == 202 and r.json()["email"] == "asha@example.com"
    assert len(codes) == 1
    # cannot log in before verifying
    r = client.post("/auth/login", json={"email": GOOD["email"], "password": GOOD["password"]})
    assert r.status_code == 403 and r.json()["detail"] == "email_not_verified"
    # wrong code then right code
    assert client.post("/auth/verify-otp", json={"email": GOOD["email"], "code": "abcdef"}).status_code == 422
    r = client.post("/auth/verify-otp", json={"email": GOOD["email"], "code": codes[0]})
    assert r.status_code == 200 and r.json() == {"verified": True}
    r = client.post("/auth/login", json={"email": GOOD["email"], "password": GOOD["password"]})
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"] and body["user"]["email"] == "asha@example.com" and "user_id" not in body


def test_signup_password_policy_errors_are_specific(ctx):
    client, *_ = ctx
    r = client.post("/auth/signup", json={**GOOD, "password": "alllowercase"})
    assert r.status_code == 422
    msg = json.dumps(r.json())
    assert "uppercase" in msg and "special" in msg and "lowercase letter" not in msg  # only failing rules listed
    assert "alllowercase" not in msg  # the submitted password is never echoed back


@pytest.mark.parametrize(
    "override",
    [{"phone": "abc"}, {"dob": "2999-01-01"}, {"dob": "1800-01-01"}, {"full_name": "A"}, {"email": "not-an-email"}],
)
def test_signup_field_validation(ctx, override):
    client, *_ = ctx
    assert client.post("/auth/signup", json={**GOOD, **override}).status_code == 422


def test_signup_duplicate_confirmed_account(ctx):
    client, backend, store, clock, codes = ctx
    client.post("/auth/signup", json=GOOD)
    client.post("/auth/verify-otp", json={"email": GOOD["email"], "code": codes[0]})
    r = client.post("/auth/signup", json=GOOD)
    assert r.status_code == 409


def test_signup_unverified_retry_reissues_code_and_updates_password(ctx):
    client, backend, store, clock, codes = ctx
    client.post("/auth/signup", json=GOOD)
    advance(clock, settings.otp_resend_cooldown_seconds + 1)
    r = client.post("/auth/signup", json={**GOOD, "password": "An0ther!pass"})
    assert r.status_code == 202 and len(codes) == 2
    assert backend.users["asha@example.com"]["password"] == "An0ther!pass"


def test_resend_cooldown_returns_429(ctx):
    client, *_ = ctx
    client.post("/auth/signup", json=GOOD)
    r = client.post("/auth/resend-otp", json={"email": GOOD["email"]})
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_resend_does_not_enumerate(ctx):
    client, *_ = ctx
    r = client.post("/auth/resend-otp", json={"email": "nobody@example.com"})
    assert r.status_code == 202


def test_login_wrong_password_is_generic_401(ctx):
    client, backend, store, clock, codes = ctx
    client.post("/auth/signup", json=GOOD)
    client.post("/auth/verify-otp", json={"email": GOOD["email"], "code": codes[0]})
    for email, pw in ((GOOD["email"], "Wrong!pass1"), ("nobody@example.com", "Wrong!pass1")):
        r = client.post("/auth/login", json={"email": email, "password": pw})
        assert r.status_code == 401 and r.json()["detail"] == "Invalid email or password"


def test_forgot_and_reset_password_flow(ctx):
    client, backend, store, clock, codes = ctx
    client.post("/auth/signup", json=GOOD)
    client.post("/auth/verify-otp", json={"email": GOOD["email"], "code": codes[0]})
    # unknown email: clear "no account" answer, no email
    n = len(codes)
    unknown = client.post("/auth/forgot-password", json={"email": "nobody@example.com"})
    assert unknown.status_code == 404 and "couldn't find an account" in unknown.json()["detail"]
    assert len(codes) == n
    # known email
    assert client.post("/auth/forgot-password", json={"email": GOOD["email"]}).status_code == 202
    assert len(codes) == n + 1
    # asking again inside the resend cooldown is told to wait (not silently "sent")
    again = client.post("/auth/forgot-password", json={"email": GOOD["email"]})
    assert again.status_code == 429 and "wait" in again.json()["detail"] and len(codes) == n + 1
    # weak new password rejected before the code is spent
    weak = client.post("/auth/reset-password", json={"email": GOOD["email"], "code": codes[-1], "new_password": "weak"})
    assert weak.status_code == 422
    ok = client.post("/auth/reset-password", json={"email": GOOD["email"], "code": codes[-1], "new_password": "N3w!password"})
    assert ok.status_code == 200
    assert client.post("/auth/login", json={"email": GOOD["email"], "password": "N3w!password"}).status_code == 200
    assert client.post("/auth/login", json={"email": GOOD["email"], "password": GOOD["password"]}).status_code == 401
    # a signup code cannot be replayed as a reset code
    assert client.post("/auth/reset-password", json={"email": GOOD["email"], "code": codes[0], "new_password": "N3w!password"}).status_code == 400


def test_refresh(ctx):
    client, *_ = ctx
    assert client.post("/auth/refresh", json={"refresh_token": "ref-token-123"}).json()["access_token"] == "acc2"
    assert client.post("/auth/refresh", json={"refresh_token": "bogus-token-xx"}).status_code == 401


def test_me_requires_auth(ctx):
    client, *_ = ctx
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_me_get_and_patch(ctx, monkeypatch):
    client, backend, *_ = ctx
    backend.create_unconfirmed_user("asha@example.com", "x", {"full_name": "Asha Rao", "phone": "+6589220656", "dob": "1990-05-17"})
    from app.modules.auth import dependencies, router

    monkeypatch.setattr(dependencies, "decode_token", lambda token: {"sub": "uid-1", "email": "asha@example.com"})
    headers = {"Authorization": "Bearer anything"}
    assert client.get("/me", headers=headers).json()["full_name"] == "Asha Rao"
    r = client.patch("/me", headers=headers, json={"phone": "+6511112222"})
    assert r.status_code == 200 and r.json()["phone"] == "+6511112222"
    assert client.patch("/me", headers=headers, json={"dob": "2999-01-01"}).status_code == 422
    assert client.patch("/me", headers=headers, json={}).status_code == 400


def test_logs_never_contain_passwords_or_codes(ctx):
    client, backend, store, clock, codes = ctx
    captured: list[str] = []

    class H(logging.Handler):
        def emit(self, record):
            from app.core.logging_setup import JsonFormatter

            captured.append(JsonFormatter().format(record))

    h = H()
    root = logging.getLogger()
    root.addHandler(h)
    root.setLevel(logging.INFO)
    try:
        client.post("/auth/signup", json={**GOOD, "email": "weak@example.com", "password": "weakpassword"})  # 422 path
        client.post("/auth/signup", json=GOOD)
        client.post("/auth/verify-otp", json={"email": GOOD["email"], "code": codes[0]})
        client.post("/auth/login", json={"email": GOOD["email"], "password": GOOD["password"]})
    finally:
        root.removeHandler(h)
    http_lines = "\n".join(l for l in captured if '"logger": "http"' in l)
    assert GOOD["password"] not in http_lines
    assert "weakpassword" not in http_lines  # not even via the validation-error response body
    assert codes[0] not in http_lines
    assert "ref-token-123" not in http_lines and '"acc"' not in http_lines
    assert "<-- 200 POST /auth/login" in http_lines


def test_verifying_email_links_guest_orders_and_survives_linking_failure(ctx, orders_repo):
    client, backend, store, clock, codes = ctx
    client.post("/auth/signup", json=GOOD)
    assert client.post("/auth/verify-otp", json={"email": GOOD["email"], "code": codes[0]}).status_code == 200
    assert len(orders_repo.linked) == 1 and orders_repo.linked[0][1] == "asha@example.com"

    def broken(user_id, email):
        raise RuntimeError("db down")

    orders_repo.link_guest_orders = broken
    client.post("/auth/signup", json={**GOOD, "email": "second@example.com"})
    assert client.post("/auth/verify-otp", json={"email": "second@example.com", "code": codes[1]}).status_code == 200


def test_forgot_password_for_an_unverified_signup_gets_no_code(ctx):
    client, backend, store, clock, codes = ctx
    client.post("/auth/signup", json=GOOD)  # signed up but never entered the code
    n = len(codes)
    r = client.post("/auth/forgot-password", json={"email": GOOD["email"]})
    assert r.status_code == 404 and len(codes) == n
