"""The weekly digest as mail: rendered, sent, and the "Not useful" links in it honoured.

Rendering follows the digest on screen exactly: the dollars at stake first, three
sections in a fixed order, each card an item, a supplier, a figure, one action
and "Not useful". An empty section is one muted line. Past ten cards, the rest
is "N smaller items" behind the link to the app.

Sending goes through Resend when RESEND_API_KEY is set, else through any
mailbox over SMTP (SMTP_HOST, SMTP_USER, SMTP_PASSWORD: a Gmail address with an
app password costs nothing and needs no domain). With neither the mail is
printed to the log, so the demo and a laptop behave the same as a deployment
apart from the delivery. Each week's digest is mailed once (PriceDigest.emailed_at).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import logging
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage

import httpx
from sqlalchemy.orm import Session

from app.auth.models import MANAGER_ROLES, User
from app.config import settings
from app.prices import engine, schemas
from app.prices.models import PriceDigest, PriceItem
from app.restaurant.models import Restaurant

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
#: The two colours mail is allowed: the app's danger and success.
UP = "#c2410c"
OWED = "#15803d"
INK = "#111111"
MUTED = "#6b6b6b"
RULE = "#e3e3e3"
FONT = "Inter, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"

#: Used only when SECRET_KEY is unset -- local development, like app.auth.mfa.
_DEV_KEY = "fullhouse-development-key-do-not-use-in-production"


# --- the "Not useful" link ---------------------------------------------------------------


def _key() -> bytes:
    return (settings.secret_key or _DEV_KEY).encode()


def not_useful_token(restaurant_id: str | None, item_id: str, week: date) -> str:
    """A link anyone holding the mail can use, for this item in this week's digest only."""
    payload = f"{restaurant_id or ''}|{item_id}|{week.isoformat()}".encode()
    signature = hmac.new(_key(), payload, hashlib.sha256).digest()[:20]
    return base64.urlsafe_b64encode(payload + b"|" + signature).decode().rstrip("=")


def read_not_useful_token(token: str, today: date | None = None) -> tuple[str | None, str, date] | None:
    """(restaurant id, item id, week) when the token is genuine and recent; None otherwise."""
    today = today or date.today()
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload, signature = raw.rsplit(b"|", 1)
        restaurant_id, item_id, week_text = payload.decode().split("|")
        week = date.fromisoformat(week_text)
    except (ValueError, UnicodeDecodeError):
        return None
    expected = hmac.new(_key(), payload, hashlib.sha256).digest()[:20]
    if not hmac.compare_digest(signature, expected):
        return None
    if not (today - timedelta(weeks=engine.WINDOW_WEEKS) <= week <= today + timedelta(days=7)):
        return None
    return (restaurant_id or None), item_id, week


def apply_not_useful(db: Session, token: str, today: date | None = None) -> PriceItem | None:
    """Hide the item the token names for four weeks, and rebuild the digest. None when the link is no good."""
    today = today or date.today()
    read = read_not_useful_token(token, today)
    if read is None:
        return None
    restaurant_id, item_id, _week = read
    item = db.get(PriceItem, item_id)
    if item is None or item.restaurant_id != restaurant_id:
        return None
    item.hidden_until = today + timedelta(weeks=engine.HIDE_WEEKS)
    engine.build_digest(db, restaurant_id, today)
    db.flush()
    return item


# --- rendering ----------------------------------------------------------------------------


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def subject_for(digest: schemas.DigestOut) -> str:
    if digest.total <= 0:
        return "Nothing to act on in your prices this week"
    return f"{_money(digest.total)} at stake this week"


