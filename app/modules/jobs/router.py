"""Scheduled-job entrypoint, called once a day by cron-job.org with the X-Jobs-Secret header."""

from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.core.config import settings
from app.core.logging_setup import log_event
from app.core.rate_limit import check_key, client_ip
from app.modules.admin.repo import AdminRepo, get_admin_repo
from app.modules.jobs.repo import EmailLogRepo, JobsRepo, get_email_log, get_jobs_repo
from app.modules.jobs.service import run_daily
from app.modules.unsubscribe.repo import SuppressionRepo, get_suppression_repo

_log = logging.getLogger("jobs")
router = APIRouter(prefix="/jobs", tags=["jobs"])


def require_jobs_secret(request: Request, x_jobs_secret: Annotated[str, Header()] = "") -> None:
    ip = client_ip(request)
    configured = settings.jobs_secret
    if not configured or not hmac.compare_digest(x_jobs_secret.encode(), configured.encode()):
        check_key(f"jobs-fail:{ip}", 10, 900)  # lockout after repeated failures
        log_event(_log, "jobs auth failed", {"ip": ip}, level=logging.WARNING)
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Forbidden")


@router.post("/daily", dependencies=[Depends(require_jobs_secret)])
async def daily(
    repo: JobsRepo = Depends(get_jobs_repo), log: EmailLogRepo = Depends(get_email_log),
    suppressions: SuppressionRepo = Depends(get_suppression_repo), admin: AdminRepo = Depends(get_admin_repo),
):
    """Birthday emails, 7-day follow-ups and stale-signup cleanup. Safe to call repeatedly."""
    return await run_daily(repo, log, suppressions, admin=admin)
