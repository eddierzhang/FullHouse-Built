"""Stock that tracks itself.

Four things move stock without anyone typing a quantity on the Inventory page:

    deplete   POS sales, through each dish's recipe, take ingredients off the shelf.
    counts    A person walks an area and says what is there; the difference is recorded.
    waste     Two taps (or a sentence): what was thrown away and why.
    deliveries  already booked by confirmed invoices and received orders (app.prices.stock, app.restaurant.orders).

Every movement goes through `app.stock.ledger`, so each is recorded once however often the job, the sync or the
phone retries. `rollup` then writes one row per item per day for the reports, and `variance` reads only those rows.
Counts are never required: without them stock is "expected", and the report says which items nobody has counted.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.notify import service as notify
from app.recipes.models import CONFIRMED, Recipe
from app.restaurant import crud
from app.restaurant.models import InventoryAdjustment, InventoryItem, MenuItem, RecipeItem, Restaurant, Supplier
from app.sales.models import SalesItem
from app.stock import ledger
from app.stock.models import OPEN, POSTED, StockCount, StockCountLine, StockDaily, StorageArea

#: Why something was thrown away. The key is stored at the front of the movement's note.
WASTE_REASONS = {
    "spoiled": "Spoiled",
    "dropped": "Dropped or spilled",
    "overcooked": "Overcooked or sent back",
    "prep": "Trim and prep loss",
    "comped": "Comped",
    "staff_meal": "Staff meal",
    "other": "Other",
}
#: Reasons that count as each column of the daily rollup.
RECEIVED, USED, WASTED, COUNTED = ("delivery", "reorder"), ("sale", "used", "prep"), ("waste",), ("count",)
EPSILON = 1e-6


# --- sales deplete stock ------------------------------------------------------------------------


def _deplete_from(db: Session, restaurant: Restaurant, today: date) -> date:
    """The first day whose sales come off the shelf: the day stock tracking was first run. Earlier sales are
    already in the stock figures the restaurant started with, so taking them off again would be wrong."""
    settings = dict(restaurant.inventory_settings or {})
    if settings.get("deplete_from"):
        return date.fromisoformat(settings["deplete_from"])
    settings["deplete_from"] = today.isoformat()
    restaurant.inventory_settings = settings
    db.add(restaurant)
    db.flush()
    return today


def dish_map(db: Session, restaurant_id: str) -> tuple[dict[str, str], dict[str, str]]:
    """POS item -> dish: by the confirmed recipe that names the POS item, and by the dish's name."""
    by_pos = {r.pos_item_id: r.menu_item_id for r in db.query(Recipe).filter(
        Recipe.restaurant_id == restaurant_id, Recipe.status == CONFIRMED, Recipe.pos_item_id.isnot(None), Recipe.menu_item_id.isnot(None))}
    by_name = {m.name.strip().lower(): m.id for m in db.query(MenuItem).filter(MenuItem.restaurant_id == restaurant_id)}
    return by_pos, by_name


def _sale_key(row: SalesItem, inventory_item_id: str) -> str:
    pos = row.pos_item_id if len(row.pos_item_id) <= 60 else hashlib.sha1(row.pos_item_id.encode()).hexdigest()[:24]
    return f"sale:{row.date.isoformat()}:{row.source}:{pos}:{inventory_item_id}"


