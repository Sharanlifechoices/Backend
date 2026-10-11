"""One-time-code logic: generation, HMAC hashing, cooldown, attempt limits, expiry.

Storage sits behind the small `OtpStore` interface so the rules can be tested without Supabase.
Only an HMAC of the code is ever stored; the plaintext exists in memory just long enough to email it.
"""

from __future__ import annotations

import hmac
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal, Protocol

from app.core.config import settings

Purpose = Literal["signup", "reset"]


class OtpError(Exception):
    """Verification failed. `kind` is one of: invalid_or_expired, invalid_code, too_many_attempts."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


class OtpCooldown(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"retry after {retry_after}s")
        self.retry_after = retry_after


@dataclass
class OtpRecord:
    id: str
    email: str
    purpose: str
    code_hash: str
    attempts: int
    expires_at: datetime
    consumed_at: datetime | None
    created_at: datetime


class OtpStore(Protocol):
    def latest(self, email: str, purpose: str) -> OtpRecord | None: ...
    def invalidate_active(self, email: str, purpose: str) -> None: ...
    def insert(self, email: str, purpose: str, code_hash: str, expires_at: datetime) -> OtpRecord: ...
    def record_attempt(self, otp_id: str) -> None: ...
    def consume(self, otp_id: str) -> None: ...


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_code(email: str, purpose: str, code: str) -> str:
    message = f"{email.lower()}:{purpose}:{code}".encode()
    return hmac.new(settings.otp_secret.encode(), message, hashlib.sha256).hexdigest()


class OtpService:
    def __init__(self, store: OtpStore, now: Callable[[], datetime] = _utcnow):
        self.store = store
        self.now = now

    def issue(self, email: str, purpose: Purpose) -> str:
        """Create a new code (invalidating older ones). Raises OtpCooldown if asked too soon."""
        email = email.lower()
        latest = self.store.latest(email, purpose)
        if latest:
            wait = settings.otp_resend_cooldown_seconds - (self.now() - latest.created_at).total_seconds()
            if wait > 0:
                raise OtpCooldown(int(wait) + 1)
        code = f"{secrets.randbelow(1_000_000):06d}"
        self.store.invalidate_active(email, purpose)
        self.store.insert(
            email, purpose, hash_code(email, purpose, code), self.now() + timedelta(seconds=settings.otp_ttl_seconds)
        )
        return code

    def verify(self, email: str, purpose: Purpose, code: str) -> None:
        """Return on success (code is consumed). Raises OtpError otherwise."""
        email = email.lower()
        record = self.store.latest(email, purpose)
        if record is None or record.consumed_at is not None or record.expires_at <= self.now():
            raise OtpError("invalid_or_expired")
        if record.attempts >= settings.otp_max_attempts:
            raise OtpError("too_many_attempts")
        if not hmac.compare_digest(record.code_hash, hash_code(email, purpose, code)):
            attempts_used = record.attempts + 1  # read before record_attempt (stores may mutate `record`)
            self.store.record_attempt(record.id)
            if attempts_used >= settings.otp_max_attempts:
                self.store.consume(record.id)  # burn the code after the last allowed attempt
                raise OtpError("too_many_attempts")
            raise OtpError("invalid_code")
        self.store.consume(record.id)


class SupabaseOtpStore:
    """OtpStore backed by the `email_otps` table (service-role client)."""

    def __init__(self, client):
        self._client = client

    @staticmethod
    def _parse(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None

    def _to_record(self, row: dict) -> OtpRecord:
        return OtpRecord(
            id=row["id"],
            email=row["email"],
            purpose=row["purpose"],
            code_hash=row["code_hash"],
            attempts=row["attempts"],
            expires_at=self._parse(row["expires_at"]),
            consumed_at=self._parse(row.get("consumed_at")),
            created_at=self._parse(row["created_at"]),
        )

    def latest(self, email, purpose):
        rows = (
            self._client.table("email_otps").select("*").eq("email", email).eq("purpose", purpose)
            .order("created_at", desc=True).limit(1).execute().data
        )
        return self._to_record(rows[0]) if rows else None

    def invalidate_active(self, email, purpose):
        self._client.table("email_otps").update({"consumed_at": _utcnow().isoformat()}) \
            .eq("email", email).eq("purpose", purpose).is_("consumed_at", "null").execute()

    def insert(self, email, purpose, code_hash, expires_at):
        row = self._client.table("email_otps").insert(
            {"email": email, "purpose": purpose, "code_hash": code_hash, "expires_at": expires_at.isoformat()}
        ).execute().data[0]
        return self._to_record(row)

    def record_attempt(self, otp_id):
        # Read-modify-write is acceptable here: the per-email/IP rate limits bound concurrency.
        row = self._client.table("email_otps").select("attempts").eq("id", otp_id).limit(1).execute().data[0]
        self._client.table("email_otps").update({"attempts": row["attempts"] + 1}).eq("id", otp_id).execute()

    def consume(self, otp_id):
        self._client.table("email_otps").update({"consumed_at": _utcnow().isoformat()}).eq("id", otp_id).execute()
