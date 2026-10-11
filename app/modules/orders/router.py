"""Checkout, payment verification, Razorpay webhook, order history and slot availability.

Prices always come from the catalog; nothing the client sends about amounts is trusted.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging_setup import log_event
from app.core.rate_limit import rate_limit
from app.core.scheduling import BOOKING_WINDOW_DAYS, SLOTS, today_sgt
from app.modules.auth.backend import AuthBackend, get_backend
from app.modules.auth.dependencies import CurrentUser, get_current_user
from app.modules.cart.repo import CartRepo, get_cart_repo
from app.modules.cart.router import warm_catalog
from app.modules.cart.rules import check_line
from app.modules.catalog.repo import CatalogRepo
from app.modules.orders import razorpay as rzp
from app.modules.orders.razorpay import RazorpayClient, RazorpayError, get_razorpay
from app.modules.orders.repo import OrdersRepo, get_orders_repo
from app.modules.orders.service import fulfil_paid_order

_log = logging.getLogger(__name__)
router = APIRouter(tags=["orders"])

_WEBHOOK_EVENTS = {"payment.captured", "order.paid"}


class SessionCheckout(BaseModel):
    item_id: str = Field(min_length=1, max_length=64)
    preferred_date: date
    preferred_time: str = Field(max_length=16)


class VerifyRequest(BaseModel):
    razorpay_order_id: str = Field(min_length=1, max_length=64)
    razorpay_payment_id: str = Field(min_length=1, max_length=64)
    razorpay_signature: str = Field(min_length=1, max_length=256)


def _order_item(line: dict, item: dict) -> dict:
    return {
        "item_id": item["id"], "type": item["type"], "title": item["title"], "subtitle": item.get("subtitle", ""),
        "price_minor": item["price_minor"], "currency": item["currency"],
        "preferred_date": line["preferred_date"].isoformat() if isinstance(line["preferred_date"], date) else line["preferred_date"],
        "preferred_time": line["preferred_time"],
    }


async def _start_checkout(
    user: CurrentUser, lines: list[dict], source: str, catalog: CatalogRepo,
    repo: OrdersRepo, backend: AuthBackend, razorpay: RazorpayClient,
) -> dict:
    if not lines:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Your cart is empty")
    items: list[dict] = []
    for line in lines:
        item = catalog.get(line["item_id"])
        pdate = date.fromisoformat(str(line["preferred_date"])) if line.get("preferred_date") else None
        try:
            check_line(item, pdate, line.get("preferred_time"))
        except ValueError as exc:
            title = item["title"] if item else line["item_id"]
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{title}: {exc}")
        items.append(_order_item({**line, "preferred_date": pdate}, item))
    if len({i["currency"] for i in items}) != 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Items in different currencies cannot be paid together")

    for day in {i["preferred_date"] for i in items if i["preferred_date"]}:
        unavailable = await run_in_threadpool(repo.unavailable_slots, day)
        if any(i["preferred_time"] in unavailable for i in items if i["preferred_date"] == day):
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"That time on {day} is no longer available. Please pick another.")

    profile = await run_in_threadpool(backend.profile_by_id, user.id) or {}
    email = user.email or profile.get("email")
    if not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Your account has no email address")
    total, currency = sum(i["price_minor"] for i in items), items[0]["currency"]

    order = await run_in_threadpool(
        repo.create,
        {
            "user_id": user.id, "email": email, "customer_name": profile.get("full_name", ""), "phone": profile.get("phone", ""),
            "source": source, "total_minor": total, "currency": currency,
        },
        items,
    )
    try:
        remote = await razorpay.create_order(total, currency, order["id"], {"order_id": order["id"], "user_id": user.id})
    except RazorpayError as exc:
        await run_in_threadpool(repo.mark_failed, order["id"])
        log_event(_log, "razorpay order creation failed", {"order_id": order["id"], "error": str(exc)}, level=logging.ERROR)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Payments are unavailable right now. Please try again shortly.")
    await run_in_threadpool(repo.attach_razorpay_order, order["id"], remote["id"])
    log_event(_log, "checkout started", {"order_id": order["id"], "razorpay_order_id": remote["id"], "total_minor": total, "lines": len(items)})
    return {
        "order_id": order["id"], "razorpay_order_id": remote["id"], "key_id": settings.razorpay_key_id,
        "amount": total, "currency": currency,
        "prefill": {"name": profile.get("full_name", ""), "email": email, "contact": profile.get("phone", "")},
    }


@router.post("/checkout", dependencies=[Depends(rate_limit("checkout", 20, 600))])
async def checkout(
    user: CurrentUser = Depends(get_current_user), cart: CartRepo = Depends(get_cart_repo),
    catalog: CatalogRepo = Depends(warm_catalog), repo: OrdersRepo = Depends(get_orders_repo),
    backend: AuthBackend = Depends(get_backend), razorpay: RazorpayClient = Depends(get_razorpay),
):
    """Pay for everything currently in the server-side cart."""
    lines = await run_in_threadpool(cart.lines, user.id)
    return await _start_checkout(user, lines, "cart", catalog, repo, backend, razorpay)


@router.post("/checkout/session", dependencies=[Depends(rate_limit("checkout", 20, 600))])
async def checkout_session(
    body: SessionCheckout, user: CurrentUser = Depends(get_current_user),
    catalog: CatalogRepo = Depends(warm_catalog), repo: OrdersRepo = Depends(get_orders_repo),
    backend: AuthBackend = Depends(get_backend), razorpay: RazorpayClient = Depends(get_razorpay),
):
    """Book a single session directly (the floating Book Your Session button), without touching the cart."""
    item = catalog.get(body.item_id)
    if item is None or not item["requires_schedule"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Please choose a session to book")
    line = {"item_id": body.item_id, "preferred_date": body.preferred_date, "preferred_time": body.preferred_time}
    return await _start_checkout(user, [line], "direct", catalog, repo, backend, razorpay)


def _order_view(order: dict, items: list[dict], catalog: CatalogRepo | None = None) -> dict:
    out_items = []
    for i in items:
        view = {k: i.get(k) for k in ("item_id", "type", "title", "subtitle", "price_minor", "currency", "preferred_date", "preferred_time")}
        if catalog and i["type"] == "book" and order["status"] == "paid":
            view["pdf_url"] = (catalog.get(i["item_id"]) or {}).get("pdf_url") or None  # owner-only download link
        out_items.append(view)
    return {
        "id": order["id"], "status": order["status"], "total_minor": order["total_minor"], "currency": order["currency"],
        "paid_at": order.get("paid_at"), "created_at": order.get("created_at"), "items": out_items,
    }


@router.post("/checkout/verify", dependencies=[Depends(rate_limit("checkout-verify", 30, 600))])
async def verify_payment(
    body: VerifyRequest, user: CurrentUser = Depends(get_current_user),
    catalog: CatalogRepo = Depends(warm_catalog), repo: OrdersRepo = Depends(get_orders_repo),
):
    order = await run_in_threadpool(repo.by_razorpay_id, body.razorpay_order_id)
    if order is None or order.get("user_id") != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Order not found")
    if not rzp.verify_payment_signature(body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature):
        log_event(_log, "payment signature rejected", {"order_id": order["id"]}, level=logging.WARNING)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Payment verification failed")
    paid = await fulfil_paid_order(repo, catalog, body.razorpay_order_id, body.razorpay_payment_id)
    return _order_view(paid, paid["items"], catalog)


@router.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request, x_razorpay_signature: Optional[str] = Header(default=None),
                           catalog: CatalogRepo = Depends(warm_catalog), repo: OrdersRepo = Depends(get_orders_repo)):
    """Source of truth for payments: still settles the order if the customer closed the tab before /checkout/verify."""
    raw = await request.body()
    if not rzp.verify_webhook_signature(raw, x_razorpay_signature or ""):
        log_event(_log, "webhook signature rejected", {"bytes": len(raw)}, level=logging.WARNING)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid signature")
    try:
        event = json.loads(raw)
        entity = event["payload"]["payment"]["entity"]
    except (ValueError, KeyError, TypeError):
        return {"status": "ignored"}  # e.g. refund or other events we do not handle
    if event.get("event") not in _WEBHOOK_EVENTS:
        return {"status": "ignored"}

    order = await run_in_threadpool(repo.by_razorpay_id, entity.get("order_id", ""))
    if order is None:
        log_event(_log, "webhook for unknown order", {"razorpay_order_id": entity.get("order_id")}, level=logging.WARNING)
        return {"status": "unknown_order"}  # 200: retrying cannot fix it
    if entity.get("amount") != order["total_minor"] or entity.get("currency") != order["currency"]:
        log_event(_log, "webhook amount mismatch", {"order_id": order["id"], "paid": entity.get("amount"), "expected": order["total_minor"]}, level=logging.ERROR)
        return {"status": "amount_mismatch"}
    await fulfil_paid_order(repo, catalog, entity["order_id"], entity.get("id", ""))  # errors -> 500 so Razorpay retries
    return {"status": "ok"}


@router.get("/orders")
async def my_orders(
    user: CurrentUser = Depends(get_current_user), repo: OrdersRepo = Depends(get_orders_repo),
    catalog: CatalogRepo = Depends(warm_catalog),
):
    orders = await run_in_threadpool(repo.for_user, user.id)
    return {"orders": [_order_view(o, o.get("order_items", []), catalog) for o in orders]}


@router.get("/availability", dependencies=[Depends(rate_limit("availability", 120, 60))])
async def availability(day: date = Query(alias="date"), repo: OrdersRepo = Depends(get_orders_repo)):
    today = today_sgt()
    if day < today or day > today + timedelta(days=BOOKING_WINDOW_DAYS):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Choose a date within the next {BOOKING_WINDOW_DAYS} days")
    unavailable = set(await run_in_threadpool(repo.unavailable_slots, day.isoformat()))
    return {"date": day.isoformat(), "slots": [{"time": s, "available": s not in unavailable} for s in SLOTS]}
