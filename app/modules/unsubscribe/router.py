"""Unsubscribe endpoints.

GET shows a confirmation page and changes nothing (mail scanners and link prefetchers GET links, and
must not unsubscribe people). POST performs it and is also the RFC 8058 one-click target.
PUT /me/subscription lets a logged-in user switch emails back on from their profile.
"""

from __future__ import annotations

import logging
from html import escape

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.core.config import settings
from app.core.logging_setup import log_event
from app.core.rate_limit import rate_limit
from app.core.unsubscribe import parse_token
from app.modules.auth.dependencies import CurrentUser, get_current_user
from app.modules.unsubscribe.repo import SuppressionRepo, get_suppression_repo

_log = logging.getLogger(__name__)
router = APIRouter(tags=["unsubscribe"])


def _page(title: str, message: str, form_action: str = "", status_code: int = 200) -> HTMLResponse:
    form = (
        f'<form method="post" action="{escape(form_action, quote=True)}"><button type="submit">Yes, unsubscribe me</button></form>'
        if form_action else ""
    )
    html = (
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#050510;"
        "color:#f8f7ff;font-family:Arial,sans-serif}.c{max-width:420px;margin:16px;padding:32px;background:#0f0f2d;border-radius:16px;"
        "text-align:center}h1{color:#d4a853;font-size:22px}button,a.b{display:inline-block;margin-top:16px;padding:12px 24px;"
        "border:0;border-radius:999px;background:#d4a853;color:#1a1204;font-weight:bold;text-decoration:none;cursor:pointer;"
        f'font-size:15px}}</style></head><body><div class="c"><h1>{escape(title)}</h1><p>{escape(message)}</p>{form}'
        f'<a class="b" href="{escape(settings.frontend_url, quote=True)}">Back to My LifeChoices</a></div></body></html>'
    )
    return HTMLResponse(html, status_code=status_code)


_BAD_LINK = ("This link is not valid", "The unsubscribe link looks incomplete or was altered. Please use the link from your latest email.")


@router.get("/unsubscribe", response_class=HTMLResponse, dependencies=[Depends(rate_limit("unsubscribe", 60, 60))])
async def unsubscribe_page(token: str = Query(max_length=512)):
    if parse_token(token) is None:
        return _page(*_BAD_LINK, status_code=400)
    return _page("Unsubscribe?", "You will stop receiving follow-ups, birthday wishes and newsletters. Order and account emails will still reach you.",
                 form_action=f"/unsubscribe?token={escape(token, quote=True)}")


@router.post("/unsubscribe", response_class=HTMLResponse, dependencies=[Depends(rate_limit("unsubscribe", 60, 60))])
async def unsubscribe(token: str = Query(max_length=512), repo: SuppressionRepo = Depends(get_suppression_repo)):
    email = parse_token(token)
    if email is None:
        return _page(*_BAD_LINK, status_code=400)
    await run_in_threadpool(repo.suppress, email)
    log_event(_log, "unsubscribed", {"email": email})
    return _page("You are unsubscribed", "You will no longer receive our marketing emails. You can switch them back on any time from your profile.")


class SubscriptionUpdate(BaseModel):
    subscribed: bool


@router.put("/me/subscription")
async def set_subscription(
    body: SubscriptionUpdate, user: CurrentUser = Depends(get_current_user), repo: SuppressionRepo = Depends(get_suppression_repo),
):
    if not user.email:
        return {"subscribed": body.subscribed}
    await run_in_threadpool(repo.resubscribe if body.subscribed else repo.suppress, user.email)
    log_event(_log, "subscription changed", {"subscribed": body.subscribed})
    return {"subscribed": body.subscribed}
