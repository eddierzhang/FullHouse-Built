"""Telling the restaurant something, once.

    from app.notify import service as notify
    notify.tell(db, restaurant_id, kind="autopilot_send", title="Sysco order goes out at 2:30 pm",
                body="$248.10, 6 lines. Cancel it before then if it looks wrong.", link="#autopilot",
                dedupe_key=f"autopilot:{po.id}")

A notification with a `dedupe_key` the restaurant has already had is not written again. `meter` adds to a
restaurant's monthly usage of something that costs money, and `within_budget` says whether there is room.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.notify.models import Notification, UsageMeter

#: Kinds that are also mailed to the owners and managers, because they need someone to act and the person may
#: not have the app open: an order about to go out by itself, one held or failed, a late or short delivery.
EMAIL_KINDS = {"autopilot_send", "autopilot_held", "autopilot_failed", "autopilot_off", "order_late", "delivery_short", "supplier_reply"}
#: Kinds only owners and managers see: they are about spending.
MANAGER_KINDS = {"autopilot_send", "autopilot_held", "autopilot_failed", "autopilot_off", "autopilot_sent", "supplier_reply", "order_below_minimum"}

#: What a restaurant may use in a month before the feature says no. One plan for now.
MONTHLY_BUDGETS = {"model_usd": 20.0, "voice_seconds": 6 * 3600.0, "messages": 500.0}


def tell(db: Session, restaurant_id: str, *, kind: str, title: str, dedupe_key: str, body: str | None = None,
         link: str | None = None, user_email: str | None = None) -> Notification | None:
    """Write one notification; None when this restaurant has already been told (same `dedupe_key`)."""
    if db.query(Notification.id).filter(Notification.restaurant_id == restaurant_id, Notification.dedupe_key == dedupe_key).first():
        return None
    row = Notification(restaurant_id=restaurant_id, user_email=user_email, kind=kind[:24], title=title[:200], body=body,
                       link=link, dedupe_key=dedupe_key[:120])
    db.add(row)
    if kind in EMAIL_KINDS:
        # Mailed by a job, from a row written with the notification: said once, even if the job runs twice.
        from app.ordering.models import Outbox

        db.add(Outbox(restaurant_id=restaurant_id, kind="notice", dedupe_key=f"notice:{dedupe_key}"[:120],
                      payload={"title": title[:200], "body": body or "", "link": link or ""}))
    db.flush()
    return row


def deliver_notices(db: Session, restaurant_id: str | None = None) -> int:
    """Mail every notice waiting, to the restaurant's owners and managers. Each row is claimed before its mail
    goes, so none is sent twice. On a site with no mailbox the notices stay in the app only. Returns how many went."""
    from html import escape

    from app.config import settings
    from app.ordering.models import Outbox
    from app.prices import mail

    query = db.query(Outbox).filter(Outbox.kind == "notice", Outbox.status == "pending")
    if restaurant_id:
        query = query.filter(Outbox.restaurant_id == restaurant_id)
    sent = 0
    for row in query.order_by(Outbox.created_at).limit(200).all():
        if not mail.can_send():
            row.status = "logged"  # nowhere to send from: it is in the app's notifications, and that is all
            continue
        to = mail.recipients(db, row.restaurant_id)
        claimed = db.query(Outbox).filter(Outbox.id == row.id, Outbox.status == "pending").update(
            {"status": "sending", "attempts": Outbox.attempts + 1}, synchronize_session=False)
        db.commit()
        if claimed != 1 or not to:
            continue
        db.refresh(row)
        link = f"{settings.link_base}/{row.payload.get('link') or ''}" if settings.link_base else ""
        text = "\n\n".join(part for part in (row.payload.get("body") or "", f"Open FullHouse: {link}" if link else "") if part)
        html = (f'<div style="font-family:system-ui,sans-serif;font-size:15px;line-height:22px">{escape(row.payload.get("body") or "")}'
                + (f'<p><a href="{escape(link)}">Open FullHouse</a></p>' if link else "") + "</div>")
        try:
            delivered = mail.send(to, str(row.payload.get("title") or "FullHouse"), html, text)
        except Exception as err:
            delivered, row.last_error = False, str(err)[:500]
        row.status = "sent" if delivered else "failed"
        row.sent_at = datetime.now(timezone.utc) if delivered else None
        sent += 1 if delivered else 0
        db.commit()
    db.flush()
    return sent


def visible_to(db: Session, restaurant_id: str, user_email: str, is_manager: bool, limit: int = 30) -> list[Notification]:
    """A person's notifications, newest first: the restaurant's, less the ones about spending for staff."""
    query = db.query(Notification).filter(Notification.restaurant_id == restaurant_id,
                                          (Notification.user_email.is_(None)) | (Notification.user_email == user_email))
    if not is_manager:
        query = query.filter(Notification.kind.notin_(MANAGER_KINDS))
    return query.order_by(Notification.created_at.desc()).limit(limit).all()


def _month(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def used(db: Session, restaurant_id: str, meter_name: str, now: datetime | None = None) -> float:
    row = db.query(UsageMeter).filter(UsageMeter.restaurant_id == restaurant_id, UsageMeter.month == _month(now),
                                      UsageMeter.meter == meter_name).first()
    return row.amount if row else 0.0


def meter(db: Session, restaurant_id: str, meter_name: str, amount: float, now: datetime | None = None) -> float:
    """Add `amount` to this month's use of `meter_name`. Returns the month's total."""
    row = db.query(UsageMeter).filter(UsageMeter.restaurant_id == restaurant_id, UsageMeter.month == _month(now),
                                      UsageMeter.meter == meter_name).first()
    if row is None:
        row = UsageMeter(restaurant_id=restaurant_id, month=_month(now), meter=meter_name, amount=0.0)
        db.add(row)
    row.amount = round((row.amount or 0.0) + amount, 6)
    db.flush()
    return row.amount


def within_budget(db: Session, restaurant_id: str, meter_name: str, budget: float | None = None, now: datetime | None = None) -> bool:
    """Whether the restaurant still has room this month. `budget` overrides the plan's figure (tests pass 0)."""
    limit = MONTHLY_BUDGETS.get(meter_name, 0.0) if budget is None else budget
    return used(db, restaurant_id, meter_name, now) < limit
