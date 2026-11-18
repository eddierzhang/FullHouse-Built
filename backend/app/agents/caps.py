"""One pending-proposal cap per kind per restaurant per week. Beyond it, hold back silently and log it."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.agents.models import AgentAction
from app.prices.engine import week_start

logger = logging.getLogger(__name__)

#: Proposals of a kind made in one week for one restaurant. Mappings (the agent's questions) have
#: their own cap in app.prices.engine.
CAPS = {"recipe": 5, "purchase_order": 3, "calendar_item": 10, "agreed_price": 10, "menu_reprice": 5, "prep_sheet": 2}


def made_this_week(db: Session, restaurant_id: str | None, kind: str, today: date | None = None) -> int:
    monday = week_start(today or date.today())
    since = datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)
    return (
        db.query(AgentAction)
        .filter(AgentAction.restaurant_id == restaurant_id, AgentAction.action_type == kind, AgentAction.created_at >= since)
        .count()
    )


def room(db: Session, restaurant_id: str | None, kind: str, today: date | None = None) -> int:
    cap = CAPS.get(kind)
    if cap is None:
        return 10**6
    return max(0, cap - made_this_week(db, restaurant_id, kind, today))


def within(db: Session, restaurant_id: str | None, kind: str, today: date | None = None) -> bool:
    """True when another proposal of this kind may be made this week; logs the hold-back otherwise."""
    if room(db, restaurant_id, kind, today) > 0:
        return True
    from app.platform import events

    logger.info("cap reached: %s for %s this week; holding", kind, restaurant_id)
    events.emit(db, restaurant_id, "proposal.held", "agent_actions", None, {"kind": kind, "cap": CAPS.get(kind)})
    return False
