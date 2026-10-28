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
from app.restaurant.inventory import lock_items, plural, stock_change
from app.restaurant.models import (
    CANCELLED,
    DRAFT,
    OPEN_ORDER_STATUSES,
    ORDERED,
    RECEIVED,
    SHIPPED,
    InventoryItem,
    PurchaseOrder,
    PurchaseOrderLine,
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
        db.query(PurchaseOrder).filter(PurchaseOrder.restaurant_id == restaurant_id)
        .filter(PurchaseOrder.status.in_(OPEN_ORDER_STATUSES))
        .order_by(PurchaseOrder.expected_on.is_(None), PurchaseOrder.expected_on, PurchaseOrder.ordered_at)
        .all()
    )


def list_orders(db: Session, restaurant_id: str | None, status: str = "open", limit: int = 100) -> list[PurchaseOrder]:
    query = db.query(PurchaseOrder).filter(PurchaseOrder.restaurant_id == restaurant_id)
    if status == "open":
        return open_orders(db, restaurant_id)
    if status != "all":
        query = query.filter(PurchaseOrder.status == status)
    return query.order_by(PurchaseOrder.updated_at.desc()).limit(limit).all()


def order_in(db: Session, restaurant_id: str | None, order_id: str) -> PurchaseOrder | None:
    return db.query(PurchaseOrder).filter(PurchaseOrder.restaurant_id == restaurant_id).filter(PurchaseOrder.id == order_id).first()


def place(
    db: Session, item: InventoryItem, quantity: float, *, supplier: Supplier | None = None, packs: float | None = None,
    pack_unit: str | None = None, estimated_cost: float | None = None, expected_on: date | None = None,
    note: str | None = None, action_id: str | None = None, placed_by: str | None = None, today: date | None = None,
    supplier_name: str | None = None, status: str = ORDERED,
) -> PurchaseOrder:
    """Record an order for one stock item. `status` ORDERED when it was placed already (by phone), DRAFT when it
    waits to be sent from here. Expected when the supplier's lead time says, unless told."""
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
        status=status, expected_on=expected_on, note=(note or "")[:255] or None, action_id=action_id,
        placed_by=placed_by or get_actor().id, sent_at=_now() if status == ORDERED else None,
        sent_by=(placed_by or get_actor().id) if status == ORDERED else None,
    )
    db.add(order)
    db.flush()
    return order


def draft_with_lines(
    db: Session, restaurant_id: str, supplier: Supplier, lines: list[dict], *, note: str | None = None, action_id: str | None = None,
    expected_on: date | None = None, today: date | None = None,
) -> PurchaseOrder:
    """A drafted order of several lines to one supplier, from an approved proposal. Each line:
    {"item_id", "inventory_item_id", "description", "qty" (packs), "unit" (pack name), "units", "unit_price_expected"}."""
    if not lines:
        raise OrderError("An order needs at least one line.")
    today = today or date.today()
    total = sum(float(l.get("qty") or 0) * float(l.get("unit_price_expected") or 0) for l in lines)
    order = PurchaseOrder(
        restaurant_id=restaurant_id, supplier_id=supplier.id, supplier_name=supplier.name, inventory_item_id=None,
        item_name=f"{len(lines)} item{'s' if len(lines) != 1 else ''}", quantity=float(sum(float(l.get("qty") or 0) for l in lines)),
        unit="lines", estimated_cost=round(total, 2) if total else None, status=DRAFT,
        expected_on=expected_on or today + timedelta(days=supplier.lead_time_days or DEFAULT_LEAD_DAYS), note=(note or "")[:255] or None,
        action_id=action_id, placed_by=get_actor().id,
    )
    db.add(order)
    db.flush()
    for position, line in enumerate(lines):
        qty = float(line.get("qty") or 0)
        if qty <= 0:
            continue
        db.add(PurchaseOrderLine(
            restaurant_id=restaurant_id, po_id=order.id, position=position, item_id=line.get("item_id"),
            inventory_item_id=line.get("inventory_item_id"), description=str(line.get("description") or "")[:200] or "Item",
            qty=qty, unit=str(line.get("unit") or "case")[:32], units=line.get("units"),
            unit_price_expected=float(line["unit_price_expected"]) if line.get("unit_price_expected") is not None else None,
        ))
    db.flush()
    return order


