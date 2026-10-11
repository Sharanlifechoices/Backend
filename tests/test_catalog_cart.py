from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core import rate_limit
from app.core.scheduling import SLOTS, today_sgt
from app.main import app
from app.modules.auth import dependencies
from app.modules.auth.dependencies import CurrentUser, get_current_user
from app.modules.cart.repo import get_cart_repo
from app.modules.catalog.recommend import recommend
from app.modules.catalog.repo import get_catalog_repo


def book(n, related=(), tags=("numerology", "book")):
    return {"id": f"book-{n}", "type": "book", "title": "The Numbers Say So", "subtitle": f"S{n}", "description": "", "price_minor": 2775,
            "currency": "SGD", "cover": "c", "pdf_url": f"https://secret/{n}.pdf", "tags": list(tags), "related_ids": list(related),
            "route": f"/books/{n}", "requires_schedule": False, "sort_order": n, "active": True}


def service(sid, order, related=(), tags=("service",)):
    return {"id": sid, "type": "service", "title": sid, "subtitle": "", "description": "", "price_minor": 9600, "currency": "SGD", "cover": "",
            "pdf_url": "", "tags": list(tags), "related_ids": list(related), "route": "/x", "requires_schedule": True, "sort_order": order, "active": True}


CATALOG = [
    book(1, related=["svc-numerology", "book-8"]),
    book(2, related=["svc-numerology", "book-6"]),
    book(8, related=["svc-numerology", "book-1"]),
    service("svc-numerology", 20, related=["book-1"], tags=("numerology", "service")),
    service("svc-chakra", 22, tags=("chakra", "service")),
    {**service("retreat", 30, tags=("retreat",)), "type": "retreat", "price_minor": None, "requires_schedule": False},
]


# ---- recommendations ------------------------------------------------------------------
def test_recommends_related_book_and_service_not_cart_items():
    picks = recommend(["book-1"], CATALOG)
    ids = [p["item"]["id"] for p in picks]
    assert "book-1" not in ids
    assert ids[0] in ("svc-numerology", "book-8")
    assert {"svc-numerology", "book-8"} <= set(ids)
    reasons = {p["item"]["id"]: p["reason"] for p in picks}
    assert reasons["book-8"] == "people_also_liked"
    assert reasons["svc-numerology"] == "explore_with_sharan"


def test_owned_books_are_excluded_but_services_are_not():
    ids = [p["item"]["id"] for p in recommend(["book-1"], CATALOG, owned_ids=["book-8", "svc-numerology"])]
    assert "book-8" not in ids
    assert "svc-numerology" in ids  # sessions can be booked repeatedly


def test_nonempty_cart_never_suggests_unrelated_items():
    ids = [p["item"]["id"] for p in recommend(["svc-chakra"], CATALOG)]
    assert "retreat" not in ids and "book-2" not in ids


def test_empty_cart_suggests_services_first_and_respects_limit():
    picks = recommend([], CATALOG, limit=2)
    assert len(picks) == 2 and all(p["item"]["type"] == "service" for p in picks)


# ---- HTTP ----------------------------------------------------------------------------
class FakeCatalog:
    def all_active(self):
        return CATALOG

    def get(self, item_id):
        return next((i for i in CATALOG if i["id"] == item_id), None)


class FakeCartRepo:
    def __init__(self):
        self.rows: list[dict] = []
        self.n = 0

    def lines(self, user_id):
        return [r for r in self.rows if r["user_id"] == user_id]

    def find_line(self, user_id, item_id, d, t):
        return next((r for r in self.rows if (r["user_id"], r["item_id"], r["preferred_date"], r["preferred_time"]) == (user_id, item_id, d.isoformat() if d else None, t)), None)

    def add_line(self, user_id, item_id, d, t):
        existing = self.find_line(user_id, item_id, d, t)
        if existing:
            return existing
        self.n += 1
        row = {"id": f"line-{self.n}", "user_id": user_id, "item_id": item_id, "preferred_date": d.isoformat() if d else None, "preferred_time": t}
        self.rows.append(row)
        return row

    def get_line(self, user_id, line_id):
        return next((r for r in self.rows if r["id"] == line_id and r["user_id"] == user_id), None)

    def update_line(self, user_id, line_id, d, t):
        row = self.get_line(user_id, line_id)
        if not row:
            return None
        if self.find_line(user_id, row["item_id"], d, t):
            return {"conflict": True}
        row.update(preferred_date=d.isoformat(), preferred_time=t)
        return row

    def remove_line(self, user_id, line_id):
        row = self.get_line(user_id, line_id)
        if row:
            self.rows.remove(row)
        return bool(row)

    def clear(self, user_id):
        self.rows = [r for r in self.rows if r["user_id"] != user_id]


