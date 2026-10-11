import asyncio
import json
import logging

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core import rate_limit
from app.core.admin_auth import require_admin
from app.core.config import settings
from app.core.email import send_email
from app.core.logging_setup import REDACTED, JsonFormatter, redact
from app.core.passwords import password_failures
from app.core.request_logging import RequestLoggingMiddleware


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []
        self.setFormatter(JsonFormatter())

    def emit(self, record):
        self.lines.append(self.format(record))


@pytest.fixture
def captured():
    handler = ListHandler()
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    yield handler
    root.removeHandler(handler)


def make_app():
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.post("/login")
    async def login(body: dict):
        return {"ok": True, "access_token": "abc.def.ghi"}

    @app.get("/admin-only", dependencies=[Depends(require_admin)])
    async def admin_only():
        return {"ok": True}

    return app


def test_redact_masks_sensitive_keys_recursively():
    out = redact({"email": "a@b.c", "password": "Hunter2!", "nested": {"otp": "123456", "refresh_token": "x"}, "code": "999"})
    assert out["email"] == "a@b.c"
    assert out["password"] == REDACTED
    assert out["nested"]["otp"] == REDACTED and out["nested"]["refresh_token"] == REDACTED
    assert out["code"] == REDACTED


def test_redact_masks_bearer_in_strings():
    assert "abc123" not in redact("Authorization failed for Bearer abc123.def")


def test_requests_and_responses_are_logged_without_secrets(captured):
    client = TestClient(make_app())
    resp = client.post("/login", json={"email": "a@b.c", "password": "Sup3rSecret!"}, headers={"Authorization": "Bearer topsecrettoken"})
    assert resp.status_code == 200
    assert resp.headers["x-request-id"]
    joined = "\n".join(captured.lines)
    assert "--> POST /login" in joined and "<-- 200 POST /login" in joined
    for secret in ("Sup3rSecret!", "topsecrettoken", "abc.def.ghi"):
        assert secret not in joined
    assert "a@b.c" in joined  # non-sensitive data is preserved
    # every line carries the same request id
    http_records = [json.loads(l) for l in captured.lines if json.loads(l)["logger"] == "http"]
    assert len(http_records) == 2  # one request line, one response line
    ids = {r["request_id"] for r in http_records}
    assert ids == {resp.headers["x-request-id"]}


def test_mock_email_logs_instead_of_sending(captured, monkeypatch):
    monkeypatch.setattr(settings, "email_mode", "mock")
    result = asyncio.run(send_email(to="x@y.z", subject="Hi", html="<b>123456</b>", kind="otp"))
    assert result.mode == "mock"
    assert any("[MOCK EMAIL]" in l and "123456" in l for l in captured.lines)


@pytest.mark.parametrize(
    "pw,expected_count",
    [("Abcdef1!", 0), ("abcdef1!", 1), ("ABCDEF1!", 1), ("Abcdefg1", 1), ("Ab1!", 1), ("abc", 3)],
)
def test_password_policy(pw, expected_count):
    assert len(password_failures(pw)) == expected_count


def test_admin_secret_and_lockout(monkeypatch):
    rate_limit.reset_all()
    monkeypatch.setattr(settings, "admin_secret", "s3cret-value")
    client = TestClient(make_app())
    assert client.get("/admin-only").status_code == 403
    assert client.get("/admin-only", headers={"X-Admin-Secret": "wrong"}).status_code == 403
    assert client.get("/admin-only", headers={"X-Admin-Secret": "s3cret-value"}).status_code == 200
    for _ in range(12):
        client.get("/admin-only", headers={"X-Admin-Secret": "wrong"})
    assert client.get("/admin-only", headers={"X-Admin-Secret": "wrong"}).status_code == 429


def test_admin_rejects_when_unconfigured(monkeypatch):
    rate_limit.reset_all()
    monkeypatch.setattr(settings, "admin_secret", "")
    assert TestClient(make_app()).get("/admin-only", headers={"X-Admin-Secret": ""}).status_code == 403


def test_client_ip_uses_rightmost_forwarded_entry():
    # a spoofed leftmost value must not win over what the proxy appended
    assert rate_limit.forwarded_client_ip("6.6.6.6, 203.0.113.9", "10.0.0.1") == "203.0.113.9"
    assert rate_limit.forwarded_client_ip(None, "10.0.0.1") == "10.0.0.1"
    assert rate_limit.forwarded_client_ip("", None) == "unknown"


def test_health_endpoint():
    from app.main import app

    assert TestClient(app).get("/health").json()["status"] == "ok"


# ---- outgoing hooks on an AsyncClient (Resend and Razorpay use one) -------------------
def test_async_client_hooks_log_and_redact_without_crashing(caplog):
    import asyncio
    import logging

    import httpx

    from app.core.http_logging import async_hooks_for

    def handler(request):
        return httpx.Response(200, json={"id": "order_1", "api_key": "SECRET-RESPONSE"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), event_hooks=async_hooks_for("razorpay")) as client:
            return await client.post("https://api.razorpay.com/v1/orders", json={"amount": 100, "token": "SECRET-REQUEST"},
                                     headers={"Authorization": "Basic abc"})

    with caplog.at_level(logging.INFO, logger="outgoing"):
        resp = asyncio.run(run())
    assert resp.json()["id"] == "order_1"  # body still readable after the hook consumed it
    text = " ".join(str(getattr(r, "data", "")) + r.getMessage() for r in caplog.records)
    assert "razorpay" in text and "SECRET-REQUEST" not in text and "SECRET-RESPONSE" not in text and "Basic abc" not in text
