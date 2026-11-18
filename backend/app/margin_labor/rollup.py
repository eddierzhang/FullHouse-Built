"""`rollup_intervals`: order-level sales rolled up to 15-minute intervals per channel (spec sections 4 and 11).

A pure function of the orders of one business date: the date's rows are deleted and written again, so running it
twice leaves the same rows. An order counts in the interval it was opened in. Voided and refunded orders and
voided lines are left out. Covers are the POS guest counts and are null for an interval where no order had one;
they are never estimated.

`sales_items` (the per-item, per-day table older readers use) is left alone when another source already has
rows for the date; where order lines are the only source of a date's sales, its rows are written here under
source "orders", so those readers see the day.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.margin_labor.business import interval_start
from app.margin_labor.models import IntervalActual, SalesOrder, SalesOrderLine
from app.platform import events
from app.sales.models import SalesItem

CALC_VERSION = "1"
COUNTED_STATUSES = ("closed", "open")


def rollup_intervals(db: Session, restaurant_id: str, day: date) -> dict:
    """Rewrite one business date's interval rows (and, where nothing else supplies them, its per-item rows)."""
    orders = db.query(SalesOrder).filter(SalesOrder.restaurant_id == restaurant_id, SalesOrder.business_date == day,
                                         SalesOrder.status.in_(COUNTED_STATUSES)).all()
    by_order = {o.id: o for o in orders}
    cells: dict[tuple[datetime, str], dict] = {}
    for order in orders:
        cell = cells.setdefault((interval_start(order.opened_at), order.channel), {"orders": 0, "covers": None, "items": Decimal(0), "net": Decimal(0)})
        cell["orders"] += 1
        cell["net"] += order.net
        if order.covers is not None:
            cell["covers"] = (cell["covers"] or 0) + order.covers
    per_item: dict[tuple[str, str], list[Decimal]] = {}
    lines = db.query(SalesOrderLine).filter(SalesOrderLine.restaurant_id == restaurant_id, SalesOrderLine.business_date == day,
                                            SalesOrderLine.voided.is_(False)).all()
    for line in lines:
        order = by_order.get(line.order_id)
        if order is None:
            continue
        cells[(interval_start(order.opened_at), order.channel)]["items"] += line.qty
        held = per_item.setdefault((line.pos_item_id, line.pos_item_name), [Decimal(0), Decimal(0)])
        held[0] += line.qty
        held[1] += line.net

    db.query(IntervalActual).filter(IntervalActual.restaurant_id == restaurant_id, IntervalActual.business_date == day).delete(synchronize_session=False)
    now = datetime.now(timezone.utc)
    for (start, channel), cell in sorted(cells.items()):
        db.add(IntervalActual(restaurant_id=restaurant_id, business_date=day, interval_start=start, channel=channel, computed_at=now, **cell))

    wrote_items = 0
    others = db.query(SalesItem.id).filter(SalesItem.restaurant_id == restaurant_id, SalesItem.date == day, SalesItem.source != "orders").first()
    if others is None:
        db.query(SalesItem).filter(SalesItem.restaurant_id == restaurant_id, SalesItem.date == day, SalesItem.source == "orders").delete(synchronize_session=False)
        for (pos_item_id, name), (qty, net) in sorted(per_item.items()):
            db.add(SalesItem(restaurant_id=restaurant_id, date=day, pos_item_id=pos_item_id, pos_item_name=name, qty=float(qty), net=float(net), source="orders"))
            wrote_items += 1
    db.flush()
    events.emit(db, restaurant_id, "intervals.rolled_up", "interval_actuals", day.isoformat(), {"intervals": len(cells), "items": wrote_items})
    return {"business_date": day.isoformat(), "intervals": len(cells), "orders": len(orders), "sales_items": wrote_items}
