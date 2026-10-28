"""Purchase orders: placed, shipped, received, cancelled.

Placing an order changes nothing in the stock figures. Receiving it does:
the received quantity is booked as one stock change with the order in the
note, by whoever received it. An order can be received by hand on the
Inventory page, or by its invoice arriving (app.prices.stock finds the open
order for that item and supplier and receives it with what the invoice
says was delivered), and a hand receipt followed by the invoice counts
once, not twice.

Nothing here sends anything to a supplier. The order is a record of what
the restaurant ordered, however it placed it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.audit.context import Actor, actor_context, get_actor
from app.restaurant.inventory import in_scope, lock_items, plural, stock_change
from app.restaurant.models import (
    CANCELLED,
    OPEN_ORDER_STATUSES,
    ORDERED,
    RECEIVED,
    SHIPPED,
    InventoryItem,
    PurchaseOrder,
    Supplier,
)

#: When no supplier (and so no lead time) is named.
DEFAULT_LEAD_DAYS = 3
#: An invoice arriving within this long of a hand receipt for the same item is that receipt's invoice.
RECEIPT_MATCH_DAYS = 14


class OrderError(ValueError):
    """The order can't take this step. The message is for the person."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def open_orders(db: Session, restaurant_id: str | None) -> list[PurchaseOrder]:
    return (
        in_scope(db.query(PurchaseOrder), restaurant_id)
        .filter(PurchaseOrder.status.in_(OPEN_ORDER_STATUSES))
        .order_by(PurchaseOrder.expected_on.is_(None), PurchaseOrder.expected_on, PurchaseOrder.ordered_at)
        .all()
    )


def list_orders(db: Session, restaurant_id: str | None, status: str = "open", limit: int = 100) -> list[PurchaseOrder]:
    query = in_scope(db.query(PurchaseOrder), restaurant_id)
    if status == "open":
        return open_orders(db, restaurant_id)
    if status != "all":
        query = query.filter(PurchaseOrder.status == status)
    return query.order_by(PurchaseOrder.updated_at.desc()).limit(limit).all()


def order_in(db: Session, restaurant_id: str | None, order_id: str) -> PurchaseOrder | None:
    return in_scope(db.query(PurchaseOrder), restaurant_id).filter(PurchaseOrder.id == order_id).first()


def place(
    db: Session, item: InventoryItem, quantity: float, *, supplier: Supplier | None = None, packs: float | None = None,
    pack_unit: str | None = None, estimated_cost: float | None = None, expected_on: date | None = None,
    note: str | None = None, action_id: str | None = None, placed_by: str | None = None, today: date | None = None,
    supplier_name: str | None = None,
) -> PurchaseOrder:
    """Record an order placed with a supplier. Expected when the supplier's lead time says, unless told."""
    if quantity <= 0:
        raise OrderError("Order a positive amount.")
    today = today or date.today()
    if expected_on is None:
        lead = supplier.lead_time_days if supplier is not None else DEFAULT_LEAD_DAYS
        expected_on = today + timedelta(days=lead)
    order = PurchaseOrder(
        restaurant_id=item.restaurant_id, supplier_id=supplier.id if supplier else None,
        supplier_name=supplier.name if supplier else (supplier_name or None), inventory_item_id=item.id, item_name=item.name,
        quantity=round(quantity, 6), unit=item.unit, packs=packs, pack_unit=pack_unit, estimated_cost=estimated_cost,
        status=ORDERED, expected_on=expected_on, note=(note or "")[:255] or None, action_id=action_id,
        placed_by=placed_by or get_actor().id,
    )
    db.add(order)
    db.flush()
    return order


def mark_shipped(db: Session, order: PurchaseOrder, expected_on: date | None = None) -> PurchaseOrder:
    if order.status not in OPEN_ORDER_STATUSES:
        raise OrderError(f"This order is already {order.status}.")
    order.status = SHIPPED
    order.shipped_at = _now()
    if expected_on is not None:
        order.expected_on = expected_on
    db.flush()
    return order


