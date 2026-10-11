"""Admin API. Every route needs the X-Admin-Secret header (see app/core/admin_auth.py)."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.admin_auth import require_admin
from app.core.email import EmailError
from app.core.logging_setup import log_event
from app.core.passwords import validate_password
from app.core.rate_limit import rate_limit
from app.core.scheduling import SLOTS
from app.modules.admin import newsletter as nl
from app.modules.admin.repo import AdminRepo, get_admin_repo
from app.modules.auth.backend import AuthBackend, get_backend
from app.modules.cart.router import warm_catalog
from app.modules.catalog.repo import CatalogRepo, get_catalog_repo
from app.modules.jobs.repo import EmailLogRepo, get_email_log
from app.modules.orders.repo import OrdersRepo, get_orders_repo
from app.modules.orders.service import send_purchase_confirmation
from app.modules.unsubscribe.repo import SuppressionRepo, get_suppression_repo

_log = logging.getLogger("admin")
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin), Depends(rate_limit("admin", 300, 60))])

Page = Query(50, ge=1, le=200)


def _not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{what} not found")


# ---- purchases ------------------------------------------------------------------------
@router.get("/orders")
async def list_orders(
    status_filter: Optional[Literal["created", "paid", "failed"]] = Query("paid", alias="status"), q: str = "",
    since: Optional[date] = None, until: Optional[date] = None, limit: int = Page, offset: int = Query(0, ge=0),
    repo: AdminRepo = Depends(get_admin_repo),
):
    """All purchases (paid by default; pass status=created|failed to investigate unfinished payments)."""
    until_iso = (until + timedelta(days=1)).isoformat() if until else None
    rows, total = await run_in_threadpool(repo.list_orders, status_filter, q, since.isoformat() if since else None, until_iso, limit, offset)
    return {"orders": rows, "total": total}


@router.get("/orders/{order_id}")
async def get_order(order_id: str, repo: AdminRepo = Depends(get_admin_repo)):
    order = await run_in_threadpool(repo.get_order, order_id)
    if not order:
        raise _not_found("Order")
    return order


@router.post("/orders/{order_id}/resend-confirmation")
async def resend_confirmation(
    order_id: str, repo: AdminRepo = Depends(get_admin_repo), orders: OrdersRepo = Depends(get_orders_repo),
    catalog: CatalogRepo = Depends(warm_catalog),
):
    order = await run_in_threadpool(repo.get_order, order_id)
    if not order or order["status"] != "paid":
        raise _not_found("Paid order")
    await run_in_threadpool(orders.release_confirmation_email, order_id)  # clear the once-only claim, then resend
    sent = await send_purchase_confirmation(orders, catalog, order, order["order_items"])
    if not sent:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Could not send the email. See logs.")
    log_event(_log, "confirmation resent", {"order_id": order_id})
    return {"sent": True}


# ---- users ----------------------------------------------------------------------------
class PasswordChange(BaseModel):
    password: str = Field(max_length=128)
    _v = field_validator("password")(validate_password)


@router.get("/users")
async def list_users(q: str = "", limit: int = Page, offset: int = Query(0, ge=0), repo: AdminRepo = Depends(get_admin_repo)):
    rows, total = await run_in_threadpool(repo.list_users, q, limit, offset)
    return {"users": rows, "total": total}


@router.get("/users/{user_id}")
async def get_user(user_id: str, repo: AdminRepo = Depends(get_admin_repo)):
    profile = await run_in_threadpool(repo.get_profile, user_id)
    if not profile:
        raise _not_found("User")
    profile.pop("unsubscribe_token", None)
    return {"user": profile, "orders": await run_in_threadpool(repo.user_orders, user_id), "bookings": await run_in_threadpool(repo.user_bookings, user_id)}


@router.put("/users/{user_id}/password")
async def set_user_password(
    user_id: str, body: PasswordChange, repo: AdminRepo = Depends(get_admin_repo), backend: AuthBackend = Depends(get_backend),
):
    """Replace a user's password (same policy as signup). Tell the user out-of-band; existing sessions expire on their own."""
    if not await run_in_threadpool(repo.get_profile, user_id):
        raise _not_found("User")
    await run_in_threadpool(backend.set_password, user_id, body.password)
    log_event(_log, "admin set user password", {"user_id": user_id})
    return {"updated": True}


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: str, repo: AdminRepo = Depends(get_admin_repo)):
    if not await run_in_threadpool(repo.get_profile, user_id):
        raise _not_found("User")
    await run_in_threadpool(repo.delete_user, user_id)
    log_event(_log, "admin deleted user", {"user_id": user_id})


