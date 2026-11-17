"""Checking deliveries in, and reading what suppliers say back.

    expected       What is due: every order that has been placed and not fully received, with its lines.
    post_receipt   What actually arrived, line by line. Stock is booked through the ledger once per receipt line,
                   the order closes or stays part-received, and anything short becomes a drafted credit claim.
    handle_reply   A supplier's answer to an order (by its own reply address, or pasted in): confirmed, shipped,
                   or a change a person has to look at.
    late           Orders past their date with nothing received, each with a chase message ready.

Once any of a line has been checked in here, the invoice for it is matched to that receipt rather than booked
again (app.prices.stock asks `received_line_without_invoice`), so a delivery is never counted twice.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.notify import service as notify
from app.prices.models import DRAFT as MESSAGE_DRAFT
from app.prices.models import OutreachDraft, SupplierRep
from app.receiving.models import Receipt, ReceiptLine
from app.restaurant import orders
from app.restaurant.models import (
    CONFIRMED,
    DRAFT,
    OPEN_ORDER_STATUSES,
    ORDERED,
    PARTIAL,
    RECEIVED,
    SHIPPED,
    InventoryItem,
    PurchaseOrder,
    PurchaseOrderLine,
    Restaurant,
)
from app.stock import ledger

#: What a person can say about a line. "partial" means some came and the rest is still expected.
STATUSES = ("ok", "short", "over", "substituted", "damaged", "missing", "partial")
#: Statuses that mean the supplier owes something for the part that did not arrive usable.
OWED = ("short", "damaged", "missing")
EPSILON = 1e-6


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- what is due --------------------------------------------------------------------------------


def _line_views(order: PurchaseOrder) -> list[dict]:
    """An order as lines to check in. An order of one stock item is one line, in the item's own unit."""
    if order.inventory_item_id is not None:
        already = order.received_qty or 0.0
        return [{"key": "order", "description": order.item_name, "expected": round(order.quantity - already, 4), "unit": order.unit,
                 "ordered": order.quantity, "received_so_far": already, "inventory_item_id": order.inventory_item_id,
                 "packs": order.packs, "pack_unit": order.pack_unit}]
    out = []
    for line in order.lines:
        if line.received_qty is not None and line.receipt_status != "partial":
            continue
        already = line.received_qty or 0.0
        out.append({"key": line.id, "description": line.description, "expected": round(line.qty - already, 4), "unit": line.unit,
                    "ordered": line.qty, "received_so_far": already, "inventory_item_id": line.inventory_item_id, "packs": None, "pack_unit": None})
    return out


def chase_message(order: PurchaseOrder, restaurant_name: str | None) -> dict:
    """A short message asking where a late order is, for the person to send from their own mail."""
    due = order.expected_on.strftime("%A %b %-d") if order.expected_on else "this week"
    placed = (order.sent_at or order.ordered_at).strftime("%b %-d")
    return {"subject": f"Order of {placed}: where is it?",
            "body": f"Hello,\n\nOur order of {placed} ({order.item_name}) was due {due} and hasn't arrived. "
                    f"Can you tell me when it will be here, and whether anything on it is short?\n\nThanks,\n{restaurant_name or ''}".strip()}


def expected(db: Session, restaurant_id: str, today: date | None = None) -> list[dict]:
    """Orders placed and not fully in, soonest first, each with the lines still to check in."""
    today = today or date.today()
    restaurant = db.get(Restaurant, restaurant_id)
    rows = (db.query(PurchaseOrder).filter(PurchaseOrder.restaurant_id == restaurant_id, PurchaseOrder.status.in_(OPEN_ORDER_STATUSES),
                                          PurchaseOrder.status != DRAFT).all())
    out = []
    for order in sorted(rows, key=lambda o: (o.expected_on or today, o.ordered_at)):
        late = bool(order.expected_on and order.expected_on < today)
        out.append({
            "id": order.id, "supplier_id": order.supplier_id, "supplier_name": order.supplier_name or "A supplier", "status": order.status,
            "summary": order.item_name, "expected_on": order.expected_on, "ordered_at": order.sent_at or order.ordered_at,
            "confirmed_at": order.confirmed_at, "shipped_at": order.shipped_at, "autopilot": bool(order.autopilot),
            "late": late, "days_late": (today - order.expected_on).days if late else 0, "estimated_cost": order.estimated_cost,
            "note": order.note, "lines": _line_views(order), "chase": chase_message(order, restaurant.name if restaurant else None) if late else None,
            "rep_email": _rep_email(db, order),
        })
    return out