def describe(order: PurchaseOrder) -> str:
    """"2 cases (48 head) of Romaine Lettuce from Sysco Foods"."""
    amount = f"{order.quantity:g} {order.unit}"
    if order.packs and order.pack_unit:
        amount = f"{order.packs:g} {plural(order.pack_unit, order.packs)} ({amount})"
    where = f" from {order.supplier_name}" if order.supplier_name else ""
    return f"{amount} of {order.item_name}{where}"


def receive(
    db: Session, order: PurchaseOrder, quantity: float | None = None, note: str | None = None,
    received_by: str | None = None, invoice_line_id: str | None = None,
) -> PurchaseOrder:
    """Book what arrived into stock, once, and close the order. Does not commit."""
    if order.status not in OPEN_ORDER_STATUSES:
        raise OrderError(f"This order is already {order.status}.")
    amount = order.quantity if quantity is None else quantity
    if amount < 0:
        raise OrderError("Received amount can't be negative.")
    lock_items(db, [order.inventory_item_id])
    item = db.get(InventoryItem, order.inventory_item_id)
    db.refresh(item)
    if amount:
        item.quantity_on_hand = round(item.quantity_on_hand + amount, 6)
    said = f"Order received: {describe(order)}"
    if amount != order.quantity:
        said += f"; {amount:g} {order.unit} arrived"
    if note:
        said += f". {note}"
    # Booked by whoever received it, but still traceable to the proposal that placed the order.
    actor = get_actor()
    with actor_context(Actor(type=actor.type, id=received_by or actor.id, restaurant_id=order.restaurant_id,
                             agent_run_id=actor.agent_run_id or _run_of(db, order.action_id),
                             agent_action_id=actor.agent_action_id or order.action_id)):
        with stock_change("reorder", said[:255]):
            db.flush()
    order.status = RECEIVED
    order.received_at = _now()
    order.received_qty = round(amount, 6)
    order.received_by = received_by or get_actor().id
    order.invoice_line_id = invoice_line_id
    db.flush()
    return order


def _run_of(db: Session, action_id: str | None) -> str | None:
    if not action_id:
        return None
    from app.agents.models import AgentAction

    action = db.get(AgentAction, action_id)
    return action.run_id if action else None


def cancel(db: Session, order: PurchaseOrder, note: str | None = None) -> PurchaseOrder:
    if order.status not in OPEN_ORDER_STATUSES:
        raise OrderError(f"This order is already {order.status}.")
    order.status = CANCELLED
    if note:
        order.note = f"{order.note}. {note}"[:255] if order.note else note[:255]
    db.flush()
    return order


# --- for the invoices ---------------------------------------------------------------------


def open_for(db: Session, restaurant_id: str | None, inventory_item_id: str, supplier_id: str | None) -> PurchaseOrder | None:
    """The open order an invoice line for this item is delivering: the same supplier's first, else the oldest."""
    candidates = [o for o in open_orders(db, restaurant_id) if o.inventory_item_id == inventory_item_id]
    if not candidates:
        return None
    same = [o for o in candidates if supplier_id and o.supplier_id == supplier_id]
    pool = same or [o for o in candidates if o.supplier_id is None] or candidates
    return min(pool, key=lambda o: o.ordered_at)


def received_without_invoice(
    db: Session, restaurant_id: str | None, inventory_item_id: str, supplier_id: str | None, today: date,
) -> PurchaseOrder | None:
    """A recent hand receipt for this item that no invoice has claimed yet: the invoice is for it."""
    since = datetime.combine(today - timedelta(days=RECEIPT_MATCH_DAYS), datetime.min.time(), tzinfo=timezone.utc)
    rows = (
        in_scope(db.query(PurchaseOrder), restaurant_id)
        .filter(PurchaseOrder.inventory_item_id == inventory_item_id, PurchaseOrder.status == RECEIVED,
                PurchaseOrder.invoice_line_id.is_(None), PurchaseOrder.received_at >= since)
        .order_by(PurchaseOrder.received_at)
        .all()
    )
    same = [o for o in rows if supplier_id and o.supplier_id == supplier_id]
    pool = same or [o for o in rows if o.supplier_id is None]
    return pool[0] if pool else None


def is_overdue(order: PurchaseOrder, today: date | None = None) -> bool:
    today = today or date.today()
    return order.status in OPEN_ORDER_STATUSES and order.expected_on is not None and order.expected_on < today
