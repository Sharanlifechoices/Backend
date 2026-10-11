"""Password policy shared by signup, reset and admin-set passwords."""

from __future__ import annotations

MIN_LENGTH = 8
_SPECIALS = set("!@#$%^&*()_+-=[]{};':\"\\|,.<>/?`~")


def password_failures(password: str) -> list[str]:
    """Return the human-readable rules the password fails (empty list = valid)."""
    failures = []
    if len(password) < MIN_LENGTH:
        failures.append(f"At least {MIN_LENGTH} characters")
    if not any(c.islower() for c in password):
        failures.append("At least one lowercase letter")
    if not any(c.isupper() for c in password):
        failures.append("At least one uppercase letter")
    if not any(c in _SPECIALS for c in password):
        failures.append("At least one special symbol (e.g. ! @ # $ %)")
    return failures


def validate_password(password: str) -> str:
    """Pydantic-validator friendly: returns the password or raises ValueError."""
    failures = password_failures(password)
    if failures:
        raise ValueError("Password must have: " + "; ".join(failures))
    return password
