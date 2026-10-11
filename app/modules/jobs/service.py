"""Daily jobs: 7-day follow-up, birthday emails, stale-signup cleanup.

Every email goes through `send_once`, which claims a unique dedupe key in `email_log` first, so
the whole run is safe to repeat (cron retries, a manual re-run, overlapping catch-up windows).
Each job stops at the shared deadline instead of overrunning the cron caller's HTTP timeout; the
next run picks up whatever was left.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable

from app.core.config import settings
from app.core.email import send_email
from app.core.email_templates import birthday_email, followup_email
from app.core.logging_setup import log_event
from app.core.scheduling import today_sgt
from app.core.unsubscribe import unsubscribe_headers, unsubscribe_url
from app.modules.jobs.repo import EmailLogRepo, JobsRepo
from app.modules.unsubscribe.repo import SuppressionRepo

_log = logging.getLogger(__name__)

FOLLOWUP_AFTER = timedelta(days=7)
FOLLOWUP_CATCHUP = timedelta(days=2)  # a missed cron day is healed by the next runs
UNVERIFIED_MAX_AGE = timedelta(days=7)


def new_stats() -> dict:
    return {"sent": 0, "skipped": 0, "failed": 0}


async def send_once(
    log: EmailLogRepo, key: str, kind: str, to: str, build: Callable[[], tuple[str, str]], stats: dict,
    headers: dict[str, str] | None = None,
) -> None:
    try:
        if not await asyncio.to_thread(log.claim, key, kind, to):
            stats["skipped"] += 1
            return
        subject, html = build()
        try:
            result = await send_email(to=to, subject=subject, html=html, kind=kind, headers=headers)
        except Exception as exc:
            await asyncio.to_thread(log.mark_failed, key, f"{type(exc).__name__}: {exc}")
            raise
        await asyncio.to_thread(log.mark_sent, key, result.provider_id)
        stats["sent"] += 1
        if settings.email_mode == "resend" and settings.email_send_interval_seconds:
            await asyncio.sleep(settings.email_send_interval_seconds)  # stay under Resend's rate limit
    except Exception as exc:
        stats["failed"] += 1
        log_event(_log, "job email failed", {"kind": kind, "key": key, "error": type(exc).__name__}, level=logging.ERROR)


def birthday_dates(today: date) -> list[tuple[int, int]]:
    """(month, day) pairs celebrated today. Feb 29 birthdays are celebrated on Feb 28 in non-leap years."""
    pairs = [(today.month, today.day)]
    if (today.month, today.day) == (2, 28) and not _is_leap(today.year):
        pairs.append((2, 29))
    return pairs


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


async def run_birthdays(repo: JobsRepo, log: EmailLogRepo, suppressions: SuppressionRepo, today: date, deadline: float) -> dict:
    stats = new_stats()
    for month, day in birthday_dates(today):
        for profile in await asyncio.to_thread(repo.birthday_profiles, month, day):
            if time.monotonic() > deadline:
                stats["stopped_early"] = True
                return stats
            name = profile.get("full_name") or "friend"
            await send_once(log, f"birthday:{profile['user_id']}:{today.year}", "birthday", profile["email"],
                            lambda n=name, e=profile["email"], m=month: birthday_email(n, unsubscribe_url(e), m), stats,
                            headers=unsubscribe_headers(profile["email"]))
    for month, day in birthday_dates(today):
        guests = await asyncio.to_thread(repo.birthday_guest_buyers, month, day)
        suppressed = await asyncio.to_thread(suppressions.suppressed_among, [g["email"] for g in guests])
        for guest in guests:
            if time.monotonic() > deadline:
                stats["stopped_early"] = True
                return stats
            if guest["email"] in suppressed:
                stats["skipped"] += 1
                continue
            name = guest.get("customer_name") or "friend"
            await send_once(log, f"birthday:{guest['email']}:{today.year}", "birthday", guest["email"],
                            lambda n=name, e=guest["email"], m=month: birthday_email(n, unsubscribe_url(e), m), stats,
                            headers=unsubscribe_headers(guest["email"]))
    return stats


async def run_followups(repo: JobsRepo, log: EmailLogRepo, suppressions: SuppressionRepo, now: datetime, deadline: float) -> dict:
    stats = new_stats()
    orders = await asyncio.to_thread(repo.paid_orders_between, now - FOLLOWUP_AFTER - FOLLOWUP_CATCHUP, now - FOLLOWUP_AFTER)
    suppressed = await asyncio.to_thread(suppressions.suppressed_among, [o["email"] for o in orders])
    for order in orders:
        if time.monotonic() > deadline:
            stats["stopped_early"] = True
            return stats
        if order["email"].lower() in suppressed:
            stats["skipped"] += 1
            continue
        titles = [i["title"] for i in order.get("order_items", [])]
        await send_once(log, f"followup:{order['id']}", "followup", order["email"],
                        lambda o=order, t=titles: followup_email(o.get("customer_name") or "there", t, unsubscribe_url(o["email"])), stats,
                        headers=unsubscribe_headers(order["email"]))
    return stats


async def run_cleanup(repo: JobsRepo, now: datetime) -> dict:
    """Delete signups that never verified their email (their profile goes with the auth user)."""
    deleted = failed = 0
    for row in await asyncio.to_thread(repo.stale_unverified, now - UNVERIFIED_MAX_AGE):
        try:
            await asyncio.to_thread(repo.delete_user, row["user_id"])
            deleted += 1
        except Exception as exc:
            failed += 1
            log_event(_log, "stale signup cleanup failed", {"user_id": row["user_id"], "error": type(exc).__name__}, level=logging.ERROR)
    return {"deleted": deleted, "failed": failed}


async def run_daily(
    repo: JobsRepo, log: EmailLogRepo, suppressions: SuppressionRepo, now: datetime | None = None, budget_seconds: float | None = None,
    admin=None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    started = time.monotonic()
    deadline = started + (budget_seconds if budget_seconds is not None else settings.jobs_budget_seconds)
    result: dict = {}
    jobs = [
        ("birthdays", lambda: run_birthdays(repo, log, suppressions, today_sgt(), deadline)),
        ("followups", lambda: run_followups(repo, log, suppressions, now, deadline)),
        ("cleanup", lambda: run_cleanup(repo, now)),
    ]
    if admin is not None:  # finish any newsletter whose background send was cut off by a restart
        from app.modules.admin.newsletter import resume_stalled

        jobs.append(("newsletters", lambda: resume_stalled(admin, log, suppressions, deadline)))
    for name, job in jobs:
        try:
            result[name] = await job()
        except Exception as exc:  # one broken job must not stop the others
            log_event(_log, "daily job crashed", {"job": name, "error": type(exc).__name__}, level=logging.ERROR)
            result[name] = {"error": type(exc).__name__}
    result["duration_ms"] = round((time.monotonic() - started) * 1000)
    log_event(_log, "daily jobs finished", result)
    return result
