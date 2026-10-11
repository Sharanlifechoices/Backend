"""Branded HTML email templates. All user-supplied values are HTML-escaped."""

from __future__ import annotations

import calendar
import re
from html import escape

from app.core.birthday_readings import MONTH_READINGS
from app.core.config import settings

_GOLD = "#d4a853"
_BG = "#050510"
_CARD = "#0f0f2d"
SITE_LABEL = "www.my-lifechoices.com"
CONTACT_EMAIL = "yogi.sk7@gmail.com"


def _layout(title: str, body_html: str, footer_html: str = "") -> str:
    title_row = (
        f'<tr><td style="font-size:20px;font-weight:bold;padding-bottom:12px">{escape(title)}</td></tr>' if title else ""
    )
    return f"""<!doctype html><html><body style="margin:0;background:{_BG};font-family:Arial,Helvetica,sans-serif;color:#f8f7ff">
<table width="100%" cellpadding="0" cellspacing="0" style="background:{_BG};padding:24px 12px"><tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:{_CARD};border-radius:16px;padding:32px">
<tr><td style="text-align:center;font-size:22px;color:{_GOLD};letter-spacing:1px;padding-bottom:12px">My LifeChoices</td></tr>
{title_row}
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


def _bold(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)


def _letter(*paragraphs: str) -> str:
    """Paragraphs of plain letter text (escaped here); **bold** and newlines are allowed."""
    return "".join("<p>" + _bold(escape(t)).replace("\n", "<br>") + "</p>" for t in paragraphs)


def booking_url() -> str:
    """Opens the booking window on the site (signed-out visitors are sent to sign in first)."""
    return f"{settings.frontend_url}/?book=1"


def first_name(name: str) -> str:
    return (name or "").strip().split(" ")[0] or "friend"


def _signature(closing: str = "With warmth and light,") -> str:
    return (
        "<p>Keep your vibration high. Stay aligned with your Dharma. And remember —<br><strong>Numbers Say So.</strong></p>"
        f"<p>{escape(closing)}<br><strong>Sharan Ji Baba</strong><br>"
        f'🌐 <a style="color:{_GOLD}" href="{escape(settings.frontend_url, quote=True)}">{escape(SITE_LABEL)}</a><br>'
        f"✉️ {escape(CONTACT_EMAIL)}</p>"
    )


def book_label(item: dict) -> str:
    return f"{item['title']} — {item['subtitle']}" if item.get("subtitle") else item["title"]


def purchase_email(name: str, items: list[dict], total_label: str) -> tuple[str, str]:
    """Order confirmation in Sharan's voice. Books get the onboarding letter with that book's own download link."""
    books = [i for i in items if i.get("type") == "book"]
    sessions = [i for i in items if i.get("type") != "book"]
    session_html = ""
    if sessions:
        rows = "".join(
            f"<li>{escape(i['title'])}"
            + (f' <span style="color:#8a87a6">({escape(i["detail"])})</span>' if i.get("detail") else "")
            + "</li>"
            for i in sessions
        )
        session_html = (
            f"<p>Your session request{'s' if len(sessions) > 1 else ''}:</p><ul>{rows}</ul>"
            "<p>My team will confirm your slot with you personally.</p>"
        )
    paid = f'<p style="font-size:12px;color:#8a87a6">Paid: {escape(total_label)}</p>'

    if not books:
        body = (
            _letter(f"Namaste, {first_name(name)}.", "Thank you — your payment is received and your session is on its way to being confirmed.")
            + session_html
            + _signature()
            + paid
        )
        return "Your session request is received", _layout("", body)

    many = len(books) > 1
    links = ""
    for b in books:
        if b.get("pdf_url"):
            links += _button(f"Download: {book_label(b)}", b["pdf_url"])
        else:  # misconfigured catalog row: never send a dead button, ask them to write in
            links += f"<p><strong>{escape(book_label(b))}</strong> — your link is being prepared, just reply to this email.</p>"
    ready = (
        "**Your copies are ready —** here are your one-time, non-editable download links. They're yours alone, so save them somewhere safe once you've claimed them:"
        if many
        else "**Your copy is ready —** here's your one-time, non-editable download link. It's yours alone, so save it somewhere safe once you've claimed it:"
    )
    body = (
        _letter(
            "Namaste, and welcome.",
            "You just did more than buy a book. You stepped into your Cosmic Blueprint.\n"
            "I'm Sharan Ji Baba. For half a lifetime, I've dived into the deep end of Vedic, Chinese, and Egyptian energy sciences, "
            "astrology, and yogic practice — so you don't have to drown in the shallow end of life's confusion.",
            "What you're holding is your blueprint and your stepping stones. Woven through it is something more: the Chronicle of "
            "Suryaloka — the story behind the numbers, of a King and Queen who were also a Sun and a Moon, and seven children who "
            "became seven planets. Read it as guidance. Read it as myth. Either way, it's here to show you what the numbers have been "
            "trying to tell you all along.",
            ready,
        )
        + links
        + _letter(
            "I firmly believe foreknowledge is power. If you know what's coming, you can meet it differently. So take your time, "
            "sit with what resonates, and let the rest go.",
            "And if this book leaves you wanting more —\n"
            "A personal reading decodes your specific 9-year cycles, so you catch the pitfalls and negotiate success.",
            "If that calls to you, you're always welcome to book a session, whenever it feels right.",
        )
        + _button("Book a session", booking_url())
        + session_html
        + _signature()
        + paid
    )
    return "Welcome to the World of NumberSaysSo — Your Book Is Ready", _layout("", body)


def _unsub_footer(url: str) -> str:
    if not url:
        return ""
    return (
        "You are receiving this because you shopped with us. "
        f'<a style="color:{_GOLD}" href="{escape(url, quote=True)}">Unsubscribe</a>'
    )


def followup_email(name: str, item_titles: list[str] | None = None, unsubscribe_url: str = "") -> tuple[str, str]:
    """Sent about a week after a purchase. The copy is the same for every book."""
    body = (
        _letter(
            "Namaste, and welcome back.",
            "By now, you've had a little time with your book. Some pages you probably read once and moved on.\n"
            "Others — I'd guess — you read twice, because something in them felt a little too accurate.\n"
            "That's not a coincidence. That's the numbers doing their work.",
            "I'd love to hear what landed for you.",
            "What did you learn about yourself that you didn't expect?\n"
            "What part of your Chronicle felt like it was written about you, specifically?\n"
            "Where did it challenge you — or confirm something you already knew deep down?",
            "Just hit reply and tell me. I read every single one myself, and your reflections often become the seed for the next "
            "chapter someone else needs.",
            "Here's the thing about a book, though — it can only take you as far as the page.\n"
            "A personal reading takes you further. It maps your own 9-year cycles, the days that work for you and the ones that "
            "don't, so you're not just understanding your pattern — you're finally working with it instead of against it. "
            "That's the part a book alone can't give you.",
            "If you've read this far and you're curious what that would look like for you, I'm not going anywhere. Reply to this "
            "email, message me on WhatsApp, or book a slot directly through the website — whichever feels easiest.",
        )
        + _button("Book a session", booking_url())
        + _signature()
    )
    return "How has your Blueprint been speaking to you?", _layout("", body, _unsub_footer(unsubscribe_url))


def birthday_email(name: str, unsubscribe_url: str = "", month: int | None = None) -> tuple[str, str]:
    """`month` is the birth month (1-12); its reading, if Sharan has written one, is included."""
    reading = MONTH_READINGS.get(month or 0, "").strip()
    month_html = ""
    if reading:
        month_html = f"<p><strong>For you, this {escape(calendar.month_name[month])} year:</strong></p>" + "".join(
            "<p>" + escape(part).replace("\n", "<br>") + "</p>" for part in reading.split("\n\n")
        )
    body = (
        _letter(
            f"Namaste, {first_name(name)},",
            "Happy birthday.",
            "Today isn't just another day on the calendar for you — it's the day your own cycle turns over. A new year of your "
            "Blueprint begins, and I didn't want it to pass without a word from me.",
        )
        + month_html
        + _letter(
            "Carry that with you as gently or as seriously as you like. You don't need to do anything with it today except blow "
            "out a candle and let it settle.",
            "That's really all I wanted to say. Just — happy birthday, and thank you for letting this journey include me in it, "
            "even in this small way.",
            "Wishing you a year that surprises you in all the right ways.",
        )
        + _button("Book your birthday reading", booking_url())
        + _signature("With love,")
    )
    return "Happy Birthday — a little something for your year ahead 🎂", _layout("", body, _unsub_footer(unsubscribe_url))


def newsletter_email(subject: str, body_html: str, unsubscribe_url: str) -> tuple[str, str]:
    """`body_html` is admin-authored trusted HTML (only admins can reach the send endpoint)."""
    footer = (
        f'You are receiving this because you have an account with us. '
        f'<a style="color:{_GOLD}" href="{escape(unsubscribe_url, quote=True)}">Unsubscribe</a>'
    )
    return subject, _layout(subject, body_html, footer)
