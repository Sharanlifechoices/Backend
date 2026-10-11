from app.core.scheduling import SLOTS
from app.tools import firestore_migration as fm


def test_decodes_firestore_rest_values():
    doc = {"name": "projects/p/databases/(default)/documents/bookOrders/abc123", "fields": {
        "amount": {"integerValue": "2775"}, "name": {"stringValue": "Asha"}, "ok": {"booleanValue": True},
        "slots": {"mapValue": {"fields": {"2:00 PM": {"booleanValue": False}}}}, "tags": {"arrayValue": {"values": [{"stringValue": "a"}]}},
        "none": {"nullValue": None}, "price": {"doubleValue": 1.5},
    }}
    assert fm.decode_document(doc) == {"id": "abc123", "amount": 2775, "name": "Asha", "ok": True, "slots": {"2:00 PM": False}, "tags": ["a"], "none": None, "price": 1.5}


def test_order_mapping_marks_email_as_already_sent_and_keeps_dob():
    doc = {"id": "o1", "bookId": 4, "bookTitle": "The Numbers Say So — Idealistic Strategist", "amount": 2775, "currency": "SGD", "dob": "1990-05-17",
           "email": " Asha@Example.com ", "name": "Asha", "phone": "+65 1", "paymentId": "pay_9", "createdAt": "2026-03-01T10:00:00.000Z", "emailSent": True}
    order, item = fm.map_order(doc, user_id=None)
    assert order["email"] == "asha@example.com" and order["status"] == "paid" and order["source"] == "legacy"
    assert order["confirmation_emailed_at"] == "2026-03-01T10:00:00+00:00"  # no second confirmation will be sent
    assert order["customer_dob"] == "1990-05-17" and order["razorpay_payment_id"] == "pay_9" and order["paid_at"] == order["created_at"]
    assert item == {"item_id": "book-4", "type": "book", "title": "The Numbers Say So", "subtitle": "Idealistic Strategist", "price_minor": 2775, "currency": "SGD"}


def test_orders_whose_old_email_never_went_out_are_left_unmarked():
    doc = {"id": "o2", "bookId": 3, "bookTitle": "T — S", "amount": 2775, "email": "a@b.c", "createdAt": "2026-03-01T10:00:00Z"}
    assert fm.map_order(doc, None)[0]["confirmation_emailed_at"] is None


def test_incomplete_orders_are_skipped_not_guessed():
    assert fm.map_order({"id": "x", "email": "a@b.c"}, None) is None
    assert fm.map_booking({"id": "x", "date": "2026-01-01"}, None) is None
    assert fm.map_gallery({"id": "x", "title": "no url"}) is None


def test_booking_mapping_translates_services_and_old_paid_status():
    base = {"id": "b1", "date": "2026-04-02", "slot": "2:00 PM", "email": "A@B.c", "name": "Asha", "phone": "1", "createdAt": "2026-03-30T01:00:00Z"}
    assert fm.map_booking({**base, "service": "Chakra Reading", "status": "paid"}, "u1") == {
        "legacy_id": "b1", "user_id": "u1", "email": "a@b.c", "name": "Asha", "phone": "1", "service_id": "svc-chakra-reading",
        "service_title": "Chakra Reading", "booking_date": "2026-04-02", "slot": "2:00 PM", "status": "pending", "created_at": "2026-03-30T01:00:00+00:00"}
    assert fm.map_booking({**base, "service": "Meditation & Breathwork", "status": "confirmed"}, None)["service_id"] == "svc-meditation-breathwork"
    assert fm.map_booking({**base, "service": "Something New", "status": "cancelled"}, None)["service_id"] == "legacy-other"


def test_availability_keeps_only_blocked_known_slots():
    assert fm.map_availability({"id": "2026-05-01", "slots": {SLOTS[0]: True, SLOTS[1]: False, "3:33 AM": False}}) == {"day": "2026-05-01", "blocked_slots": [SLOTS[1]]}
    assert fm.map_availability({"id": "2026-05-02", "slots": {s: True for s in SLOTS}}) is None


def test_gallery_mapping_keeps_the_retreat_flag():
    assert fm.map_gallery({"id": "g1", "url": "https://x/y.jpg", "title": "T", "type": "retreat", "createdAt": "2026-01-01T00:00:00Z"})["type"] == "retreat"
    assert fm.map_gallery({"id": "g2", "url": "https://x/z.jpg", "title": "U"})["type"] == "gallery"


def test_plan_links_orders_to_existing_accounts_by_email():
    data = {"bookOrders": [{"id": "o1", "bookId": 1, "bookTitle": "T — S", "amount": 100, "email": "Known@x.com"},
                           {"id": "o2", "bookId": 1, "bookTitle": "T — S", "amount": 100, "email": "stranger@x.com"}],
            "bookings": [], "availability": [], "gallery": []}
    plan = fm.build_plan(data, {"known@x.com": "user-1"})
    assert [o["user_id"] for o, _ in plan["orders"]] == ["user-1", None]


def test_migration_only_issues_get_requests_to_firestore():
    import httpx

    seen = []

    def handler(request):
        seen.append(request.method)
        return httpx.Response(200, json={"documents": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        for name in fm.COLLECTIONS:
            fm.fetch_collection(client, "proj", "key", name)
    assert seen and set(seen) == {"GET"}  # read-only: Firestore is never written
