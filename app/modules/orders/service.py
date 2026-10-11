"""Payment fulfilment: turns a verified payment into a paid order, bookings and a confirmation email.

Called from both /checkout/verify and the Razorpay webhook, possibly more than once for the same
payment. Every step is idempotent, and the side effects re-run on a replay so a crash half-way
through is healed by the next call.
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi.concurrency import run_in_threadpool

from app.core.email import send_email
from app.core.email_templates import purchase_email
from app.core.logging_setup import log_event
from app.modules.catalog.repo import CatalogRepo
from app.modules.orders.repo import OrdersRepo

_log = logging.getLogger(__name__)


def money(minor: int, currency: str) -> str:
    return f"{currency} {minor / 100:,.2f}"


def _detail(item: dict) -> str:
    if not item.get("preferred_date"):
        return ""
    day = date.fromisoformat(str(item["preferred_date"])).strftime("%d %b %Y")
    return f"{day}, {item['preferred_time']} SGT (to be confirmed)"


async def send_purchase_confirmation(repo: OrdersRepo, catalog: CatalogRepo, order: dict, items: list[dict]) -> bool:
    """Send the confirmation at most once per order. The claim is released on failure so a retry can resend."""
    claimed = False
    try:
        claimed = await run_in_threadpool(repo.claim_confirmation_email, order["id"])
        if not claimed:
            return False
        await run_in_threadpool(catalog.all_active)
        lines = []
        for i in items:
            current = catalog.get(i["item_id"]) or {}
            lines.append({"type": i["type"], "title": i["title"], "subtitle": i.get("subtitle") or "",
                          "pdf_url": current.get("pdf_url") if i["type"] == "book" else "", "detail": _detail(i)})
        subject, html = purchase_email(order.get("customer_name") or "there", lines, money(order["total_minor"], order["currency"]))
        await send_email(to=order["email"], subject=subject, html=html, kind="purchase")
        return True
    except Exception as exc:  # EmailError, catalog/db hiccups: never fail a paid order over an email
        log_event(_log, "purchase email failed", {"order_id": order["id"], "error": type(exc).__name__}, level=logging.ERROR)
        if claimed:
            try:
                await run_in_threadpool(repo.release_confirmation_email, order["id"])
            except Exception:
                log_event(_log, "could not release email claim", {"order_id": order["id"]}, level=logging.ERROR)
        return False


async def fulfil_paid_order(
    repo: OrdersRepo, catalog: CatalogRepo, razorpay_order_id: str, payment_id: str
) -> dict | None:
    """Returns the paid order, or None if we have no such order."""
    order = await run_in_threadpool(repo.by_razorpay_id, razorpay_order_id)
    if order is None:
        return None
    first = await run_in_threadpool(repo.mark_paid, razorpay_order_id, payment_id) is not None
    order = {**order, "status": "paid"}
    items = await run_in_threadpool(repo.items, order["id"])
    bookings = await run_in_threadpool(repo.ensure_bookings, order, items)
    if first and order["source"] == "cart" and order.get("user_id"):
        await run_in_threadpool(repo.remove_cart_lines, order["user_id"], items)
    emailed = await send_purchase_confirmation(repo, catalog, order, items)
    log_event(
        _log, "order fulfilled",
        {"order_id": order["id"], "first_time": first, "bookings": bookings, "emailed": emailed, "items": len(items)},
    )
    return {**order, "items": items}
