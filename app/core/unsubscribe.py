"""Stateless, signed unsubscribe links.

The token is the recipient's email plus an HMAC, so it works for everyone we email (accounts and
guest purchasers alike), needs no lookup table, and cannot be forged or used for another address.
It never expires: a link in a year-old email must still work.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Optional
from urllib.parse import quote

from app.core.config import settings

_PURPOSE = b"unsubscribe:"


def _sign(email: str) -> str:
    digest = hmac.new(settings.otp_secret.encode(), _PURPOSE + email.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:24]).decode().rstrip("=")


def make_token(email: str) -> str:
    email = email.strip().lower()
    return f"{base64.urlsafe_b64encode(email.encode()).decode().rstrip('=')}.{_sign(email)}"


def parse_token(token: str) -> Optional[str]:
    """Returns the email the token was issued for, or None if it is malformed or tampered with."""
    try:
        raw_email, sig = token.split(".", 1)
        email = base64.urlsafe_b64decode(raw_email + "=" * (-len(raw_email) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    return email if hmac.compare_digest(_sign(email), sig) else None


def unsubscribe_url(email: str) -> str:
    return f"{settings.public_api_url}/unsubscribe?token={quote(make_token(email), safe='')}"


def unsubscribe_headers(email: str) -> dict[str, str]:
    """RFC 8058 one-click headers: Gmail/Outlook show an Unsubscribe button that POSTs to this URL."""
    return {"List-Unsubscribe": f"<{unsubscribe_url(email)}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
