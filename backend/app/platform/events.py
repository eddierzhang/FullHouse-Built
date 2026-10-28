"""The event log and its subscribers.

    events.emit(db, restaurant_id, "invoice.ingested", "supplier_invoices", invoice.id, {...})

Each `emit` appends one row and calls every subscriber for that type in the same transaction.
Subscribers enqueue jobs (app.platform.jobs); they never do the work themselves, so a slow
stage can't hold up the request that caused it, and a stage can be added without editing
the one before it.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.platform.models import Event

logger = logging.getLogger(__name__)

Subscriber = Callable[[Session, Event], None]
_subscribers: dict[str, list[Subscriber]] = defaultdict(list)


def subscribe(event_type: str) -> Callable[[Subscriber], Subscriber]:
    def register(fn: Subscriber) -> Subscriber:
        if fn not in _subscribers[event_type]:
            _subscribers[event_type].append(fn)
        return fn

    return register


def subscribers(event_type: str) -> list[Subscriber]:
    return list(_subscribers.get(event_type, ()))


def emit(db: Session, restaurant_id: str | None, event_type: str, aggregate_table: str | None = None,
         aggregate_id: str | None = None, payload: dict | None = None) -> Event:
    event = Event(restaurant_id=restaurant_id, type=event_type, aggregate_table=aggregate_table,
                  aggregate_id=str(aggregate_id) if aggregate_id is not None else None, payload=payload or {})
    db.add(event)
    db.flush()
    for fn in subscribers(event_type):
        try:
            fn(db, event)
        except Exception:  # a broken subscriber must not undo the write that caused the event
            logger.exception("event subscriber %s failed on %s", getattr(fn, "__name__", fn), event_type)
    return event
