"""One-off import of the old Firestore data into Supabase.

    python -m app.tools.firestore_migration            # dry run: reads Firestore, writes a JSON backup, prints the plan
    python -m app.tools.firestore_migration --apply    # also writes to Supabase

Safety:
- Firestore is only ever READ (HTTP GET against the public REST API, the same access the old site used).
  Nothing in Firestore is written, changed or deleted.
- A JSON backup of every collection is saved under backups/ before anything else happens (git-ignored: personal data).
- Re-running is safe: rows are keyed by the Firestore document id (legacy_id), existing rows are left alone,
  so edits made in the new admin panel are never overwritten.

Needs FIREBASE_API_KEY (the public web key from the old firebase.js) in the environment;
FIREBASE_PROJECT_ID defaults to sharan-24586.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from app.core.scheduling import SLOTS

COLLECTIONS = ("bookOrders", "bookings", "availability", "gallery")
SERVICE_IDS = {
    "numerology": "svc-numerology",
    "meditation & breathwork": "svc-meditation-breathwork",
    "meditation & yogic breathwork": "svc-meditation-breathwork",
    "chakra reading": "svc-chakra-reading",
}


# ---- Firestore REST (read-only) -----------------------------------------------------------
def _value(v: dict) -> Any:
    (kind, raw), = v.items()
    if kind == "nullValue":
        return None
    if kind == "integerValue":
        return int(raw)
    if kind == "doubleValue":
        return float(raw)
    if kind == "mapValue":
        return {k: _value(x) for k, x in raw.get("fields", {}).items()}
    if kind == "arrayValue":
        return [_value(x) for x in raw.get("values", [])]
    return raw  # string, boolean, timestamp (ISO string), reference, bytes


def decode_document(doc: dict) -> dict:
    return {"id": doc["name"].rsplit("/", 1)[-1], **{k: _value(v) for k, v in doc.get("fields", {}).items()}}


def fetch_collection(client: httpx.Client, project: str, key: str, name: str) -> list[dict]:
    base = f"https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents/{name}"
    docs, token = [], None
    while True:
        params = {"key": key, "pageSize": 300, **({"pageToken": token} if token else {})}
        resp = client.get(base, params=params, timeout=30)  # GET only
        resp.raise_for_status()
        body = resp.json()
        docs += [decode_document(d) for d in body.get("documents", [])]
        token = body.get("nextPageToken")
        if not token:
            return docs


# ---- mapping (pure functions, unit tested) ---------------------------------------------------
def iso(value: Any) -> Optional[str]:
    """Firestore timestamps and the ISO strings the old site stored both end up as UTC ISO strings."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def map_gallery(doc: dict) -> Optional[dict]:
    if not doc.get("url"):
        return None
    row = {"legacy_id": doc["id"], "url": doc["url"], "title": doc.get("title") or "",
           "type": "retreat" if doc.get("type") == "retreat" else "gallery"}
    if iso(doc.get("createdAt")):
        row["created_at"] = iso(doc["createdAt"])
    return row


def map_availability(doc: dict) -> Optional[dict]:
    """Old shape: id = YYYY-MM-DD, slots = {"2:00 PM": false, ...}; false means blocked."""
    blocked = [s for s in SLOTS if (doc.get("slots") or {}).get(s) is False]
    return {"day": doc["id"], "blocked_slots": blocked} if blocked else None


def map_booking(doc: dict, user_id: Optional[str]) -> Optional[dict]:
    if not (doc.get("date") and doc.get("slot") and doc.get("email")):
        return None
    service = (doc.get("service") or "").strip()
    status = doc.get("status") or "pending"
    row = {
        "legacy_id": doc["id"], "user_id": user_id, "email": doc["email"].strip().lower(), "name": doc.get("name") or "",
        "phone": doc.get("phone") or "", "service_id": SERVICE_IDS.get(service.lower(), "legacy-other"),
        "service_title": service or "Session", "booking_date": doc["date"], "slot": doc["slot"],
        # the old flow stored "paid" until Sharan confirmed it: that is the new "pending"
        "status": status if status in ("pending", "confirmed", "cancelled") else "pending",
    }
    if iso(doc.get("createdAt")):
        row["created_at"] = iso(doc["createdAt"])
    return row