def deplete(db: Session, restaurant_id: str, today: date | None = None) -> dict:
    """Take what was sold off the shelf: each POS sale line, through its dish's recipe, with yield applied.

    Run as often as you like. A sale line already taken off is skipped; one whose quantity changed since (a day
    still trading when it was last synced) has only the difference taken off. A sale with no dish or no recipe
    moves nothing and is listed by `unmapped`.
    """
    today = today or date.today()
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        return {"moved": 0, "sales": 0}
    since = _deplete_from(db, restaurant, today)
    by_pos, by_name = dish_map(db, restaurant_id)
    recipes: dict[str, list[RecipeItem]] = {}
    for line in db.query(RecipeItem).join(MenuItem, MenuItem.id == RecipeItem.menu_item_id).filter(MenuItem.restaurant_id == restaurant_id):
        recipes.setdefault(line.menu_item_id, []).append(line)
    items = {i.id: i for i in db.query(InventoryItem).filter(InventoryItem.restaurant_id == restaurant_id)}
    # What each sale line has already moved, with any corrections ("#2", "#3") folded in.
    already: dict[str, list[float]] = {}
    for key, change in db.query(InventoryAdjustment.source_key, InventoryAdjustment.change).filter(
            InventoryAdjustment.restaurant_id == restaurant_id, InventoryAdjustment.source_key.like("sale:%")):
        cell = already.setdefault(key.split("#")[0], [0.0, 0])
        cell[0] += change
        cell[1] += 1

    moved = sales = 0
    for row in db.query(SalesItem).filter(SalesItem.restaurant_id == restaurant_id, SalesItem.date >= since).order_by(SalesItem.date):
        menu_item_id = by_pos.get(row.pos_item_id) or by_name.get(row.pos_item_name.strip().lower())
        lines = recipes.get(menu_item_id or "")
        if not lines:
            continue
        sales += 1
        for line in lines:
            item = items.get(line.inventory_item_id)
            if item is None:
                continue
            usable = (item.yield_pct or 100.0) / 100.0
            target = -round(row.qty * line.quantity / (usable if usable > 0 else 1.0), 6)
            base = _sale_key(row, item.id)
            done, times = already.get(base, [0.0, 0])
            difference = round(target - done, 6)
            if abs(difference) <= EPSILON:
                continue
            key = base if times == 0 else f"{base}#{times + 1}"
            if ledger.move(db, item, difference, "sale", source_key=key, note=f"{row.qty:g} × {row.pos_item_name}, {row.date.isoformat()}"):
                already[base] = [done + difference, times + 1]
                moved += 1
    return {"moved": moved, "sales": sales, "since": since}


def unmapped(db: Session, restaurant_id: str) -> list[dict]:
    """What the POS sold, since stock tracking began, that took nothing off the shelf, and why."""
    restaurant = db.get(Restaurant, restaurant_id)
    since = (restaurant.inventory_settings or {}).get("deplete_from") if restaurant else None
    if not since:
        return []
    by_pos, by_name = dish_map(db, restaurant_id)
    with_recipe = {menu_item_id for (menu_item_id,) in db.query(RecipeItem.menu_item_id).distinct()}
    out = []
    rows = (db.query(SalesItem.pos_item_id, SalesItem.pos_item_name, func.sum(SalesItem.qty), func.sum(SalesItem.net))
            .filter(SalesItem.restaurant_id == restaurant_id, SalesItem.date >= date.fromisoformat(since))
            .group_by(SalesItem.pos_item_id, SalesItem.pos_item_name))
    for pos_item_id, name, qty, net in rows:
        menu_item_id = by_pos.get(pos_item_id) or by_name.get(name.strip().lower())
        if menu_item_id is None:
            out.append({"pos_item_name": name, "qty": float(qty or 0), "net": round(float(net or 0), 2), "why": "no dish on the menu has this name"})
        elif menu_item_id not in with_recipe:
            out.append({"pos_item_name": name, "qty": float(qty or 0), "net": round(float(net or 0), 2), "why": "its dish has no recipe"})
    return sorted(out, key=lambda r: -r["net"])


# --- counts -------------------------------------------------------------------------------------


def items_for_count(db: Session, restaurant_id: str, storage_area_id: str | None) -> list[InventoryItem]:
    """The items on a count sheet, in the order someone walks them: by shelf, then by name."""
    query = db.query(InventoryItem).filter(InventoryItem.restaurant_id == restaurant_id)
    if storage_area_id == "unplaced":
        query = query.filter(InventoryItem.storage_area_id.is_(None))
    elif storage_area_id:
        query = query.filter(InventoryItem.storage_area_id == storage_area_id)
    return sorted(query.all(), key=lambda i: (i.shelf_order or 0, i.name.lower()))