def render(db: Session, digest_row: PriceDigest, restaurant: Restaurant | None, link_base: str, api_base: str) -> tuple[str, str, str]:
    """(subject, html, text) for one digest, with every link absolute."""
    digest = engine.digest_out(db, digest_row)
    e = html.escape
    app_link = f"{link_base}/#digest" if link_base else "#digest"
    questions_link = f"{link_base}/#questions" if link_base else "#questions"
    subject = subject_for(digest)

    def not_useful_link(card: schemas.Card) -> str:
        token = not_useful_token(digest_row.restaurant_id, card.item_id, digest_row.week_start)
        return f"{api_base}/api/v1/prices/digest/not-useful/{token}"

    def item_link(card: schemas.Card) -> str:
        return f"{link_base}/#item/{card.item_id}" if link_base else f"#item/{card.item_id}"

    colour = {"up": UP, "owed": OWED}
    parts: list[str] = []
    text: list[str] = []
    parts.append(
        f'<div style="font-family:{FONT};color:{INK};max-width:560px;margin:0 auto;padding:24px 16px;'
        f'font-variant-numeric:tabular-nums;font-feature-settings:\'tnum\'">'
    )
    parts.append(f'<div style="font-size:32px;line-height:40px;font-weight:600">{e(_money(digest.total))}</div>')
    parts.append(
        f'<div style="font-size:17px;line-height:24px;color:{MUTED};margin-top:4px">at stake this week, across '
        f'{e(_plural(digest.item_count, "item"))}, {e(_plural(digest.supplier_count, "supplier"))}</div>'
    )
    text.append(f"{_money(digest.total)} at stake this week, across {_plural(digest.item_count, 'item')}, "
                f"{_plural(digest.supplier_count, 'supplier')}.")
    if digest.questions_open:
        parts.append(
            f'<div style="font-size:15px;line-height:20px;margin-top:16px">{digest.questions_open} '
            f'{"question" if digest.questions_open == 1 else "questions"} waiting. '
            f'<a href="{e(questions_link)}" style="color:{INK}">Answer them</a></div>'
        )
        text.append(f"{_plural(digest.questions_open, 'question')} waiting. Answer them: {questions_link}")
    for section in digest.sections:
        shade = colour.get(section.key, INK)
        parts.append(
            f'<div style="display:flex;justify-content:space-between;margin-top:32px;padding-bottom:8px;'
            f'border-bottom:1px solid {RULE};font-size:13px;line-height:16px;font-weight:600;color:{shade}">'
            f'<span>{e(section.label)}</span><span>{e(_money(section.total)) if section.cards else ""}</span></div>'
        )
        text.append("")
        text.append(f"{section.label.upper()}{'  ' + _money(section.total) if section.cards else ''}")
        if not section.cards:
            parts.append(f'<div style="font-size:15px;line-height:20px;color:{MUTED};padding:12px 0">Nothing this week.</div>')
            text.append("Nothing this week.")
            continue
        for card in section.cards:
            parts.append(
                f'<div style="border:1px solid {RULE};border-radius:8px;padding:16px;margin-top:12px">'
                f'<div style="display:flex;justify-content:space-between;font-size:17px;line-height:24px;font-weight:600">'
                f'<a href="{e(item_link(card))}" style="color:{INK};text-decoration:none">{e(card.item_name)}</a>'
                f'<span style="color:{shade}">{e(_money(card.impact))}</span></div>'
                f'<div style="font-size:15px;line-height:20px;margin-top:4px">{e(card.sentence)}</div>'
                f'<div style="display:flex;justify-content:space-between;align-items:center;margin-top:12px">'
                f'<a href="{e(item_link(card))}" style="display:inline-block;background:{INK};color:#fff;'
                f'padding:10px 16px;border-radius:8px;font-size:15px;line-height:20px;font-weight:600;'
                f'text-decoration:none">{e(card.action.label)}</a>'
                f'<a href="{e(not_useful_link(card))}" style="font-size:13px;line-height:16px;color:{MUTED}">Not useful</a>'
                f'</div></div>'
            )
            text.append(f"- {card.item_name}  {_money(card.impact)}")
            text.append(f"  {card.sentence}")
            text.append(f"  {card.action.label}: {item_link(card)}")
            text.append(f"  Not useful: {not_useful_link(card)}")
    if digest.hidden_count:
        parts.append(
            f'<div style="font-size:15px;line-height:20px;color:{MUTED};margin-top:16px">'
            f'<a href="{e(app_link)}" style="color:{MUTED}">{digest.hidden_count} smaller '
            f'{"item" if digest.hidden_count == 1 else "items"}</a></div>'
        )
        text.append("")
        text.append(f"{_plural(digest.hidden_count, 'smaller item')}: {app_link}")
    through = f" Invoices through {engine.short_date(digest.invoices_through)}" if digest.invoices_through else ""
    names = f" from {', '.join(digest.supplier_names)}" if digest.supplier_names else ""
    footer = f"Week of {engine.short_date(digest.week_start)}.{through}{names}."
    parts.append(
        f'<div style="margin-top:32px;padding-top:16px;border-top:1px solid {RULE};font-size:13px;line-height:16px;color:{MUTED}">'
        f'<div>Reply to this email to ask about prices, invoices, or suppliers.</div>'
        f'<div style="margin-top:8px">{e(footer)}</div>'
        f'<div style="margin-top:8px"><a href="{e(app_link)}" style="color:{INK}">Open the digest</a></div></div>'
    )
    text.append("")
    text.append("Reply to this email to ask about prices, invoices, or suppliers.")
    text.append(footer)
    text.append(f"Open the digest: {app_link}")
    parts.append("</div>")
    title = e(f"{restaurant.name} · {subject}" if restaurant and restaurant.name else subject)
    page = (
        f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        f'<title>{title}</title></head><body style="margin:0;background:#fafafa">{"".join(parts)}</body></html>'
    )
    return subject, page, "\n".join(text)


