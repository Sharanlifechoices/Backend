"""Monthly newsletter: audience rules and the resumable batch sender.

Audience: verified, subscribed accounts that signed up or paid for something in the last 12 months.
Every recipient goes through the shared `send_once`, so a key `newsletter:<id>:<email>` in `email_log`
guarantees nobody is mailed twice even if the send is restarted, retried or resumed after a restart.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from app.core.email import send_email
from app.core.email_templates import newsletter_email
from app.core.logging_setup import log_event
from app.core.unsubscribe import unsubscribe_headers, unsubscribe_url
from app.modules.admin.repo import AdminRepo
from app.modules.jobs.repo import EmailLogRepo
from app.modules.jobs.service import new_stats, send_once
from app.modules.unsubscribe.repo import SuppressionRepo

_log = logging.getLogger(__name__)

AUDIENCE_WINDOW = timedelta(days=365)
STALL_AFTER = timedelta(minutes=15)  # a 'sending' newsletter untouched this long was interrupted (e.g. Render restart)


async def build_audience(repo: AdminRepo, suppressions: SuppressionRepo, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    people = await asyncio.to_thread(repo.audience, now - AUDIENCE_WINDOW)
    suppressed = await asyncio.to_thread(suppressions.suppressed_among, [p["email"] for p in people])
    unique: dict[str, dict] = {}
    for p in people:
        email = p["email"].lower()
        if email not in suppressed:
            unique.setdefault(email, {**p, "email": email})
    return sorted(unique.values(), key=lambda p: p["email"])


async def send_test(to: str, subject: str, body_html: str) -> None:
    subject, html = newsletter_email(f"[TEST] {subject}", body_html, unsubscribe_url(to))
    await send_email(to=to, subject=subject, html=html, kind="newsletter-test", headers=unsubscribe_headers(to))


async def run_newsletter(
    repo: AdminRepo, log: EmailLogRepo, suppressions: SuppressionRepo, newsletter_id: str, deadline: float | None = None,
) -> dict:
    """Send to everyone not yet mailed. Never raises: failures are recorded on the newsletter row."""
    try:
        newsletter = await asyncio.to_thread(repo.get_newsletter, newsletter_id)
        if newsletter is None:
            return {"error": "not_found"}
        audience = await build_audience(repo, suppressions)
        stats = new_stats()
        for person in audience:
            if deadline is not None and time.monotonic() > deadline:
                stats["stopped_early"] = True
                break
            email = person["email"]
            await send_once(
                log, f"newsletter:{newsletter_id}:{email}", "newsletter", email,
                lambda e=email: newsletter_email(newsletter["subject"], newsletter["body_html"], unsubscribe_url(e)),
                stats, headers=unsubscribe_headers(email),
            )
        prefix = f"newsletter:{newsletter_id}:"
        sent = await asyncio.to_thread(repo.count_log, prefix, "sent")
        failed = await asyncio.to_thread(repo.count_log, prefix, "failed")
        if stats.get("stopped_early"):
            await asyncio.to_thread(repo.finish_newsletter, newsletter_id, "sending", sent, failed)  # stays resumable
        else:
            await asyncio.to_thread(repo.finish_newsletter, newsletter_id, "partial" if failed else "sent", sent, failed)
        log_event(_log, "newsletter run finished", {"newsletter_id": newsletter_id, "audience": len(audience), **stats, "total_sent": sent, "total_failed": failed})
        return {**stats, "total_sent": sent, "total_failed": failed}
    except Exception as exc:
        log_event(_log, "newsletter run crashed", {"newsletter_id": newsletter_id, "error": type(exc).__name__}, level=logging.ERROR)
        return {"error": type(exc).__name__}


async def resume_stalled(repo: AdminRepo, log: EmailLogRepo, suppressions: SuppressionRepo, deadline: float) -> dict:
    """Called by the daily job: finish newsletters whose background send was cut off."""
    resumed = 0
    for row in await asyncio.to_thread(repo.stalled_newsletters, datetime.now(timezone.utc) - STALL_AFTER):
        audience = await build_audience(repo, suppressions)
        if await asyncio.to_thread(repo.claim_send, row["id"], len(audience), datetime.now(timezone.utc) - STALL_AFTER):
            await run_newsletter(repo, log, suppressions, row["id"], deadline)
            resumed += 1
    return {"resumed": resumed}