def start_count(db: Session, restaurant_id: str, storage_area_id: str | None, started_by: str | None,
                client_key: str | None = None) -> StockCount:
    """Open a count of one area (or of everything). An open count of the same area is carried on, not doubled."""
    if client_key:
        existing = db.query(StockCount).filter(StockCount.restaurant_id == restaurant_id, StockCount.client_key == client_key).first()
        if existing is not None:
            return existing
    existing = db.query(StockCount).filter(StockCount.restaurant_id == restaurant_id, StockCount.status == OPEN,
                                           StockCount.storage_area_id == storage_area_id if storage_area_id else StockCount.storage_area_id.is_(None)).first()
    if existing is not None:
        return existing
    count = StockCount(restaurant_id=restaurant_id, storage_area_id=storage_area_id, started_by=started_by, client_key=client_key)
    db.add(count)
    db.flush()
    for item in items_for_count(db, restaurant_id, storage_area_id):
        db.add(StockCountLine(restaurant_id=restaurant_id, count_id=count.id, inventory_item_id=item.id, item_name=item.name,
                              unit=item.unit, expected_qty=item.quantity_on_hand, unit_cost=item.unit_cost))
    db.flush()
    return count


def count_lines(db: Session, count: StockCount) -> list[StockCountLine]:
    lines = db.query(StockCountLine).filter(StockCountLine.restaurant_id == count.restaurant_id, StockCountLine.count_id == count.id).all()
    order = {i.id: (i.shelf_order or 0, i.name.lower()) for i in db.query(InventoryItem).filter(InventoryItem.restaurant_id == count.restaurant_id)}
    return sorted(lines, key=lambda l: order.get(l.inventory_item_id, (0, l.item_name.lower())))


def save_lines(db: Session, count: StockCount, entries: list[dict]) -> None:
    """Write what has been counted so far. `counted_qty` None clears a line back to not counted."""
    if count.status != OPEN:
        raise ValueError("That count has already been posted.")
    lines = {l.inventory_item_id: l for l in count_lines(db, count)}
    for entry in entries:
        line = lines.get(entry["inventory_item_id"])
        if line is None:
            item = db.query(InventoryItem).filter(InventoryItem.restaurant_id == count.restaurant_id, InventoryItem.id == entry["inventory_item_id"]).first()
            if item is None:
                continue  # another restaurant's item, or one deleted since: not ours to count
            line = StockCountLine(restaurant_id=count.restaurant_id, count_id=count.id, inventory_item_id=item.id, item_name=item.name,
                                  unit=item.unit, expected_qty=item.quantity_on_hand, unit_cost=item.unit_cost)
            db.add(line)
            lines[item.id] = line
        counted = entry.get("counted_qty")
        if counted is not None and counted < 0:
            raise ValueError(f"{line.item_name} can't be counted as less than nothing.")
        line.counted_qty = None if counted is None else round(float(counted), 6)
    db.flush()


def post_count(db: Session, count: StockCount, area_name: str | None = None) -> dict:
    """Make the count the stock figures. Each counted line records the difference between what the ledger
    expected at this moment and what was counted; lines nobody counted change nothing. Posting twice is once."""
    if count.status == POSTED:
        return summary_of(db, count)
    where = f"Counted in {area_name}" if area_name else "Counted"
    for line in count_lines(db, count):
        if line.counted_qty is None:
            continue
        item = db.query(InventoryItem).filter(InventoryItem.restaurant_id == count.restaurant_id, InventoryItem.id == line.inventory_item_id).first()
        if item is None:
            continue
        line.expected_qty = item.quantity_on_hand
        line.unit_cost = item.unit_cost
        ledger.set_to(db, item, line.counted_qty, "count", source_key=f"count:{count.id}:{item.id}", note=where)
    count.status = POSTED
    count.posted_at = datetime.now(timezone.utc)
    db.flush()
    return summary_of(db, count)