# ---- bookings & availability ----------------------------------------------------------
class BookingUpdate(BaseModel):
    status: Literal["pending", "confirmed", "cancelled"]


class AvailabilityUpdate(BaseModel):
    blocked_slots: list[str] = Field(default_factory=list, max_length=len(SLOTS))

    @field_validator("blocked_slots")
    @classmethod
    def _known_slots(cls, v: list[str]) -> list[str]:
        bad = [s for s in v if s not in SLOTS]
        if bad:
            raise ValueError(f"Unknown time slot: {bad[0]}")
        return sorted(set(v), key=SLOTS.index)


@router.get("/bookings")
async def list_bookings(
    status_filter: Optional[Literal["pending", "confirmed", "cancelled"]] = Query(None, alias="status"),
    since: Optional[date] = None, until: Optional[date] = None, limit: int = Page, offset: int = Query(0, ge=0),
    repo: AdminRepo = Depends(get_admin_repo),
):
    rows, total = await run_in_threadpool(repo.list_bookings, status_filter, since.isoformat() if since else None, until.isoformat() if until else None, limit, offset)
    return {"bookings": rows, "total": total}


@router.patch("/bookings/{booking_id}")
async def update_booking(booking_id: str, body: BookingUpdate, repo: AdminRepo = Depends(get_admin_repo)):
    row = await run_in_threadpool(repo.update_booking, booking_id, body.status)
    if not row:
        raise _not_found("Booking")
    log_event(_log, "booking status changed", {"booking_id": booking_id, "status": body.status})
    return row


@router.get("/availability")
async def list_availability(since: date, until: date, repo: AdminRepo = Depends(get_admin_repo)):
    if until < since or (until - since).days > 120:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Choose a range of at most 120 days")
    return {"days": await run_in_threadpool(repo.list_availability, since.isoformat(), until.isoformat())}


@router.put("/availability/{day}")
async def set_availability(day: date, body: AvailabilityUpdate, repo: AdminRepo = Depends(get_admin_repo)):
    """Block these slots for the day (empty list reopens the whole day)."""
    await run_in_threadpool(repo.set_availability, day.isoformat(), body.blocked_slots)
    return {"day": day.isoformat(), "blocked_slots": body.blocked_slots}


# ---- gallery & retreats (images are uploaded to Cloudinary by the browser) -------------
class GalleryCreate(BaseModel):
    url: str = Field(min_length=8, max_length=1000, pattern=r"^https://")
    title: str = Field(min_length=1, max_length=200)
    type: Literal["gallery", "retreat"] = "gallery"


@router.get("/gallery")
async def admin_gallery(type: Optional[Literal["gallery", "retreat"]] = None, repo: AdminRepo = Depends(get_admin_repo)):
    return {"items": await run_in_threadpool(repo.list_gallery, type)}


@router.post("/gallery", status_code=201)
async def add_gallery(body: GalleryCreate, repo: AdminRepo = Depends(get_admin_repo)):
    return await run_in_threadpool(repo.add_gallery, body.url, body.title.strip(), body.type)


@router.delete("/gallery/{item_id}", status_code=204)
async def delete_gallery(item_id: str, repo: AdminRepo = Depends(get_admin_repo)):
    if not await run_in_threadpool(repo.delete_gallery, item_id):
        raise _not_found("Image")