# --- sending ------------------------------------------------------------------------------


def send(to: list[str], subject: str, body_html: str, body_text: str) -> bool:
    """Deliver with Resend, else over SMTP, else print to the log. Returns whether it was delivered."""
    if not to:
        return False
    if settings.resend_api_key:
        return _send_resend(to, subject, body_html, body_text)
    if settings.smtp_configured:
        return _send_smtp(to, subject, body_html, body_text)
    logger.info("mail (no RESEND_API_KEY or SMTP_HOST, not sent) to=%s subject=%r\n%s", ", ".join(to), subject, body_text)
    return False


def _send_smtp(to: list[str], subject: str, body_html: str, body_text: str) -> bool:
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = ", ".join(to)
    message["Subject"] = subject
    message.set_content(body_text)
    message.add_alternative(body_html, subtype="html")
    host, port = settings.smtp_host or "", settings.smtp_port
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
    with server:
        if port != 465:
            server.starttls()
        server.login(settings.smtp_user or "", settings.smtp_password or "")
        server.send_message(message)
    return True


def _send_resend(to: list[str], subject: str, body_html: str, body_text: str) -> bool:
    response = httpx.post(
        RESEND_URL,
        headers={"Authorization": f"Bearer {settings.resend_api_key}"},
        json={"from": settings.mail_from, "to": to, "subject": subject, "html": body_html, "text": body_text},
        timeout=20,
    )
    response.raise_for_status()
    return True


def recipients(db: Session, restaurant_id: str | None) -> list[str]:
    """Owners and admins: the people the digest is for."""
    rows = db.query(User.email).filter(User.restaurant_id == restaurant_id, User.role.in_(MANAGER_ROLES)).order_by(User.created_at)
    return [email for (email,) in rows if email]


def send_digest(
    db: Session, restaurant_id: str | None, to: list[str], link_base: str, api_base: str, today: date | None = None,
) -> schemas.EmailOut:
    """Build this week's digest and mail it to `to` now."""
    today = today or date.today()
    digest = engine.build_digest(db, restaurant_id, today)
    restaurant = db.get(Restaurant, restaurant_id) if restaurant_id else None
    subject, body_html, body_text = render(db, digest, restaurant, link_base, api_base)
    delivered = send(to, subject, body_html, body_text)
    digest.emailed_at = datetime.now(timezone.utc)
    db.flush()
    return schemas.EmailOut(subject=subject, sent_to=to, delivered=delivered)


def send_weekly(db: Session, restaurant_id: str | None, today: date | None = None) -> bool:
    """The Monday mail for one restaurant, unless this week's already went. Returns whether one was sent."""
    today = today or date.today()
    week = engine.week_start(today)
    existing = (
        db.query(PriceDigest).filter(PriceDigest.restaurant_id == restaurant_id, PriceDigest.week_start == week).first()
    )
    if existing is not None and existing.emailed_at is not None:
        return False
    to = recipients(db, restaurant_id)
    if not to:
        return False
    base = settings.link_base
    send_digest(db, restaurant_id, to, base, base, today)
    return True
