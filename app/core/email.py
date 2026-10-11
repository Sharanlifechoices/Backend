"""Email sending. EMAIL_MODE=mock logs the full email instead of sending it.

To go live: set EMAIL_MODE=resend, RESEND_API_KEY, RESEND_FROM_EMAIL (a Resend-verified
domain; DNS records live with the Zoho-hosted domain) and optionally EMAIL_REPLY_TO.
Nothing else in the codebase changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.http_logging import async_hooks_for
from app.core.logging_setup import log_event

_log = logging.getLogger("email")
RESEND_URL = "https://api.resend.com/emails"


class EmailError(RuntimeError):
    pass


@dataclass
class EmailResult:
    provider_id: str
    mode: str


async def send_email(
    *, to: str, subject: str, html: str, kind: str, headers: dict[str, str] | None = None
) -> EmailResult:
    """Send one email. `kind` (otp, purchase, newsletter...) is for logging/analytics."""
    if settings.email_mode == "mock":
        # Full content is logged on purpose so OTPs can be read from the log while mocked.
        # Use raw logging (not log_event/redact) so the OTP stays visible in mock mode only.
        _log.info(
            "[MOCK EMAIL] kind=%s to=%s subject=%s",
            kind,
            to,
            subject,
            extra={"data": {"kind": kind, "to": to, "subject": subject, "html": html, "headers": headers}},
        )
        return EmailResult(provider_id=f"mock-{kind}", mode="mock")

    if not settings.resend_api_key:
        raise EmailError("EMAIL_MODE=resend but RESEND_API_KEY is not set")

    payload: dict = {"from": settings.resend_from, "to": [to], "subject": subject, "html": html}
    if settings.email_reply_to:
        payload["reply_to"] = settings.email_reply_to
    if headers:
        payload["headers"] = headers

    async with httpx.AsyncClient(timeout=20.0, event_hooks=async_hooks_for("resend")) as client:
        resp = await client.post(
            RESEND_URL, json=payload, headers={"Authorization": f"Bearer {settings.resend_api_key}"}
        )
    if resp.status_code >= 400:
        log_event(_log, "resend rejected email", {"kind": kind, "to": to, "status": resp.status_code}, level=logging.ERROR)
        raise EmailError(f"Resend returned {resp.status_code}")
    return EmailResult(provider_id=resp.json().get("id", ""), mode="resend")
