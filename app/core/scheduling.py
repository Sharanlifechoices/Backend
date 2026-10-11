"""Session scheduling rules shared by the cart and booking flows (times are Singapore time)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("Asia/Singapore")
SLOTS = ["11:00 AM", "2:00 PM", "5:00 PM", "7:00 PM", "9:00 PM"]
BOOKING_WINDOW_DAYS = 30


def today_sgt() -> date:
    return datetime.now(TIMEZONE).date()


def validate_preferred_slot(preferred_date: date | None, preferred_time: str | None, today: date | None = None) -> None:
    """Raises ValueError with a user-facing message if the date/time is missing or out of range."""
    today = today or today_sgt()
    if preferred_date is None or not preferred_time:
        raise ValueError("Please choose a preferred date and time for this session")
    if preferred_time not in SLOTS:
        raise ValueError("Please choose one of the available time slots")
    if preferred_date < today:
        raise ValueError("The preferred date cannot be in the past")
    if preferred_date > today + timedelta(days=BOOKING_WINDOW_DAYS):
        raise ValueError(f"Sessions can be booked up to {BOOKING_WINDOW_DAYS} days ahead")