def map_order(doc: dict, user_id: Optional[str]) -> Optional[tuple[dict, dict]]:
    """Returns (order row, order item row). One book per old order."""
    if not (doc.get("email") and doc.get("amount") is not None and doc.get("bookId") is not None):
        return None
    title, _, subtitle = (doc.get("bookTitle") or "").partition(" — ")
    paid = iso(doc.get("createdAt"))
    currency = doc.get("currency") or "SGD"
    order = {
        "legacy_id": doc["id"], "user_id": user_id, "email": doc["email"].strip().lower(), "customer_name": doc.get("name") or "",
        "phone": doc.get("phone") or "", "status": "paid", "source": "legacy", "total_minor": int(doc["amount"]), "currency": currency,
        "razorpay_payment_id": doc.get("paymentId"), "customer_dob": doc.get("dob") or None,
        # buyers the old site already emailed are marked so no second confirmation goes out; the rest stay unmarked
        # (the admin can use "Resend confirmation email" for them)
        "confirmation_emailed_at": iso(doc.get("emailSentAt")) or (paid if doc.get("emailSent") else None),
    }
    if paid:
        order["paid_at"] = order["created_at"] = paid
    item = {"item_id": f"book-{doc['bookId']}", "type": "book", "title": title or "Book", "subtitle": subtitle,
            "price_minor": int(doc["amount"]), "currency": currency}
    return order, item


# ---- plan + apply ----------------------------------------------------------------------------
def build_plan(data: dict[str, list[dict]], user_ids: dict[str, str]) -> dict:
    uid = lambda d: user_ids.get((d.get("email") or "").strip().lower())  # noqa: E731
    return {
        "orders": [m for d in data["bookOrders"] if (m := map_order(d, uid(d)))],
        "bookings": [m for d in data["bookings"] if (m := map_booking(d, uid(d)))],
        "availability": [m for d in data["availability"] if (m := map_availability(d))],
        "gallery": [m for d in data["gallery"] if (m := map_gallery(d))],
    }


def apply_plan(db, plan: dict) -> dict:
    """Idempotent writes. Returns how many rows were newly created per table."""
    created = {}
    existing = {r["legacy_id"] for r in db.table("orders").select("legacy_id").not_.is_("legacy_id", "null").execute().data}
    n = 0
    for order, item in plan["orders"]:
        if order["legacy_id"] in existing:
            continue
        row = db.table("orders").insert(order).execute().data[0]
        db.table("order_items").insert({**item, "order_id": row["id"]}).execute()
        n += 1
    created["orders"] = n

    for table, key, rows in (("bookings", "legacy_id", plan["bookings"]), ("gallery", "legacy_id", plan["gallery"]), ("availability", "day", plan["availability"])):
        before = db.table(table).select("id" if table != "availability" else "day", count="exact").limit(1).execute().count or 0
        for i in range(0, len(rows), 100):
            db.table(table).upsert(rows[i:i + 100], on_conflict=key, ignore_duplicates=True).execute()
        after = db.table(table).select("id" if table != "availability" else "day", count="exact").limit(1).execute().count or 0
        created[table] = after - before
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write to Supabase (default is a dry run)")
    args = parser.parse_args()

    key, project = os.environ.get("FIREBASE_API_KEY"), os.environ.get("FIREBASE_PROJECT_ID", "sharan-24586")
    if not key:
        print("Set FIREBASE_API_KEY (the public web key from the old firebase.js).", file=sys.stderr)
        return 2

    data: dict[str, list[dict]] = {}
    with httpx.Client() as client:
        for name in COLLECTIONS:
            data[name] = fetch_collection(client, project, key, name)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = Path("backups") / f"firestore-{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    for name, docs in data.items():
        (backup / f"{name}.json").write_text(json.dumps(docs, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"Backup written to {backup}/ (keep it safe: contains customer data)")
    print("Read from Firestore:", {k: len(v) for k, v in data.items()})

    from app.core.supabase_client import get_service_client  # imported late so the dry run needs no Supabase

    db = get_service_client()
    profiles = db.table("profiles").select("user_id, email").execute().data
    user_ids = {p["email"].lower(): p["user_id"] for p in profiles}
    plan = build_plan(data, user_ids)
    print("Would import:", {k: len(v) for k, v in plan.items()},
          "| orders linked to an existing account:", sum(1 for o, _ in plan["orders"] if o["user_id"]),
          "| skipped as incomplete:", {"bookOrders": len(data["bookOrders"]) - len(plan["orders"]), "bookings": len(data["bookings"]) - len(plan["bookings"]),
                                       "gallery": len(data["gallery"]) - len(plan["gallery"])})
    if not args.apply:
        print("Dry run only. Re-run with --apply to write to Supabase.")
        return 0
    print("Created:", apply_plan(db, plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
