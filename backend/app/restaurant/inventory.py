"""Whose stock is whose, and a record of every change to it.

Scope. Every item belongs to one restaurant, and people only ever see or
change their own restaurant's. Sample data loaded before anyone set a
restaurant up (seed_data.py on a fresh site) has no restaurant yet; it is
visible only to accounts that have none either -- the owner mid-setup --
and the restaurant claims it the moment setup creates it.

History. A listener writes an `InventoryAdjustment` for every change to
`quantity_on_hand`, whichever code path made it: the Inventory page, an
order drawing ingredients down, an approved agent reorder, a revert. The
code making the change can say why with `stock_change(reason, note)`;
who did it comes from the same actor context the change log uses.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import event, inspect, update
from sqlalchemy.orm import Query, Session, configure_mappers

from app.audit.context import get_actor
from app.auth.models import User
from app.restaurant.models import InventoryAdjustment, InventoryItem, Restaurant

#: Why stock changed. The first four are what staff record by hand.
REASONS = {
    "delivery": "Delivery received",
    "used": "Used",
    "waste": "Wasted",
    "count": "Stock count",
    "initial": "Starting stock",
    "sale": "Sold",
    "reorder": "Reorder approved",
    "revert": "Undone",
    "correction": "Correction",
}
STAFF_REASONS = ("delivery", "used", "waste", "count")

#: Units an item can be counted in. Lower-case lookups map to these spellings.
UNITS = (
    "each", "dozen", "case", "box", "pack", "bag", "bottle", "can", "jar", "tray",
    "bunch", "head", "loaf",
    "g", "kg", "oz", "lb",
    "ml", "L", "fl oz", "qt", "gal",
)
_UNITS_BY_KEY = {unit.lower(): unit for unit in UNITS}


#: Units spelled the same however many there are.
_NO_PLURAL = {"g", "kg", "oz", "lb", "ml", "L", "fl oz", "qt", "gal", "each"}


def plural(unit: str | None, count: float) -> str:
    """ "case" -> "cases" when there's more than one; measurements stay as they are."""
    if not unit or count == 1 or unit in _NO_PLURAL:
        return unit or ""
    if unit.endswith(("x", "ch", "sh", "s")):
        return unit + "es"
    if unit == "loaf":
        return "loaves"
    return unit + "s"


def normalize_unit(value: str) -> str:
    """The canonical spelling of `value`, e.g. "LB" -> "lb". Raises ValueError for anything else."""
    unit = _UNITS_BY_KEY.get(value.strip().lower())
    if unit is None:
        raise ValueError(f"{value.strip()!r} isn't a unit we track. Use one of: {', '.join(UNITS)}.")
    return unit


# --- scope --------------------------------------------------------------------------


def scope_for(db: Session, user: User) -> str | None:
    """The restaurant whose stock `user` works with; None before one exists."""
    from app.restaurant.profile import restaurant_for

    restaurant = restaurant_for(db, user)
    return restaurant.id if restaurant else None


def site_scope(db: Session) -> str | None:
    """The restaurant the current work is for, when no signed-in person names one.

    An agent run, or a proposal being approved, acts for its own restaurant
    (the actor carries it); anything else -- a scheduled job, a script --
    works for the site's restaurant.
    """
    from app.audit.context import get_actor
    from app.restaurant.profile import get_restaurant

    if acting_for := get_actor().restaurant_id:
        return acting_for
    restaurant = get_restaurant(db)
    return restaurant.id if restaurant else None


def in_scope(query: Query, restaurant_id: str | None) -> Query:
    # `== None` is SQLAlchemy's IS NULL: no restaurant sees only unclaimed stock.
    return query.filter(InventoryItem.restaurant_id == restaurant_id)


def items_in(db: Session, restaurant_id: str | None) -> list[InventoryItem]:
    return in_scope(db.query(InventoryItem), restaurant_id).order_by(InventoryItem.name).all()