@pytest.fixture
def client():
    rate_limit.reset_all()
    repo = FakeCartRepo()
    app.dependency_overrides[get_catalog_repo] = lambda: FakeCatalog()
    app.dependency_overrides[get_cart_repo] = lambda: repo
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="user-1", email="a@b.c", access_token="t")
    c = TestClient(app)
    c.repo = repo
    yield c
    app.dependency_overrides.clear()


def future(days=3):
    return (today_sgt() + timedelta(days=days)).isoformat()


def test_public_catalog_hides_pdf_urls(client):
    body = client.get("/catalog").json()
    assert len(body["items"]) == len(CATALOG)
    assert all("pdf_url" not in i for i in body["items"])
    assert "pdf_url" not in client.get("/catalog/book-1").json()
    assert client.get("/catalog/nope").status_code == 404


def test_guest_recommendations_endpoint(client):
    r = client.post("/cart/recommendations", json={"item_ids": ["book-1"]})
    assert r.status_code == 200
    assert all("pdf_url" not in p["item"] for p in r.json()["recommendations"])
    assert client.post("/cart/recommendations", json={"item_ids": ["x"] * 51}).status_code == 422


def test_cart_requires_login():
    app.dependency_overrides.clear()
    assert TestClient(app).get("/cart").status_code == 401


def test_add_book_and_service_totals(client):
    assert client.post("/cart/items", json={"item_id": "book-1"}).status_code == 201
    r = client.post("/cart/items", json={"item_id": "svc-numerology", "preferred_date": future(), "preferred_time": SLOTS[1]})
    cart = r.json()
    assert cart["total_minor"] == 2775 + 9600 and len(cart["items"]) == 2
    assert all("pdf_url" not in i for i in cart["items"])


def test_adding_same_line_twice_is_idempotent(client):
    for _ in range(2):
        client.post("/cart/items", json={"item_id": "book-1"})
    assert len(client.get("/cart").json()["items"]) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"item_id": "svc-numerology"},  # missing schedule
        {"item_id": "svc-numerology", "preferred_date": (date.today() - timedelta(days=2)).isoformat(), "preferred_time": SLOTS[0]},
        {"item_id": "svc-numerology", "preferred_date": future(90), "preferred_time": SLOTS[0]},
        {"item_id": "svc-numerology", "preferred_date": future(), "preferred_time": "3:33 AM"},
        {"item_id": "book-1", "preferred_date": future(), "preferred_time": SLOTS[0]},  # books take no schedule
        {"item_id": "retreat"},  # enquiry-only
        {"item_id": "does-not-exist"},
    ],
)
def test_invalid_lines_rejected(client, payload):
    assert client.post("/cart/items", json=payload).status_code == 400
    assert client.repo.rows == []


def test_update_and_remove_and_ownership(client):
    client.post("/cart/items", json={"item_id": "svc-numerology", "preferred_date": future(), "preferred_time": SLOTS[0]})
    line_id = client.get("/cart").json()["items"][0]["id"]
    r = client.patch(f"/cart/items/{line_id}", json={"preferred_date": future(5), "preferred_time": SLOTS[2]})
    assert r.status_code == 200 and r.json()["items"][0]["preferred_time"] == SLOTS[2]
    assert client.patch(f"/cart/items/{line_id}", json={"preferred_date": future(5), "preferred_time": "bad"}).status_code == 400
    # another user cannot touch this line
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="user-2", email="x@y.z", access_token="t")
    assert client.delete(f"/cart/items/{line_id}").status_code == 404
    assert client.patch(f"/cart/items/{line_id}", json={"preferred_date": future(), "preferred_time": SLOTS[0]}).status_code == 404
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="user-1", email="a@b.c", access_token="t")
    assert client.delete(f"/cart/items/{line_id}").json()["items"] == []


def test_guest_merge_is_idempotent_and_skips_bad_lines(client):
    guest = {"items": [
        {"item_id": "book-1"},
        {"item_id": "svc-chakra", "preferred_date": future(), "preferred_time": SLOTS[3]},
        {"item_id": "svc-numerology"},          # invalid: no schedule
        {"item_id": "gone-item"},                # invalid: unknown
    ]}
    first = client.post("/cart/merge", json=guest).json()
    assert len(first["items"]) == 2 and len(first["skipped"]) == 2
    second = client.post("/cart/merge", json=guest).json()  # e.g. user logs in twice
    assert len(second["items"]) == 2  # no duplicates
    assert first["total_minor"] == 2775 + 9600


def test_merge_keeps_existing_server_cart(client):
    client.post("/cart/items", json={"item_id": "book-2"})
    r = client.post("/cart/merge", json={"items": [{"item_id": "book-1"}]}).json()
    assert {i["item_id"] for i in r["items"]} == {"book-1", "book-2"}


def test_clear_cart(client):
    client.post("/cart/items", json={"item_id": "book-1"})
    assert client.delete("/cart").json()["items"] == []
    assert client.get("/cart").json()["total_minor"] == 0
