"""What the charts and the what-if tools read. Everything here is a read: nothing is written.

Each function answers one question an owner asks, from rows the app already keeps:

    savings        Is this paying for itself?      (digests, and what was acted on)
    prime_cost     Am I inside my target?          (pnl_weeks)
    spend          Where did the money go?         (invoice lines, this period against the last)
    stock_outlook  When do I run out?              (stock, sales through recipes, orders on the way)
    switch_options Who is cheapest, and by how much a year? (matched invoice lines by supplier)
    search         Take me to it.                  (items, suppliers, invoices, dishes)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.prices import engine as prices
from app.prices.engine import COMPLETE
from app.prices.models import Invoice, InvoiceLine, OutreachDraft, PriceDigest, PriceItem
from app.restaurant import crud
from app.restaurant.models import (
    OPEN_ORDER_STATUSES,
    InventoryItem,
    MenuItem,
    Order,
    PurchaseOrder,
    Restaurant,
    Supplier,
)
from app.sales.models import PnlWeek

#: The sections of the digest that are money found: a cheaper supplier, a rise, a credit owed.
MONEY_SECTIONS = ("switch", "up", "owed")


def _acted_dates(db: Session, restaurant_id: str) -> tuple[dict[str, date], dict[str, date]]:
    """item -> the first day its message to a rep was sent, and item -> the day it was moved."""
    sent: dict[str, date] = {}
    for draft in db.query(OutreachDraft).filter(OutreachDraft.restaurant_id == restaurant_id, OutreachDraft.sent_at.isnot(None)):
        day = draft.sent_at.date()
        sent[draft.item_id] = min(sent.get(draft.item_id, day), day)
    moved = {i.id: i.moved_at.date() for i in db.query(PriceItem).filter(PriceItem.restaurant_id == restaurant_id, PriceItem.moved_at.isnot(None))}
    return sent, moved


def savings(db: Session, restaurant_id: str) -> dict:
    """Week by week: the dollars a week the digest found, and how much of it was acted on.

    "Acted on" follows the digest's own rule (prices.recovered_to_date): the item was moved to the
    cheaper supplier, or a message to the rep was sent, in or after the week the card appeared.
    """
    sent, moved = _acted_dates(db, restaurant_id)
    weeks, found_total, acted_total = [], 0.0, 0.0
    for digest in db.query(PriceDigest).filter(PriceDigest.restaurant_id == restaurant_id).order_by(PriceDigest.week_start):
        found = acted = 0.0
        for card in digest.cards or []:
            if card.get("section") not in MONEY_SECTIONS:
                continue
            impact = float(card.get("impact") or 0)
            found += impact
            item_id = card.get("item_id")
            if (card["section"] == "switch" and item_id in moved and moved[item_id] >= digest.week_start) or \
               (card["section"] in ("up", "owed") and item_id in sent and sent[item_id] >= digest.week_start):
                acted += impact
        found_total += found
        acted_total += acted
        weeks.append({"week_start": digest.week_start, "found": round(found, 2), "acted": round(acted, 2),
                      "found_to_date": round(found_total, 2), "acted_to_date": round(acted_total, 2)})
    latest = weeks[-1] if weeks else None
    return {"weeks": weeks, "found_to_date": round(found_total, 2), "acted_to_date": round(acted_total, 2),
            "open_this_week": round(latest["found"] - latest["acted"], 2) if latest else 0.0}


def prime_cost(db: Session, restaurant_id: str, weeks: int = 8) -> list[dict]:
    """The last weeks the prime-cost job has worked out, oldest first, complete weeks only."""
    rows = (db.query(PnlWeek).filter(PnlWeek.restaurant_id == restaurant_id, PnlWeek.sales_net > 0)
            .order_by(PnlWeek.week_of.desc()).limit(weeks).all())
    return [{"week_of": r.week_of, "sales": r.sales_net, "food_pct": r.food_pct, "labor_pct": r.labor_pct, "paper_pct": r.paper_pct,
             "prime_pct": r.prime_pct, "food": round((r.purchases_food or 0) + (r.purchases_bev or 0), 2), "labor": r.labor_dollars,
             "complete": not any((r.missing or {}).values())}
            for r in reversed(rows)]


def spend(db: Session, restaurant_id: str, days: int = 28, today: date | None = None) -> dict:
    """What was bought in the last `days`, by supplier and by category, beside the `days` before."""
    today = today or date.today()
    start, before = today - timedelta(days=days), today - timedelta(days=2 * days)
    suppliers = {s.id: s.name for s in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id)}
    by_supplier: dict[str, list[float]] = {}
    by_category: dict[str, list[float]] = {}
    rows = (db.query(InvoiceLine.extended, InvoiceLine.credited, InvoiceLine.supplier_id, Invoice.invoice_date, PriceItem.category)
            .join(Invoice, Invoice.id == InvoiceLine.invoice_id).outerjoin(PriceItem, PriceItem.id == InvoiceLine.item_id)
            .filter(Invoice.restaurant_id == restaurant_id, Invoice.status == COMPLETE, Invoice.invoice_date > before,
                    Invoice.invoice_date <= today))
    for extended, credited, supplier_id, day, category in rows:
        slot = 0 if day > start else 1
        amount = float(extended or 0) - float(credited or 0)
        by_supplier.setdefault(supplier_id, [0.0, 0.0])[slot] += amount
        by_category.setdefault(category or "unmatched", [0.0, 0.0])[slot] += amount

    def table(cells: dict[str, list[float]], label) -> list[dict]:
        out = [{"key": key, "label": label(key), "now": round(now, 2), "before": round(prior, 2)} for key, (now, prior) in cells.items()]
        return sorted(out, key=lambda r: -r["now"])

    return {"days": days, "from": start + timedelta(days=1), "to": today,
            "total_now": round(sum(v[0] for v in by_supplier.values()), 2), "total_before": round(sum(v[1] for v in by_supplier.values()), 2),
            "by_supplier": table(by_supplier, lambda k: suppliers.get(k, "Unknown supplier")),
            "by_category": table(by_category, lambda k: CATEGORY_LABELS.get(k, k.title()))}


CATEGORY_LABELS = {"food": "Food", "dry": "Dry goods", "paper": "Paper and disposables", "wine": "Wine", "bev": "Beverages",
                   "unmatched": "Not matched yet"}


def weekday_factors(db: Session, restaurant_id: str, days: int = 28) -> list[float]:
    """How busy each weekday is against the average day (Monday first), from completed orders. All 1.0 with no history."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    counts, seen = [0.0] * 7, [set() for _ in range(7)]
    from zoneinfo import ZoneInfo

    restaurant = db.get(Restaurant, restaurant_id)
    try:
        zone = ZoneInfo((restaurant.timezone if restaurant else None) or "UTC")
    except Exception:
        zone = timezone.utc
    for (created_at,) in db.query(Order.created_at).filter(Order.restaurant_id == restaurant_id, Order.status == "completed",
                                                           Order.created_at >= since):
        local = created_at.astimezone(zone)  # a Friday dinner is Friday, whatever day it is in UTC
        counts[local.weekday()] += 1
        seen[local.weekday()].add(local.date())
    per_day = [counts[i] / len(seen[i]) if seen[i] else 0.0 for i in range(7)]
    mean = sum(per_day) / 7
    if mean <= 0:
        return [1.0] * 7
    return [round(v / mean, 3) if v else 1.0 for v in per_day]