def summary_of(db: Session, count: StockCount) -> dict:
    lines = count_lines(db, count)
    counted = [l for l in lines if l.counted_qty is not None]
    difference = sum((l.counted_qty - l.expected_qty) * l.unit_cost for l in counted)
    return {"lines": len(lines), "counted": len(counted), "difference_dollars": round(difference, 2),
            "short": sum(1 for l in counted if l.counted_qty < l.expected_qty - EPSILON),
            "over": sum(1 for l in counted if l.counted_qty > l.expected_qty + EPSILON)}


def count_now(db: Session, restaurant_id: str, entries: list[dict], *, storage_area_id: str | None = None,
              started_by: str | None = None, client_key: str | None = None, area_name: str | None = None) -> tuple[StockCount, dict]:
    """Open, fill and post a count in one step: for a phone syncing after poor signal, and for voice.
    The same `client_key` posts once."""
    if client_key:
        existing = db.query(StockCount).filter(StockCount.restaurant_id == restaurant_id, StockCount.client_key == client_key).first()
        if existing is not None and existing.status == POSTED:
            return existing, summary_of(db, existing)
    count = StockCount(restaurant_id=restaurant_id, storage_area_id=storage_area_id, started_by=started_by, client_key=client_key)
    db.add(count)
    db.flush()
    save_lines(db, count, entries)
    return count, post_count(db, count, area_name)


# --- waste --------------------------------------------------------------------------------------


def record_waste(db: Session, item: InventoryItem, quantity: float, reason: str, note: str | None = None,
                 source_key: str | None = None) -> float:
    """Take `quantity` (in the item's unit) off the shelf as waste. Returns what was actually taken: waste
    cannot take away more than is there."""
    if reason not in WASTE_REASONS:
        raise ValueError(f"Unknown waste reason {reason!r}. Known: {', '.join(WASTE_REASONS)}.")
    if quantity <= 0:
        raise ValueError("Enter how much was wasted.")
    before = item.quantity_on_hand
    text = WASTE_REASONS[reason] + (f": {note.strip()}" if note and note.strip() else "")
    moved = ledger.move(db, item, -quantity, "waste", source_key=source_key, note=text, allow_negative=False)
    return round(before - item.quantity_on_hand, 6) if moved else 0.0


# --- the daily rollup and the variance report ---------------------------------------------------


def rollup(db: Session, restaurant_id: str, days: int = 35, today: date | None = None) -> int:
    """One row per item per day for the last `days`: received, used, wasted, corrected by a count, and the close.
    A sale counts on the day it was sold, not the day the job took it off. Rewrites the window; returns rows written."""
    today = today or date.today()
    start = today - timedelta(days=days - 1)
    cells: dict[tuple[str, date], dict] = {}
    rows = (db.query(InventoryAdjustment).filter(InventoryAdjustment.restaurant_id == restaurant_id,
                                                InventoryAdjustment.created_at >= datetime.combine(start - timedelta(days=days), datetime.min.time(), tzinfo=timezone.utc))
            .order_by(InventoryAdjustment.created_at, InventoryAdjustment.id))
    for row in rows:
        day = row.created_at.date()
        if row.source_key and row.source_key.startswith("sale:"):
            try:
                day = date.fromisoformat(row.source_key.split(":")[1])
            except ValueError:
                pass
        if day < start or day > today:
            continue
        cell = cells.setdefault((row.inventory_item_id, day), {"received": 0.0, "used": 0.0, "wasted": 0.0, "count_change": 0.0, "closing": 0.0})
        if row.reason in RECEIVED:
            cell["received"] += row.change
        elif row.reason in USED:
            cell["used"] -= row.change
        elif row.reason in WASTED:
            cell["wasted"] -= row.change
        elif row.reason in COUNTED:
            cell["count_change"] += row.change
        cell["closing"] = row.quantity_after
    db.query(StockDaily).filter(StockDaily.restaurant_id == restaurant_id, StockDaily.date >= start).delete(synchronize_session=False)
    for (item_id, day), cell in cells.items():
        db.add(StockDaily(restaurant_id=restaurant_id, inventory_item_id=item_id, date=day, **{k: round(v, 6) for k, v in cell.items()}))
    db.flush()
    return len(cells)


