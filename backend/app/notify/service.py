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
    db.flush()
    return row


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
