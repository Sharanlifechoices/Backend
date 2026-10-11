"""Razorpay: server-side order creation and signature checks (plain httpx, every call logged)."""

from __future__ import annotations

import hashlib
import hmac

import httpx

from app.core.config import settings
from app.core.http_logging import hooks_for

ORDERS_URL = "https://api.razorpay.com/v1/orders"


class RazorpayError(Exception):
    pass


def _hmac_hex(secret: str, message: str | bytes) -> str:
    data = message.encode() if isinstance(message, str) else message
    return hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()


def verify_payment_signature(razorpay_order_id: str, payment_id: str, signature: str) -> bool:
    """Checkout success signature: HMAC_SHA256(order_id|payment_id, key_secret)."""
    if not settings.razorpay_key_secret or not signature:
        return False
    return hmac.compare_digest(_hmac_hex(settings.razorpay_key_secret, f"{razorpay_order_id}|{payment_id}"), signature)


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """Webhook signature: HMAC_SHA256(raw body, webhook_secret). Must use the raw bytes."""
    if not settings.razorpay_webhook_secret or not signature:
        return False
    return hmac.compare_digest(_hmac_hex(settings.razorpay_webhook_secret, raw_body), signature)


class RazorpayClient:
    async def create_order(self, amount_minor: int, currency: str, receipt: str, notes: dict) -> dict:
        if not settings.razorpay_enabled:
            raise RazorpayError("Razorpay is not configured")
        payload = {"amount": amount_minor, "currency": currency, "receipt": receipt, "notes": notes}
        try:
            async with httpx.AsyncClient(timeout=20.0, event_hooks=hooks_for("razorpay")) as client:
                resp = await client.post(
                    ORDERS_URL, json=payload, auth=(settings.razorpay_key_id, settings.razorpay_key_secret)
                )
        except httpx.HTTPError as exc:
            raise RazorpayError(f"Razorpay unreachable: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise RazorpayError(f"Razorpay returned {resp.status_code}")
        return resp.json()


def get_razorpay() -> RazorpayClient:
    return RazorpayClient()