def variance(db: Session, restaurant_id: str, days: int = 28, today: date | None = None) -> dict:
    """Where stock went over the last `days`, item by item, from the daily rollup.

    `unexplained` is what counts found missing (negative) or extra (positive) beyond sales, waste and deliveries:
    the shrink. An item nobody counted in the window has no unexplained figure, only an expected one.
    """
    today = today or date.today()
    start = today - timedelta(days=days - 1)
    totals: dict[str, dict] = {}
    for row in db.query(StockDaily).filter(StockDaily.restaurant_id == restaurant_id, StockDaily.date >= start, StockDaily.date <= today):
        cell = totals.setdefault(row.inventory_item_id, {"received": 0.0, "used": 0.0, "wasted": 0.0, "count_change": 0.0})
        for field in cell:
            cell[field] += getattr(row, field)
    counted_on = dict(
        db.query(StockCountLine.inventory_item_id, func.max(StockCount.posted_at))
        .join(StockCount, StockCount.id == StockCountLine.count_id)
        .filter(StockCountLine.restaurant_id == restaurant_id, StockCount.status == POSTED, StockCountLine.counted_qty.isnot(None),
                StockCount.posted_at >= datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc))
        .group_by(StockCountLine.inventory_item_id).all())
    rows = []
    for item in db.query(InventoryItem).filter(InventoryItem.restaurant_id == restaurant_id).order_by(InventoryItem.name):
        cell = totals.get(item.id, {"received": 0.0, "used": 0.0, "wasted": 0.0, "count_change": 0.0})
        counted = item.id in counted_on
        unexplained = round(cell["count_change"], 4) if counted else None
        rows.append({
            "inventory_item_id": item.id, "name": item.name, "unit": item.unit, "unit_cost": item.unit_cost, "on_hand": item.quantity_on_hand,
            "received": round(cell["received"], 3), "used": round(cell["used"], 3), "wasted": round(cell["wasted"], 3),
            "wasted_dollars": round(cell["wasted"] * item.unit_cost, 2),
            "unexplained": unexplained, "unexplained_dollars": round(unexplained * item.unit_cost, 2) if counted else None,
            "pct_of_use": round(-unexplained / cell["used"] * 100, 1) + 0.0 if counted and cell["used"] > 0 else None,
            "last_counted_at": counted_on.get(item.id),
        })
    rows.sort(key=lambda r: (r["unexplained_dollars"] is None, r["unexplained_dollars"] or 0.0))
    counted_rows = [r for r in rows if r["unexplained"] is not None]
    return {
        "days": days, "from": start, "to": today, "items": rows,
        "missing_dollars": round(-sum(min(0.0, r["unexplained_dollars"]) for r in counted_rows), 2),
        "extra_dollars": round(sum(max(0.0, r["unexplained_dollars"]) for r in counted_rows), 2),
        "wasted_dollars": round(sum(r["wasted_dollars"] for r in rows), 2),
        "used_dollars": round(sum(r["used"] * r["unit_cost"] for r in rows), 2),
        "counted": len(counted_rows), "not_counted": len(rows) - len(counted_rows),
    }