# ---- newsletter -----------------------------------------------------------------------
class NewsletterDraft(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body_html: str = Field(min_length=1, max_length=200_000)


class SendRequest(BaseModel):
    confirm_audience: int = Field(ge=0, description="Must equal the current audience size (copy it from the dry run)")


class TestRequest(BaseModel):
    to: EmailStr


@router.get("/newsletter/audience")
async def audience_preview(repo: AdminRepo = Depends(get_admin_repo), supp: SuppressionRepo = Depends(get_suppression_repo)):
    """Dry run: who would receive a newsletter right now (accounts active in the last 12 months)."""
    people = await nl.build_audience(repo, supp)
    return {"count": len(people), "sample": [p["email"] for p in people[:20]], "window_days": nl.AUDIENCE_WINDOW.days}


@router.get("/newsletters")
async def list_newsletters(repo: AdminRepo = Depends(get_admin_repo)):
    return {"newsletters": await run_in_threadpool(repo.list_newsletters)}


@router.post("/newsletters", status_code=201)
async def create_newsletter(body: NewsletterDraft, repo: AdminRepo = Depends(get_admin_repo)):
    return await run_in_threadpool(repo.create_newsletter, body.subject.strip(), body.body_html)


@router.get("/newsletters/{newsletter_id}")
async def get_newsletter(newsletter_id: str, repo: AdminRepo = Depends(get_admin_repo)):
    row = await run_in_threadpool(repo.get_newsletter, newsletter_id)
    if not row:
        raise _not_found("Newsletter")
    return row


@router.put("/newsletters/{newsletter_id}")
async def edit_newsletter(newsletter_id: str, body: NewsletterDraft, repo: AdminRepo = Depends(get_admin_repo)):
    row = await run_in_threadpool(repo.update_draft, newsletter_id, body.subject.strip(), body.body_html)
    if not row:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Only drafts can be edited")
    return row


@router.delete("/newsletters/{newsletter_id}", status_code=204)
async def delete_newsletter(newsletter_id: str, repo: AdminRepo = Depends(get_admin_repo)):
    if not await run_in_threadpool(repo.delete_draft, newsletter_id):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Only drafts can be deleted")


@router.post("/newsletters/{newsletter_id}/test")
async def test_newsletter(newsletter_id: str, body: TestRequest, repo: AdminRepo = Depends(get_admin_repo)):
    """Send the newsletter to one address (not recorded, no audience involved)."""
    row = await run_in_threadpool(repo.get_newsletter, newsletter_id)
    if not row:
        raise _not_found("Newsletter")
    try:
        await nl.send_test(body.to, row["subject"], row["body_html"])
    except EmailError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Could not send the test email. See logs.")
    return {"sent": True}


@router.post("/newsletters/{newsletter_id}/send", status_code=202)
async def send_newsletter(
    newsletter_id: str, body: SendRequest, background: BackgroundTasks, repo: AdminRepo = Depends(get_admin_repo),
    supp: SuppressionRepo = Depends(get_suppression_repo), log: EmailLogRepo = Depends(get_email_log),
):
    """Start sending in the background. Poll GET /admin/newsletters/{id} for progress.
    Safe to call again after a failure or restart: recipients already mailed are skipped."""
    row = await run_in_threadpool(repo.get_newsletter, newsletter_id)
    if not row:
        raise _not_found("Newsletter")
    audience = await nl.build_audience(repo, supp)
    if body.confirm_audience != len(audience):
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"message": "Audience size changed. Review the dry run and confirm again.", "audience": len(audience)})
    if row["status"] == "sent":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This newsletter has already been sent")
    if not await run_in_threadpool(repo.claim_send, newsletter_id, len(audience), datetime.now(timezone.utc) - nl.STALL_AFTER):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This newsletter is already being sent")
    background.add_task(nl.run_newsletter, repo, log, supp, newsletter_id)
    log_event(_log, "newsletter send started", {"newsletter_id": newsletter_id, "audience": len(audience)})
    return {"status": "sending", "audience": len(audience)}
