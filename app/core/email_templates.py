"""Branded HTML email templates. All user-supplied values are HTML-escaped."""

from __future__ import annotations

from html import escape

from app.core.config import settings

_GOLD = "#d4a853"
_BG = "#050510"
_CARD = "#0f0f2d"


def _layout(title: str, body_html: str, footer_html: str = "") -> str:
    return f"""<!doctype html><html><body style="margin:0;background:{_BG};font-family:Arial,Helvetica,sans-serif;color:#f8f7ff">
<table width="100%" cellpadding="0" cellspacing="0" style="background:{_BG};padding:24px 12px"><tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:{_CARD};border-radius:16px;padding:32px">
<tr><td style="text-align:center;font-size:22px;color:{_GOLD};letter-spacing:1px;padding-bottom:12px">My LifeChoices</td></tr>
<tr><td style="font-size:20px;font-weight:bold;padding-bottom:12px">{escape(title)}</td></tr>
<tr><td style="font-size:15px;line-height:1.6;color:#d8d6ea">{body_html}</td></tr>
<tr><td style="font-size:12px;color:#8a87a6;padding-top:24px;border-top:1px solid #23234d">{footer_html or "With gratitude, Sharan Babaji &amp; team"}</td></tr>
</table></td></tr></table></body></html>"""


def _button(label: str, url: str) -> str:
    return (
        f'<p style="text-align:center;margin:24px 0"><a href="{escape(url, quote=True)}" '
        f'style="background:{_GOLD};color:#1a1204;padding:12px 24px;border-radius:999px;'
        f'text-decoration:none;font-weight:bold">{escape(label)}</a></p>'
    )


def otp_email(name: str, code: str, purpose: str) -> tuple[str, str]:
    action = "verify your email" if purpose == "signup" else "reset your password"
    minutes = settings.otp_ttl_seconds // 60
    subject = f"Your verification code: {code}"
    body = (
        f"<p>Hi {escape(name or 'there')},</p><p>Use this code to {action}:</p>"
        f'<p style="text-align:center;font-size:34px;letter-spacing:8px;font-weight:bold;color:{_GOLD}">{escape(code)}</p>'
        f"<p>It expires in {minutes} minutes. If you did not request it, you can ignore this email.</p>"
    )
    return subject, _layout("Your one-time code", body)


def welcome_email(name: str) -> tuple[str, str]:
    body = (
        f"<p>Welcome, {escape(name)}!</p><p>Your account is ready. Explore books, sessions and "
        "retreats with Sharan Babaji whenever you like.</p>" + _button("Visit the site", settings.frontend_url)
    )
    return "Welcome to My LifeChoices", _layout("Welcome", body)


def purchase_email(name: str, items: list[dict], total_label: str) -> tuple[str, str]:
    rows = ""
    for item in items:
        link = (
            f' — <a style="color:{_GOLD}" href="{escape(item["pdf_url"], quote=True)}">Download PDF</a>'
            if item.get("pdf_url")
            else ""
        )
        extra = f' <span style="color:#8a87a6">({escape(item["detail"])})</span>' if item.get("detail") else ""
        rows += f"<li>{escape(item['title'])}{extra}{link}</li>"
    body = (
        f"<p>Thank you, {escape(name)}! Your purchase is confirmed.</p><ul>{rows}</ul>"
        f"<p><strong>Total: {escape(total_label)}</strong></p>"
        "<p>Session bookings will be confirmed personally by Sharan Babaji's team.</p>"
    )
    return "Your My LifeChoices purchase", _layout("Purchase confirmed", body)


def followup_email(name: str, item_titles: list[str]) -> tuple[str, str]:
    titles = ", ".join(escape(t) for t in item_titles)
    body = (
        f"<p>Hi {escape(name)},</p><p>It has been a week since you picked up {titles}. "
        "We hope it is already bringing clarity. A one-to-one session can take it deeper.</p>"
        + _button("Book a session", settings.frontend_url)
    )
    return "How is your journey going?", _layout("A week on", body)


def birthday_email(name: str) -> tuple[str, str]:
    body = (
        f"<p>Happy Birthday, {escape(name)}! 🎂</p><p>May the year ahead bring you peace, purpose and joy.</p>"
        + _button("Celebrate with a session", settings.frontend_url)
    )
    return f"Happy Birthday, {name}!", _layout("Happy Birthday", body)


def newsletter_email(subject: str, body_html: str, unsubscribe_url: str) -> tuple[str, str]:
    """`body_html` is admin-authored trusted HTML (only admins can reach the send endpoint)."""
    footer = (
        f'You are receiving this because you have an account with us. '
        f'<a style="color:{_GOLD}" href="{escape(unsubscribe_url, quote=True)}">Unsubscribe</a>'
    )
    return subject, _layout(subject, body_html, footer)