def _rep_email(db: Session, order: PurchaseOrder) -> str | None:
    if not order.supplier_id:
        return None
    from app.ordering.models import SupplierCalendar

    calendar = db.query(SupplierCalendar).filter(SupplierCalendar.restaurant_id == order.restaurant_id, SupplierCalendar.supplier_id == order.supplier_id).first()
    if calendar and calendar.order_email:
        return calendar.order_email
    rep = db.query(SupplierRep).filter(SupplierRep.restaurant_id == order.restaurant_id, SupplierRep.supplier_id == order.supplier_id).first()
    return rep.email if rep and rep.email else None


# --- checking one in ----------------------------------------------------------------------------


class ReceiptError(ValueError):
    pass


def _status_for(expected_qty: float, received: float, said: str | None) -> str:
    if said:
        if said not in STATUSES:
            raise ReceiptError(f"Unknown line status {said!r}.")
        return said
    if received > expected_qty + EPSILON:
        return "over"
    if received < expected_qty - EPSILON:
        return "missing" if received <= EPSILON else "short"
    return "ok"


def post_receipt(db: Session, order: PurchaseOrder, entries: list[dict], *, client_key: str, received_by: str | None,
                 note: str | None = None, photo_document_id: str | None = None) -> tuple[Receipt, dict]:
    """Record what arrived against an order and book it into stock. Each entry: {"key": a line id or "order",
    "received": amount in the line's unit, "status"?: one of STATUSES, "note"?}. The same `client_key` posts once."""
    restaurant_id = order.restaurant_id
    existing = db.query(Receipt).filter(Receipt.restaurant_id == restaurant_id, Receipt.client_key == client_key).first()
    if existing is not None:
        return existing, summary_of(db, existing)
    if order.status not in OPEN_ORDER_STATUSES or order.status == DRAFT:
        raise ReceiptError(f"This order is {order.status}; there is nothing to check in.")
    views = {v["key"]: v for v in _line_views(order)}
    lines = {l.id: l for l in order.lines}
    receipt = Receipt(restaurant_id=restaurant_id, po_id=order.id, supplier_id=order.supplier_id, supplier_name=order.supplier_name,
                      client_key=client_key, photo_document_id=photo_document_id, received_by=received_by, note=(note or "")[:255] or None)
    db.add(receipt)
    db.flush()
    seen = set()
    for entry in entries:
        key = str(entry.get("key"))
        view = views.get(key)
        if view is None or key in seen:
            continue  # a line already closed, another order's, or sent twice in one request
        seen.add(key)
        received = round(float(entry.get("received") or 0), 4)
        if received < 0:
            raise ReceiptError("A received amount can't be negative.")
        status = _status_for(view["expected"], received, entry.get("status"))
        db.add(ReceiptLine(restaurant_id=restaurant_id, receipt_id=receipt.id, po_line_id=None if key == "order" else key,
                           inventory_item_id=view["inventory_item_id"], description=view["description"], expected_qty=view["expected"],
                           received_qty=received, unit=view["unit"], status=status, note=(entry.get("note") or "")[:255] or None))
        # Into stock, in the stock item's unit: an order of one item is already in it; a line converts from packs.
        line = lines.get(key)
        units = received if line is None else ((line.units or 0) / line.qty * received if line.qty else 0.0)
        if view["inventory_item_id"] and units > 0:
            item = db.query(InventoryItem).filter(InventoryItem.restaurant_id == restaurant_id, InventoryItem.id == view["inventory_item_id"]).first()
            if item is not None:
                ledger.move(db, item, units, "reorder", source_key=f"receipt:{receipt.id}:{key}",
                            note=f"Checked in: {received:g} {view['unit']} of {view['description']} from {order.supplier_name or 'the supplier'}")
        so_far = round(view["received_so_far"] + received, 4)
        if line is None:
            order.received_qty = so_far
            order_open = status == "partial"
        else:
            line.received_qty = so_far
            line.receipt_status = status
    if order.inventory_item_id is None:
        order_open = any(l.received_qty is None or l.receipt_status == "partial" for l in order.lines)
        order.received_qty = round(sum(l.received_qty or 0 for l in order.lines), 4)
    elif "order" not in seen:
        order_open = True
    order.status = PARTIAL if order_open else RECEIVED
    order.received_by = received_by
    if not order_open:
        order.received_at = _now()
    db.flush()
    claim = _credit_claim(db, order, receipt)
    return receipt, {**summary_of(db, receipt), "claim_draft_id": claim.id if claim else None}