def item_in(db: Session, restaurant_id: str | None, item_id: str) -> InventoryItem | None:
    """One item, only if it's this restaurant's -- another's reads as missing."""
    return in_scope(db.query(InventoryItem), restaurant_id).filter(InventoryItem.id == item_id).first()


def lock_items(db: Session, item_ids) -> None:
    """Hold these items' stock until this transaction ends, on any database.

    `SELECT ... FOR UPDATE` locks the rows on Postgres, but SQLite ignores
    it: two sales reading the same stock both see 10, both write 9, and a
    sale vanishes. A no-op UPDATE takes a write lock on both -- row locks
    on Postgres, the database write lock on SQLite -- so whoever comes next
    waits, then reads what the first one left. Call it before reading stock
    you're about to change.
    """
    ids = sorted(set(item_ids))
    if ids:
        db.execute(
            update(InventoryItem)
            .where(InventoryItem.id.in_(ids))
            .values(quantity_on_hand=InventoryItem.quantity_on_hand)
            .execution_options(synchronize_session=False)
        )


def claim_unassigned(db: Session, restaurant: Restaurant) -> None:
    """Give stock that belongs to no restaurant, and its history, to `restaurant`.

    Run when setup creates the restaurant, so sample data seeded before
    then becomes its stock rather than disappearing from view.
    """
    for model in (InventoryItem, InventoryAdjustment):
        db.execute(update(model).where(model.restaurant_id.is_(None)).values(restaurant_id=restaurant.id))


# --- history -----------------------------------------------------------------------

_reason: ContextVar[tuple[str, str | None, str | None] | None] = ContextVar("stock_change_reason", default=None)


@contextmanager
def stock_change(reason: str, note: str | None = None, request_id: str | None = None) -> Iterator[None]:
    """Record every stock change made in this block with this reason and note.

    `request_id` is the client's Idempotency-Key, for a change to one item
    that must not be recorded twice (see models.InventoryAdjustment).
    """
    if reason not in REASONS:
        raise ValueError(f"Unknown stock change reason {reason!r}")
    token = _reason.set((reason, note, request_id))
    try:
        yield
    finally:
        _reason.reset(token)


def _adjustment(item: InventoryItem, change: float, default_reason: str) -> InventoryAdjustment:
    reason, note, request_id = _reason.get() or (default_reason, None, None)
    actor = get_actor()
    return InventoryAdjustment(
        restaurant_id=item.restaurant_id,
        item=item,
        change=round(change, 6),
        quantity_after=item.quantity_on_hand,
        reason=reason,
        # Notes can come from a revert or a supplier's name: never fail a stock change over length.
        note=note[:255] if note else None,
        actor_type=actor.type,
        actor_id=actor.id,
        request_id=request_id,
    )


def record_adjustments(session: Session, flush_context, instances) -> None:
    """Before each flush: one adjustment per stock level set or changed."""
    added = []
    for obj in list(session.new):
        if isinstance(obj, InventoryItem) and obj.quantity_on_hand:
            if obj.id is None:
                obj.id = uuid.uuid4().hex  # the adjustment needs it before the flush assigns one
            added.append(_adjustment(obj, obj.quantity_on_hand, "initial"))
    for obj in list(session.dirty):
        if not isinstance(obj, InventoryItem):
            continue
        history = inspect(obj).attrs.quantity_on_hand.load_history()
        if not history.has_changes() or not history.deleted:
            continue
        before, after = history.deleted[0], obj.quantity_on_hand
        if before is not None and after != before:
            added.append(_adjustment(obj, after - before, "correction"))
    session.add_all(added)


_installed = False


def install() -> None:
    """Register the listener. Idempotent; called from app.bootstrap.setup()."""
    global _installed
    if not _installed:
        event.listen(Session, "before_flush", record_adjustments)
        # The listener diffs old and new stock, so the old value must be
        # fetched even when the attribute was expired by a commit.
        configure_mappers()
        InventoryItem.quantity_on_hand.impl.active_history = True
        _installed = True
