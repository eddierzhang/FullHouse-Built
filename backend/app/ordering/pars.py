"""Pars: the level each item is ordered up to.

    par = what a day uses × (the supplier's lead time + the days between order days + safety days)

Worked out from sales through recipes, so an item no recipe uses has no par. An owner can pin a par by hand,
and a pinned par is never overwritten.
"""

from __future__ import annotations

import math

from sqlalchemy.orm import Session

from app.ordering.models import SupplierCalendar
from app.restaurant import crud
from app.restaurant.models import InventoryItem, Restaurant, Supplier

DEFAULT_COVER_DAYS = 3


def cover_days(calendar: SupplierCalendar | None) -> float:
    """Days one order has to last: a week divided by how many days a week the supplier takes orders."""
    days = len(set(calendar.order_days or [])) if calendar else 0
    return round(7 / days, 2) if days else float(DEFAULT_COVER_DAYS)


def suggest(daily_use: float, lead_time_days: float, cover: float, safety_days: float) -> float:
    return round(daily_use * (lead_time_days + cover + safety_days), 3)


def table(db: Session, restaurant_id: str) -> list[dict]:
    """Every item with its use a day, the par worked out for it, and the par that stands (pinned or worked out)."""
    restaurant = db.get(Restaurant, restaurant_id)
    settings = (restaurant.inventory_settings or {}) if restaurant else {}
    safety = float(settings.get("safety_days") or 1)
    default_lead = int(settings.get("default_lead_time_days") or 2)
    use = crud.daily_consumption(db, restaurant_id=restaurant_id)
    suppliers = {s.id: s for s in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id)}
    calendars = {c.supplier_id: c for c in db.query(SupplierCalendar).filter(SupplierCalendar.restaurant_id == restaurant_id)}
    rows = []
    for item in db.query(InventoryItem).filter(InventoryItem.restaurant_id == restaurant_id).order_by(InventoryItem.name):
        supplier = suppliers.get(item.supplier_id) if item.supplier_id else None
        calendar = calendars.get(item.supplier_id) if item.supplier_id else None
        lead = (calendar.lead_time_days if calendar and calendar.lead_time_days is not None
                else supplier.lead_time_days if supplier and supplier.lead_time_days is not None else default_lead)
        daily = round(use.get(item.id, 0.0), 4)
        worked_out = suggest(daily, lead, cover_days(calendar), safety) if daily > 0 else None
        rows.append({"inventory_item_id": item.id, "name": item.name, "unit": item.unit, "on_hand": item.quantity_on_hand, "daily_use": daily,
                     "lead_time_days": lead, "cover_days": cover_days(calendar), "safety_days": safety, "suggested": worked_out,
                     "par": item.par if item.par_pinned else worked_out, "pinned": bool(item.par_pinned),
                     "supplier_name": supplier.name if supplier else None, "unit_cost": item.unit_cost})
    return rows


def recompute(db: Session, restaurant_id: str) -> int:
    """Write the worked-out par onto every item that isn't pinned. Returns how many changed."""
    items = {i.id: i for i in db.query(InventoryItem).filter(InventoryItem.restaurant_id == restaurant_id)}
    changed = 0
    for row in table(db, restaurant_id):
        item = items[row["inventory_item_id"]]
        if item.par_pinned:
            continue
        if (item.par or None) != (row["suggested"] or None):
            item.par = row["suggested"]
            changed += 1
    db.flush()
    return changed


def packs_to_par(par: float | None, on_hand: float, on_order: float, units_per_pack: float) -> int | None:
    """Whole packs that bring stock up to par; None when there is no par to order against."""
    if par is None or units_per_pack <= 0:
        return None
    short = par - on_hand - on_order
    return max(0, math.ceil(short / units_per_pack - 1e-9))
