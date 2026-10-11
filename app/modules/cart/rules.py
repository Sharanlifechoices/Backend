"""Rules for what may sit in a cart or be checked out. Shared by the cart and checkout flows."""

from __future__ import annotations

from datetime import date
from typing import Optional

from app.core.scheduling import validate_preferred_slot


def check_line(item: Optional[dict], preferred_date: Optional[date], preferred_time: Optional[str]) -> None:
    """Raise ValueError (user-facing) if this line cannot be bought."""
    if item is None:
        raise ValueError("This item is no longer available")
    if item.get("price_minor") is None:
        raise ValueError("This item is enquiry-only and cannot be added to the cart")
    if item["requires_schedule"]:
        validate_preferred_slot(preferred_date, preferred_time)
    elif preferred_date or preferred_time:
        raise ValueError("This item does not take a preferred date or time")