def mark_sent(db: Session, order: PurchaseOrder, sent_by: str | None = None) -> PurchaseOrder:
    """A draft becomes a placed order: sent from here by mail, or placed by phone and marked."""
    if order.status != DRAFT:
        raise OrderError(f"This order is already {order.status}.")
    order.status = ORDERED
    order.sent_at = _now()
    order.sent_by = sent_by or get_actor().id
    order.ordered_at = _now()
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
    """"2 cases (48 head) of Romaine Lettuce from Sysco Foods", or "3 items from Sysco Foods" for an order with lines."""
    where = f" from {order.supplier_name}" if order.supplier_name else ""
    if order.inventory_item_id is None:
        return f"{order.item_name}{where}"
    amount = f"{order.quantity:g} {order.unit}"
    if order.packs and order.pack_unit:
        amount = f"{order.packs:g} {plural(order.pack_unit, order.packs)} ({amount})"
    return f"{amount} of {order.item_name}{where}"


def _receive_lines(db: Session, order: PurchaseOrder, line_quantities: dict, note: str | None, attribution: Actor) -> float:
    """Each counted line into its stock item; returns the packs received over all lines."""
    packs = 0.0
    for line in order.lines:
        if line.received_qty is not None:
            packs += line.received_qty
            continue
        got = float(line_quantities.get(line.id, line.qty))
        line.received_qty = got
        packs += got
        if not line.inventory_item_id or got <= 0:
            continue
        units = (line.units or 0) / line.qty * got if line.qty else 0
        if units <= 0:
            continue
        lock_items(db, [line.inventory_item_id])
        stock = db.get(InventoryItem, line.inventory_item_id)
        db.refresh(stock)
        stock.quantity_on_hand = round(stock.quantity_on_hand + units, 6)
        said = f"Order received: {got:g} {plural(line.unit, got)} of {line.description} from {order.supplier_name or 'the supplier'}"
        if note:
            said += f". {note}"
        with actor_context(attribution):
            with stock_change("reorder", said[:255]):
                db.flush()
    return packs


def receive_line(db: Session, order: PurchaseOrder, line: PurchaseOrderLine, packs: float, units: float, note: str, received_by: str,
                 invoice_line_id: str | None = None) -> None:
    """One line of an order arrives on an invoice: `packs` as billed, `units` into stock when the item is counted
    (0 when it isn't). Closes the order once every line is in."""
    if line.received_qty is not None:
        return
    line.received_qty = round(packs, 4)
    line.invoice_line_id = invoice_line_id
    if line.inventory_item_id and units > 0:
        lock_items(db, [line.inventory_item_id])
        stock = db.get(InventoryItem, line.inventory_item_id)
        db.refresh(stock)
        stock.quantity_on_hand = round(stock.quantity_on_hand + units, 6)
        actor = get_actor()
        with actor_context(Actor(type=actor.type, id=received_by, restaurant_id=order.restaurant_id, agent_action_id=order.action_id)):
            with stock_change("reorder", f"Order received: {line.description} from {order.supplier_name or 'the supplier'}. {note}"[:255]):
                db.flush()
    if all(l.received_qty is not None for l in order.lines):
        order.status = RECEIVED
        order.received_at = _now()
        order.received_qty = round(sum(l.received_qty or 0 for l in order.lines), 4)
        order.received_by = received_by
        order.invoice_line_id = invoice_line_id
    db.flush()


def receive(
    db: Session, order: PurchaseOrder, quantity: float | None = None, note: str | None = None,
    received_by: str | None = None, invoice_line_id: str | None = None, line_quantities: dict | None = None,
) -> PurchaseOrder:
    """Book what arrived into stock, once, and close the order. Does not commit.

    An order of one stock item books `quantity` (all of it by default). An order with lines books each line
    that is counted in Inventory, at its ordered units unless `line_quantities` ({line id: units}) says otherwise.
    """
    if order.status not in OPEN_ORDER_STATUSES:
        raise OrderError(f"This order is already {order.status}.")
    actor = get_actor()
    attribution = Actor(type=actor.type, id=received_by or actor.id, restaurant_id=order.restaurant_id,
                        agent_run_id=actor.agent_run_id or _run_of(db, order.action_id),
                        agent_action_id=actor.agent_action_id or order.action_id)
    if order.inventory_item_id is None:
        amount = _receive_lines(db, order, line_quantities or {}, note, attribution)
        order.status = RECEIVED
        order.received_at = _now()
        order.received_qty = round(amount, 6)
        order.received_by = received_by or get_actor().id
        order.invoice_line_id = invoice_line_id
        db.flush()
        return order
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
    with actor_context(attribution):
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