def stock_outlook(db: Session, restaurant_id: str, today: date | None = None) -> dict:
    """Every stocked item with what the par calculator and the run-out chart need."""
    today = today or date.today()
    restaurant = db.get(Restaurant, restaurant_id)
    settings = (restaurant.inventory_settings or {}) if restaurant else {}
    default_lead = int(settings.get("default_lead_time_days") or 2)
    from app.platform import flags
    from app.stock import engine as stock

    use = stock.daily_use(db, restaurant_id) if flags.enabled(db, restaurant_id, flags.V3_STOCK) else crud.daily_consumption(db, restaurant_id=restaurant_id)
    suppliers = {s.id: s for s in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id)}
    incoming: dict[str, list[dict]] = {}
    for order in db.query(PurchaseOrder).filter(PurchaseOrder.restaurant_id == restaurant_id, PurchaseOrder.status.in_(OPEN_ORDER_STATUSES),
                                               PurchaseOrder.status != "draft"):
        when = {"expected_on": max(order.expected_on or today, today), "late": bool(order.expected_on and order.expected_on < today)}
        if order.inventory_item_id is not None:
            left = order.quantity - (order.received_qty or 0.0 if order.status == "partial" else 0.0)  # what is still to come
            if left > 0:
                incoming.setdefault(order.inventory_item_id, []).append({**when, "quantity": round(left, 4)})
        for line in order.lines:  # an order of several lines: each stocked line still to come, in the stock item's unit
            if not line.inventory_item_id or not line.units or not line.qty:
                continue
            if line.received_qty is not None and line.receipt_status != "partial":
                continue
            left = line.units / line.qty * (line.qty - (line.received_qty or 0.0))
            if left > 0:
                incoming.setdefault(line.inventory_item_id, []).append({**when, "quantity": round(left, 4)})
    items = []
    for item in db.query(InventoryItem).filter(InventoryItem.restaurant_id == restaurant_id).order_by(InventoryItem.name):
        supplier = suppliers.get(item.supplier_id) if item.supplier_id else None
        daily = round(use.get(item.id, 0.0), 4)
        items.append({
            "id": item.id, "name": item.name, "unit": item.unit, "on_hand": item.quantity_on_hand, "daily_use": daily,
            "reorder_threshold": item.reorder_threshold, "reorder_qty": item.reorder_qty, "unit_cost": item.unit_cost,
            "pack_size": item.pack_size, "pack_unit": item.pack_unit, "par": item.par,
            "lead_time_days": supplier.lead_time_days if supplier and supplier.lead_time_days is not None else default_lead,
            "supplier_name": supplier.name if supplier else None,
            "days_left": round(item.quantity_on_hand / daily, 1) if daily > 0 else None,
            "incoming": sorted(incoming.get(item.id, []), key=lambda o: o["expected_on"]),
        })
    return {"today": today, "safety_days": float(settings.get("safety_days") or 1), "weekday_factors": weekday_factors(db, restaurant_id),
            "items": items}


