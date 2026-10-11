"""Admin guard: shared secret in the X-Admin-Secret header.

Constant-time comparison, per-IP failure lockout, and every admin call is logged.
Known limitation (accepted): a single shared secret has no per-admin identity.
"""

from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import Header, HTTPException, Request, status

from app.core.config import settings
from app.core.logging_setup import log_event
from app.core.rate_limit import check_key, client_ip

_log = logging.getLogger("admin")
_FAIL_LIMIT = 10
_FAIL_WINDOW = 900


def require_admin(request: Request, x_admin_secret: Annotated[str, Header()] = "") -> None:
    ip = client_ip(request)
    configured = settings.admin_secret
    if not configured or not hmac.compare_digest(x_admin_secret.encode(), configured.encode()):
        check_key(f"admin-fail:{ip}", _FAIL_LIMIT, _FAIL_WINDOW)  # lockout after repeated failures
        log_event(_log, "admin auth failed", {"ip": ip, "path": request.url.path}, level=logging.WARNING)
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Forbidden")
    log_event(_log, "admin call", {"ip": ip, "method": request.method, "path": request.url.path})