def waste_log(db: Session, restaurant_id: str, days: int = 28, limit: int = 50) -> list[dict]:
    """Recent waste entries, newest first, with the reason read back off the note."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    labels = {label: key for key, label in WASTE_REASONS.items()}
    out = []
    rows = (db.query(InventoryAdjustment, InventoryItem).join(InventoryItem, InventoryItem.id == InventoryAdjustment.inventory_item_id)
            .filter(InventoryAdjustment.restaurant_id == restaurant_id, InventoryAdjustment.reason == "waste", InventoryAdjustment.created_at >= since)
            .order_by(InventoryAdjustment.created_at.desc()).limit(limit))
    for row, item in rows:
        label, _, rest = (row.note or "").partition(": ")
        known = label in labels
        out.append({"id": row.id, "item_name": item.name, "unit": item.unit, "quantity": round(-row.change, 3),
                    "dollars": round(-row.change * item.unit_cost, 2), "reason": labels.get(label, "other"),
                    "reason_label": label if known else "Wasted", "note": (rest or None) if known else (row.note or None),
                    "by": row.actor_id if row.actor_type == "human" else row.actor_type, "at": row.created_at})
    return out


# --- alerts -------------------------------------------------------------------------------------


def alerts(db: Session, restaurant_id: str) -> list[dict]:
    """What needs a decision today: items that run out before a delivery could arrive, and dishes that can't be made."""
    restaurant = db.get(Restaurant, restaurant_id)
    default_lead = int(((restaurant.inventory_settings or {}) if restaurant else {}).get("default_lead_time_days") or 2)
    use = crud.daily_consumption(db, restaurant_id=restaurant_id)
    suppliers = {s.id: s for s in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id)}
    items = {i.id: i for i in db.query(InventoryItem).filter(InventoryItem.restaurant_id == restaurant_id)}
    out = []
    for item in items.values():
        daily = use.get(item.id, 0.0)
        if daily <= 0:
            continue
        supplier = suppliers.get(item.supplier_id) if item.supplier_id else None
        lead = supplier.lead_time_days if supplier and supplier.lead_time_days is not None else default_lead
        days_left = item.quantity_on_hand / daily
        if days_left < lead:
            out.append({"kind": "runs_out", "inventory_item_id": item.id, "title": f"{item.name} runs out before a delivery could arrive",
                        "detail": f"{max(0.0, days_left):.1f} days left at {daily:.1f} {item.unit} a day; {supplier.name if supplier else 'a supplier'} needs {lead} "
                                  f"{'day' if lead == 1 else 'days'}.", "days_left": round(max(0.0, days_left), 1)})
    for dish in db.query(MenuItem).filter(MenuItem.restaurant_id == restaurant_id, MenuItem.is_available.is_(True)).order_by(MenuItem.name):
        short = [items[line.inventory_item_id].name for line in db.query(RecipeItem).filter(RecipeItem.menu_item_id == dish.id)
                 if line.inventory_item_id in items and items[line.inventory_item_id].quantity_on_hand < line.quantity]
        if short:
            out.append({"kind": "eighty_six", "menu_item_id": dish.id, "title": f"86 {dish.name}",
                        "detail": f"Not enough {', '.join(short)} for one plate.", "days_left": 0.0})
    return sorted(out, key=lambda a: (a["kind"] != "eighty_six", a["days_left"]))


def run(db: Session, restaurant_id: str, today: date | None = None) -> dict:
    """The stock job: take sales off, roll the days up, and say once a day what needs a decision."""
    today = today or date.today()
    result = deplete(db, restaurant_id, today)
    result["rolled_up"] = rollup(db, restaurant_id, today=today)
    told = 0
    for alert in alerts(db, restaurant_id):
        key = alert.get("inventory_item_id") or alert.get("menu_item_id")
        if notify.tell(db, restaurant_id, kind=f"stock_{alert['kind']}", title=alert["title"], body=alert["detail"], link="#variance",
                       dedupe_key=f"stock:{alert['kind']}:{key}:{today.isoformat()}"):
            told += 1
    result["told"] = told
    return result


def status(db: Session, restaurant_id: str) -> dict:
    """Whether sales are coming off the shelf, since when, and when they last did."""
    restaurant = db.get(Restaurant, restaurant_id)
    since = (restaurant.inventory_settings or {}).get("deplete_from") if restaurant else None
    last = db.query(func.max(InventoryAdjustment.created_at)).filter(InventoryAdjustment.restaurant_id == restaurant_id,
                                                                    InventoryAdjustment.reason == "sale").scalar()
    last_sale_day = db.query(func.max(SalesItem.date)).filter(SalesItem.restaurant_id == restaurant_id).scalar()
    return {"tracking_since": since, "last_depleted_at": last, "last_sales_day": last_sale_day, "out_of_balance": ledger.out_of_balance(db, restaurant_id)}


def areas(db: Session, restaurant_id: str) -> list[StorageArea]:
    return db.query(StorageArea).filter(StorageArea.restaurant_id == restaurant_id).order_by(StorageArea.position, StorageArea.name).all()