def switch_options(db: Session, restaurant_id: str, today: date | None = None) -> list[dict]:
    """Items bought from more than one supplier: each supplier's latest price a pack, and the packs bought a week."""
    today = today or date.today()
    suppliers = {s.id: s.name for s in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id)}
    out = []
    for facts in prices.gather(db, restaurant_id, today).values():
        latest_by: dict[str, prices.Priced] = {}
        for priced in facts.priced:
            held = latest_by.get(priced.supplier_id)
            if held is None or (priced.day, priced.line.line_no) > (held.day, held.line.line_no):
                latest_by[priced.supplier_id] = priced
        if len(latest_by) < 2 or facts.latest is None or not facts.item.pack_size:
            continue
        weekly_packs = facts.weekly_units(today) / facts.item.pack_size
        out.append({
            "item_id": facts.item.id, "item_name": facts.item.name, "category": facts.item.category,
            "current_supplier_id": facts.latest.supplier_id, "weekly_packs": round(weekly_packs, 2),
            "suppliers": sorted(({"supplier_id": sid, "name": suppliers.get(sid, "Unknown supplier"),
                                  "per_pack": prices.per_pack(facts.item, p.per_unit), "last_date": p.day}
                                 for sid, p in latest_by.items()), key=lambda s: s["per_pack"]),
        })
    return sorted(out, key=lambda o: -(max(s["per_pack"] for s in o["suppliers"]) - min(s["per_pack"] for s in o["suppliers"])) * o["weekly_packs"])


def search(db: Session, restaurant_id: str, text: str, limit: int = 5) -> list[dict]:
    """Things in this restaurant whose name contains `text`: where a tap should land, most useful kinds first."""
    needle = text.strip()
    if len(needle) < 2:
        return []
    # The person's text is matched as written: "%" and "_" are characters, not wildcards.
    like = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    out: list[dict] = []
    for item in db.query(InventoryItem).filter(InventoryItem.restaurant_id == restaurant_id, InventoryItem.name.ilike(like, escape="\\")).order_by(InventoryItem.name).limit(limit):
        low = item.quantity_on_hand <= item.reorder_threshold
        out.append({"kind": "stock", "title": item.name, "href": "#inventory",
                    "subtitle": f"{item.quantity_on_hand:g} {item.unit} on hand" + (", low" if low else "")})
    for item in db.query(PriceItem).filter(PriceItem.restaurant_id == restaurant_id, PriceItem.name.ilike(like, escape="\\")).order_by(PriceItem.name).limit(limit):
        out.append({"kind": "price", "title": item.name, "href": f"#item/{item.id}", "subtitle": "Prices by supplier, eight weeks"})
    for supplier in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id, Supplier.name.ilike(like, escape="\\")).order_by(Supplier.name).limit(limit):
        out.append({"kind": "supplier", "title": supplier.name, "href": "#suppliers",
                    "subtitle": f"{supplier.lead_time_days}-day lead time" if supplier.lead_time_days is not None else "Supplier"})
    invoices = (db.query(Invoice, Supplier.name).join(Supplier, Supplier.id == Invoice.supplier_id)
                .filter(Invoice.restaurant_id == restaurant_id, or_(Invoice.reference.ilike(like, escape="\\"), Supplier.name.ilike(like, escape="\\")))
                .order_by(Invoice.invoice_date.desc()).limit(limit))
    for invoice, supplier_name in invoices:
        total = f", {prices.money(invoice.total)}" if invoice.total is not None else ""
        out.append({"kind": "invoice", "title": f"{supplier_name} {invoice.reference or ''}".strip(), "href": "#invoices",
                    "subtitle": f"Invoice, {prices.short_date(invoice.invoice_date)}{total}"})
    for dish in db.query(MenuItem).filter(MenuItem.restaurant_id == restaurant_id, MenuItem.name.ilike(like, escape="\\")).order_by(MenuItem.name).limit(limit):
        out.append({"kind": "dish", "title": dish.name, "href": "#menu", "subtitle": f"Dish, {prices.money(dish.price)}"})
    return out