def open_for(db: Session, restaurant_id: str | None, inventory_item_id: str, supplier_id: str | None,
             invoice_date: date | None = None) -> PurchaseOrder | None:
    """The open order of one item an invoice line is delivering: the same supplier's first, else the oldest.
    An invoice dated before the order was placed can't be its delivery (a day of slack: sent-at is UTC, invoice dates are local)."""
    candidates = [o for o in open_orders(db, restaurant_id) if o.inventory_item_id == inventory_item_id and o.status != DRAFT
                  and (invoice_date is None or (o.sent_at or o.ordered_at).date() - timedelta(days=1) <= invoice_date)]
    if not candidates:
        return None
    same = [o for o in candidates if supplier_id and o.supplier_id == supplier_id]
    pool = same or [o for o in candidates if o.supplier_id is None] or candidates
    return min(pool, key=lambda o: o.ordered_at)


def open_line_for(db: Session, restaurant_id: str | None, item_id: str | None, inventory_item_id: str | None,
                  supplier_id: str | None, invoice_date: date | None = None) -> tuple[PurchaseOrder, PurchaseOrderLine] | None:
    """The open order line (by price item, else stock item) from this supplier that an invoice line is delivering.
    An invoice dated before the order was sent can't be its delivery."""
    for order in sorted((o for o in open_orders(db, restaurant_id) if o.inventory_item_id is None and o.status != DRAFT
                         and (not supplier_id or o.supplier_id == supplier_id)
                         and (invoice_date is None or (o.sent_at or o.ordered_at).date() - timedelta(days=1) <= invoice_date)), key=lambda o: o.ordered_at):
        for line in order.lines:
            if line.received_qty is not None:
                continue
            if (item_id and line.item_id == item_id) or (inventory_item_id and line.inventory_item_id == inventory_item_id):
                return order, line
    return None


def short_delivery_cards(db: Session, restaurant_id: str | None, today: date | None = None) -> list[dict]:
    """Orders whose invoice brought less than was ordered: what to take up with the rep."""
    from app.prices import engine
    from app.prices.models import InvoiceLine

    today = today or date.today()
    cards = []
    since = today - timedelta(weeks=engine.WINDOW_WEEKS)
    orders = (db.query(PurchaseOrder).filter(PurchaseOrder.restaurant_id == restaurant_id)
              .filter(PurchaseOrder.inventory_item_id.is_(None), PurchaseOrder.sent_at.isnot(None),
                      PurchaseOrder.status.in_((ORDERED, SHIPPED, RECEIVED))).all())
    for order in orders:
        if order.sent_at and order.sent_at.date() < since:
            continue
        for line in order.lines:
            if not line.invoice_line_id or line.received_qty is None or line.received_qty >= line.qty:
                continue
            invoice_line = db.get(InvoiceLine, line.invoice_line_id)
            if invoice_line is None:
                continue
            short = line.qty - line.received_qty
            impact = round(short * float(line.unit_price_expected or invoice_line.unit_price), 2)
            if impact <= 0:
                continue
            cards.append({
                "section": "owed", "item_id": line.item_id or order.id, "item_name": line.description, "impact": impact,
                "sentence": (f"Ordered {line.qty:g} {plural(line.unit, line.qty)} of {line.description} from {order.supplier_name or 'the supplier'} "
                             f"on {engine.short_date(order.sent_at.date())}; the invoice of {engine.short_date(invoice_line.invoice.invoice_date)} "
                             f"brought {line.received_qty:g}. Short {short:g}."),
                "action": {"kind": "draft", "label": "Draft message", "draft_kind": "short_delivery", "supplier_id": order.supplier_id},
                "sources": [{"kind": "invoice", "id": invoice_line.invoice_id, "label": f"{engine.short_date(invoice_line.invoice.invoice_date)}, {order.supplier_name or 'Supplier'}"}],
            })
    return cards


def received_without_invoice(
    db: Session, restaurant_id: str | None, inventory_item_id: str, supplier_id: str | None, today: date,
) -> PurchaseOrder | None:
    """A recent hand receipt for this item that no invoice has claimed yet: the invoice is for it."""
    since = datetime.combine(today - timedelta(days=RECEIPT_MATCH_DAYS), datetime.min.time(), tzinfo=timezone.utc)
    rows = (
        db.query(PurchaseOrder).filter(PurchaseOrder.restaurant_id == restaurant_id)
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