def summary_of(db: Session, receipt: Receipt) -> dict:
    lines = db.query(ReceiptLine).filter(ReceiptLine.restaurant_id == receipt.restaurant_id, ReceiptLine.receipt_id == receipt.id).all()
    order = db.query(PurchaseOrder).filter(PurchaseOrder.restaurant_id == receipt.restaurant_id, PurchaseOrder.id == receipt.po_id).first()
    return {"lines": len(lines), "ok": sum(1 for l in lines if l.status == "ok"), "owed": sum(1 for l in lines if l.status in OWED),
            "still_coming": sum(1 for l in lines if l.status == "partial"), "order_status": order.status if order else None}


def _credit_claim(db: Session, order: PurchaseOrder, receipt: Receipt) -> OutreachDraft | None:
    """Anything short, damaged or missing: a message to the rep, drafted, for a person to send. Also a notice."""
    owed = [l for l in db.query(ReceiptLine).filter(ReceiptLine.restaurant_id == receipt.restaurant_id, ReceiptLine.receipt_id == receipt.id)
            if l.status in OWED]
    if not owed:
        return None
    words = {"short": "short", "damaged": "arrived damaged", "missing": "did not arrive"}
    said = "; ".join(f"{l.description}: ordered {l.expected_qty:g} {l.unit}, {l.received_qty:g} usable ({words[l.status]})" for l in owed)
    supplier = order.supplier_name or "the supplier"
    notify.tell(db, order.restaurant_id, kind="delivery_short", title=f"{supplier} delivery came in short",
                body=said, link="#receive", dedupe_key=f"receipt:short:{receipt.id}")
    # The message needs an item the invoices know (the draft pages are built around one) and a supplier.
    price_line = next((db.get(PurchaseOrderLine, l.po_line_id) for l in owed if l.po_line_id), None)
    item_id = price_line.item_id if price_line is not None else None
    if item_id is None and order.inventory_item_id is not None:
        from app.prices.models import PriceItem

        linked = db.query(PriceItem).filter(PriceItem.restaurant_id == order.restaurant_id, PriceItem.inventory_item_id == order.inventory_item_id).first()
        item_id = linked.id if linked else None
    if item_id is None or not order.supplier_id:
        return None
    rep = db.query(SupplierRep).filter(SupplierRep.restaurant_id == order.restaurant_id, SupplierRep.supplier_id == order.supplier_id).first()
    day = receipt.received_at.strftime("%b %-d")
    draft = OutreachDraft(
        restaurant_id=order.restaurant_id, supplier_id=order.supplier_id, item_id=item_id, kind="short_delivery",
        to_name=rep.name if rep else None, to_email=(rep.email if rep and rep.email else _rep_email(db, order)),
        subject=f"Delivery of {day}: short on {len(owed)} line{'s' if len(owed) != 1 else ''}"[:200],
        body=(f"{'Hi ' + rep.name.split()[0] + ',' if rep and rep.name else 'Hello,'}\n\nYour delivery on {day} did not match our order. {said}. "
              f"Please credit what did not arrive, or tell me when the rest is coming.\n\nThanks"),
        line_ids=[], status=MESSAGE_DRAFT)
    db.add(draft)
    db.flush()
    return draft


def received_line_without_invoice(db: Session, restaurant_id: str | None, item_id: str | None, inventory_item_id: str | None,
                                  supplier_id: str | None, today: date, days: int = 10) -> PurchaseOrderLine | None:
    """An order line already checked in here that no invoice has claimed: the invoice arriving now is for it."""
    since = _now() - timedelta(days=days)
    rows = (db.query(PurchaseOrderLine).join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
            .filter(PurchaseOrderLine.restaurant_id == restaurant_id, PurchaseOrderLine.received_qty.isnot(None),
                    PurchaseOrderLine.receipt_status.isnot(None), PurchaseOrderLine.invoice_line_id.is_(None),
                    PurchaseOrder.updated_at >= since)
            .order_by(PurchaseOrder.updated_at).all())
    for line in rows:
        if supplier_id and line.order.supplier_id and line.order.supplier_id != supplier_id:
            continue
        if (item_id and line.item_id == item_id) or (inventory_item_id and line.inventory_item_id == inventory_item_id):
            return line
    return None


