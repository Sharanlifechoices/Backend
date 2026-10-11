import pytest
from fastapi.testclient import TestClient

from app.core import rate_limit
from app.core.unsubscribe import make_token, parse_token, unsubscribe_headers, unsubscribe_url
from app.main import app
from app.modules.auth.dependencies import CurrentUser, get_current_user
from app.modules.unsubscribe.repo import get_suppression_repo


class FakeSuppressions:
    def __init__(self):
        self.emails = set()

    def suppress(self, email):
        self.emails.add(email.lower())

    def resubscribe(self, email):
        self.emails.discard(email.lower())


@pytest.fixture
def client():
    rate_limit.reset_all()
    repo = FakeSuppressions()
    app.dependency_overrides[get_suppression_repo] = lambda: repo
    c = TestClient(app)
    c.repo = repo
    yield c
    app.dependency_overrides.pop(get_suppression_repo, None)
    app.dependency_overrides.pop(get_current_user, None)


# ---- tokens ---------------------------------------------------------------------------
def test_token_round_trips_and_is_case_insensitive():
    assert parse_token(make_token("Asha@Example.com")) == "asha@example.com"


def test_tampered_or_foreign_tokens_are_rejected():
    good = make_token("a@b.c")
    email_part, sig = good.split(".")
    other_email_part = make_token("victim@x.com").split(".")[0]
    for bad in ("", "garbage", good + "x", f"{other_email_part}.{sig}", f"{email_part}.", ".", "a.b.c", "é.é"):
        assert parse_token(bad) is None, bad


def test_one_click_headers_point_at_the_signed_url():
    headers = unsubscribe_headers("a@b.c")
    assert headers["List-Unsubscribe"] == f"<{unsubscribe_url('a@b.c')}>"
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


# ---- endpoints ------------------------------------------------------------------------
def test_get_shows_confirmation_and_does_not_unsubscribe(client):
    r = client.get(f"/unsubscribe?token={make_token('a@b.c')}")
    assert r.status_code == 200 and "Yes, unsubscribe me" in r.text
    assert client.repo.emails == set()  # link scanners / prefetchers must not unsubscribe people


def test_post_unsubscribes_and_is_idempotent(client):
    token = make_token("Asha@Example.com")
    for _ in range(2):
        r = client.post(f"/unsubscribe?token={token}")
        assert r.status_code == 200 and "unsubscribed" in r.text
    assert client.repo.emails == {"asha@example.com"}


def test_one_click_post_from_a_mail_client_works(client):
    r = client.post(f"/unsubscribe?token={make_token('a@b.c')}", data={"List-Unsubscribe": "One-Click"})
    assert r.status_code == 200 and client.repo.emails == {"a@b.c"}


def test_invalid_token_is_rejected_without_side_effects(client):
    assert client.get("/unsubscribe?token=nope").status_code == 400
    assert client.post("/unsubscribe?token=nope").status_code == 400
    assert client.post("/unsubscribe").status_code == 422
    assert client.repo.emails == set()


def test_page_escapes_the_token_in_the_form_action(client):
    r = client.get('/unsubscribe?token="><script>alert(1)</script>')
    assert r.status_code == 400 and "<script>" not in r.text


def test_logged_in_user_can_unsubscribe_and_resubscribe_from_profile(client):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="u1", email="Asha@Example.com", access_token="t")
    assert client.put("/me/subscription", json={"subscribed": False}).json() == {"subscribed": False}
    assert client.repo.emails == {"asha@example.com"}
    assert client.put("/me/subscription", json={"subscribed": True}).json() == {"subscribed": True}
    assert client.repo.emails == set()


def test_subscription_requires_login(client):
    app.dependency_overrides.pop(get_current_user, None)
    assert client.put("/me/subscription", json={"subscribed": False}).status_code == 401