# --- what the supplier said ---------------------------------------------------------------------

_CHANGE = re.compile(r"\b(substitut\w*|out of stock|unavailable|not available|back[- ]?order\w*|short(ed|age)?\b|can(no|')t fill|unable to fill|"
                     r"instead of|replac\w+|discontinued|price (has )?(changed|increase\w*)|delay\w*|won'?t (make|arrive)|running late)", re.I)
_SHIPPED = re.compile(r"\b(shipped|on (its|the|our) way|out for delivery|dispatched|on the truck|loaded|tracking|left the warehouse|en route)\b", re.I)
_CONFIRM = re.compile(r"\b(confirm\w*|received your order|got (it|your order)|order (is )?(accepted|placed|in)|will (be )?deliver\w*|all set|"
                      r"thank(s| you) for your order|we('ll| will) have (it|that))\b", re.I)


def classify(text: str) -> str:
    """What a supplier's reply means, by rule: change beats shipped beats confirmed. Anything else is for a person."""
    body = _own_words(text)
    if _CHANGE.search(body):
        return "change"
    if _SHIPPED.search(body):
        return "shipped"
    if _CONFIRM.search(body):
        return "confirmed"
    return "other"


def _own_words(text: str) -> str:
    """The reply without the message it quotes: our own order would otherwise be read as the supplier's words."""
    kept = []
    for line in (text or "").splitlines():
        if line.lstrip().startswith(">") or re.match(r"^\s*On .+ wrote:\s*$", line) or re.match(r"^-{2,}\s*Original Message", line, re.I):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def handle_reply(db: Session, order: PurchaseOrder, text: str, sender: str | None = None) -> str:
    """Apply a supplier's reply to its order. Confirmed and shipped update the order; a change, or anything the
    rules can't read, leaves the order alone and tells a person, with the supplier's words."""
    kind = classify(text)
    excerpt = " ".join(_own_words(text).split())[:220]
    name = order.supplier_name or "The supplier"
    if order.status in (RECEIVED, "cancelled") or order.status == DRAFT:
        kind = "other"
    if kind == "confirmed" and order.status == ORDERED:
        order.status, order.confirmed_at = CONFIRMED, _now()
    elif kind == "confirmed":
        order.confirmed_at = order.confirmed_at or _now()
    elif kind == "shipped":
        orders.mark_shipped(db, order)
        order.confirmed_at = order.confirmed_at or _now()
    else:
        notify.tell(db, order.restaurant_id, kind="supplier_reply", title=f"{name} replied about your order: have a look",
                    body=excerpt or "They replied without any text.", link="#receive",
                    dedupe_key=f"reply:{order.id}:{abs(hash(excerpt)) % 10**10}")
    stamp = {"confirmed": "confirmed it", "shipped": "says it has shipped", "change": "wants to change something", "other": "replied"}[kind]
    order.note = f"{name} {stamp}: {excerpt}"[:255] if excerpt else f"{name} {stamp}."[:255]
    db.flush()
    return kind


def order_for_token(db: Session, token: str) -> PurchaseOrder | None:
    if not token or len(token) < 12:
        return None
    return db.query(PurchaseOrder).filter(PurchaseOrder.reply_token == token).first()


def late(db: Session, restaurant_id: str, today: date | None = None) -> list[dict]:
    """Say once a day about each order past its date. Returns the late orders."""
    today = today or date.today()
    rows = [o for o in expected(db, restaurant_id, today) if o["late"]]
    for row in rows:
        notify.tell(db, restaurant_id, kind="order_late", title=f"{row['supplier_name']} order is {row['days_late']} day{'s' if row['days_late'] != 1 else ''} late",
                    body=f"{row['summary']}, due {row['expected_on'].strftime('%A %b %-d')}. A message to chase it is ready.", link="#receive",
                    dedupe_key=f"late:{row['id']}:{today.isoformat()}")
    return rows
