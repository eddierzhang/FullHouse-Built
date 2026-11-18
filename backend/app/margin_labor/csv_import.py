"""CSV import for order-level sales, time punches and delivery payouts (spec sections 4 and 11).

Three files, each read-only and safe to import twice: every row is upserted on its external key, so the
second import of the same file changes nothing and row counts match. A row that cannot be read (no id, no
time, a number that isn't one) is left out and reported with its line number; nothing is guessed.

    orders    one row per order line: order_id, opened_at, item_name, qty, line_net, and optionally closed_at,
              channel, platform, covers, item_id, category, price, discounts, tax, tips, status, voided,
              voided_after_fire, fired_at, modifiers
    punches   punch_id, employee, role, clock_in, and optionally clock_out
    payouts   platform, order_id, and optionally gross, commission, fees, adjustments, payout, paid_at

Headers are matched without regard to case, spaces or punctuation, and a few common other names are accepted.
A time with no offset is the restaurant's local time. The business date is computed here, once.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.margin_labor import settings as ml_settings
from app.margin_labor.business import business_date, zone
from app.margin_labor.models import CHANNELS, ORDER_STATUSES, ChannelPayout, LaborPunch, PosItem, SalesOrder, SalesOrderLine
from app.platform import events
from app.restaurant.models import Staff

MAX_ROWS = 200_000
CONNECTION = "csv"

ALIASES = {
    "order_id": ("orderid", "order", "ordernumber", "checkid", "check", "ticket", "ticketid", "transactionid"),
    "opened_at": ("openedat", "opened", "created", "createdat", "orderdate", "datetime", "date", "time", "ordertime"),
    "closed_at": ("closedat", "closed", "paidat", "completedat"),
    "channel": ("channel", "ordertype", "diningoption", "servicetype", "source"),
    "platform": ("platform", "provider", "marketplace"),
    "covers": ("covers", "guests", "guestcount", "partysize"),
    "item_id": ("itemid", "sku", "plu", "menuitemid"),
    "item_name": ("itemname", "item", "menuitem", "name", "product"),
    "category": ("category", "menugroup", "salescategory"),
    "price": ("price", "menuprice", "unitprice"),
    "qty": ("qty", "quantity", "count"),
    "line_net": ("linenet", "net", "netsales", "amount", "netamount", "itemnet"),
    "discounts": ("discounts", "discount"),
    "tax": ("tax", "taxes"),
    "tips": ("tips", "tip", "gratuity"),
    "status": ("status", "orderstatus"),
    "voided": ("voided", "void", "isvoid"),
    "voided_after_fire": ("voidedafterfire", "voidafterfire", "wasted"),
    "fired_at": ("firedat", "fired", "senttokitchen"),
    "modifiers": ("modifiers", "mods"),
    "punch_id": ("punchid", "id", "shiftid", "timecardid", "entryid"),
    "employee": ("employee", "employeename", "name", "staff", "teammember"),
    "role": ("role", "job", "jobtitle", "position"),
    "clock_in": ("clockin", "in", "start", "timein", "intime"),
    "clock_out": ("clockout", "out", "end", "timeout", "outtime"),
    "gross": ("gross", "subtotal", "grosssales"),
    "commission": ("commission", "commissionfee", "marketplacefee"),
    "fees": ("fees", "fee", "processingfee", "otherfees"),
    "adjustments": ("adjustments", "adjustment", "errorcharges"),
    "payout": ("payout", "netpayout", "total", "totalpayout"),
    "paid_at": ("paidat", "payoutdate", "paid", "date"),
}
_CHANNEL_WORDS = {
    "dinein": "dine_in", "dine": "dine_in", "forhere": "dine_in", "eatin": "dine_in", "table": "dine_in",
    "takeout": "takeout", "togo": "takeout", "pickup": "takeout", "takeaway": "takeout", "carryout": "takeout",
    "delivery": "delivery_own", "deliveryown": "delivery_own", "inhousedelivery": "delivery_own",
    "deliveryplatform": "delivery_platform", "thirdparty": "delivery_platform", "marketplace": "delivery_platform",
    "doordash": "delivery_platform", "ubereats": "delivery_platform", "grubhub": "delivery_platform",
    "catering": "catering",
}
_PLATFORMS = {"doordash": "DoorDash", "ubereats": "Uber Eats", "grubhub": "Grubhub"}


@dataclass
class Result:
    kind: str
    rows: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    #: Orders, for the orders file: one order is many rows.
    orders: int = 0
    business_dates: list[date] = field(default_factory=list)
    #: [{"line": int, "problem": str}], first fifty.
    problems: list[dict] = field(default_factory=list)
    problem_count: int = 0

    def problem(self, line: int, text: str) -> None:
        self.problem_count += 1
        if len(self.problems) < 50:
            self.problems.append({"line": line, "problem": text})

    def as_dict(self) -> dict:
        return {"kind": self.kind, "rows": self.rows, "created": self.created, "updated": self.updated, "unchanged": self.unchanged,
                "orders": self.orders, "business_dates": [d.isoformat() for d in sorted(set(self.business_dates))],
                "problems": self.problems, "problem_count": self.problem_count}


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (header or "").lower())


def _columns(headers: list[str], wanted: tuple[str, ...]) -> dict[str, str]:
    """Our field -> the file's header, for each wanted field the file has. The field's own name wins over an alias."""
    by_norm = {_norm(h): h for h in headers if h}
    found = {}
    for name in wanted:
        for candidate in (_norm(name), *ALIASES.get(name, ())):
            if candidate in by_norm:
                found[name] = by_norm[candidate]
                break
    return found


def _rows(text: str) -> tuple[list[str], list[dict]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    rows = []
    for index, row in enumerate(reader):
        if index >= MAX_ROWS:
            break
        rows.append(row)
    return list(reader.fieldnames or []), rows


def _decimal(text: str | None, default: Decimal | None = Decimal(0)) -> Decimal | None:
    cleaned = (text or "").strip().replace("$", "").replace(",", "")
    if cleaned.startswith("(") and cleaned.endswith(")"):  # accountants' negative
        cleaned = "-" + cleaned[1:-1]
    if cleaned == "":
        return default
    try:
        value = Decimal(cleaned)
    except InvalidOperation as err:
        raise ValueError(f"{text!r} is not a number") from err
    if not value.is_finite():
        raise ValueError(f"{text!r} is not a number")
    return value


def _moment(text: str | None, tz) -> datetime | None:
    """A timestamp from a CSV cell. With an offset it is exact; without one it is the restaurant's local time."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    for candidate in (cleaned.replace("Z", "+00:00"), cleaned.replace(" ", "T", 1).replace("Z", "+00:00")):
        try:
            moment = datetime.fromisoformat(candidate)
            break
        except ValueError:
            moment = None
    if moment is None:
        for pattern in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %I:%M:%S %p", "%Y-%m-%d %I:%M %p"):
            try:
                moment = datetime.strptime(cleaned, pattern)
                break
            except ValueError:
                continue
    if moment is None:
        raise ValueError(f"{text!r} is not a date and time")
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=tz)


def _flag(text: str | None) -> bool:
    return (text or "").strip().lower() in ("1", "true", "yes", "y", "t", "voided")


def _channel(text: str | None, platform: str | None) -> tuple[str, str | None]:
    word = _norm(text or "")
    channel = word if word in {_norm(c) for c in CHANNELS} else _CHANNEL_WORDS.get(word)
    if channel in {_norm(c) for c in CHANNELS}:
        channel = next(c for c in CHANNELS if _norm(c) == channel)
    named = _PLATFORMS.get(word) or ((platform or "").strip() or None)
    if channel is None:
        channel = "delivery_platform" if named else "dine_in"
    if named and channel == "delivery_own":
        channel = "delivery_platform"
    return channel, named if channel == "delivery_platform" else None


def _set(row, values: dict) -> bool:
    """Write `values` onto `row`; whether anything differed."""
    changed = False
    for name, value in values.items():
        if getattr(row, name) != value:
            setattr(row, name, value)
            changed = True
    return changed


# --- orders ---------------------------------------------------------------------------------------


def import_orders(db: Session, restaurant_id: str, text: str) -> Result:
    """Order lines from a POS export. One order is the rows that share an order id; its lines are numbered in
    the order they appear, so the same file gives the same lines."""
    result = Result("orders")
    headers, rows = _rows(text)
    cols = _columns(headers, ("order_id", "opened_at", "closed_at", "channel", "platform", "covers", "item_id", "item_name", "category",
                              "price", "qty", "line_net", "discounts", "tax", "tips", "status", "voided", "voided_after_fire", "fired_at", "modifiers"))
    missing = [name for name in ("order_id", "opened_at", "item_name", "qty", "line_net") if name not in cols]
    if missing:
        result.problem(1, f"The file has no column for: {', '.join(missing)}.")
        return result
    conf = ml_settings.for_restaurant(db, restaurant_id)
    tz = zone(conf.timezone)

    def cell(row: dict, name: str) -> str | None:
        return row.get(cols[name]) if name in cols else None

    # Read the whole file first: an order with a bad line is left out whole, not half imported.
    grouped: dict[str, dict] = {}
    for number, row in enumerate(rows, start=2):
        result.rows += 1
        external = (cell(row, "order_id") or "").strip()
        if not external:
            result.problem(number, "No order id.")
            continue
        order = grouped.setdefault(external, {"lines": [], "bad": False, "first": number})
        try:
            opened = _moment(cell(row, "opened_at"), tz)
            if opened is None:
                raise ValueError("no opening time")
            name = (cell(row, "item_name") or "").strip()
            if not name:
                raise ValueError("no item name")
            qty = _decimal(cell(row, "qty"), None)
            if qty is None:
                raise ValueError("no quantity")
            order["lines"].append({
                "opened_at": opened, "closed_at": _moment(cell(row, "closed_at"), tz), "channel": cell(row, "channel"), "platform": cell(row, "platform"),
                "covers": cell(row, "covers"), "pos_item_id": ((cell(row, "item_id") or "").strip() or name)[:120], "pos_item_name": name[:200],
                "category": (cell(row, "category") or "").strip() or None, "price": _decimal(cell(row, "price"), None), "qty": qty,
                "net": _decimal(cell(row, "line_net")), "discounts": _decimal(cell(row, "discounts")), "tax": _decimal(cell(row, "tax")),
                "tips": _decimal(cell(row, "tips")), "status": (cell(row, "status") or "").strip().lower(), "voided": _flag(cell(row, "voided")),
                "voided_after_fire": _flag(cell(row, "voided_after_fire")), "fired_at": _moment(cell(row, "fired_at"), tz),
                "modifiers": [m.strip() for m in (cell(row, "modifiers") or "").split("|") if m.strip()],
            })
        except ValueError as err:
            order["bad"] = True
            result.problem(number, f"Order {external}: {err}. The whole order was left out.")

    existing = {o.external_id: o for o in db.query(SalesOrder).filter(SalesOrder.restaurant_id == restaurant_id, SalesOrder.connection_key == CONNECTION,
                                                                     SalesOrder.external_id.in_(list(grouped) or [""]))} if len(grouped) <= 900 else \
        {o.external_id: o for o in db.query(SalesOrder).filter(SalesOrder.restaurant_id == restaurant_id, SalesOrder.connection_key == CONNECTION)}
    catalog = {i.pos_item_id: i for i in db.query(PosItem).filter(PosItem.restaurant_id == restaurant_id, PosItem.connection_key == CONNECTION)}
    for external, held in grouped.items():
        if held["bad"] or not held["lines"]:
            continue
        lines = held["lines"]
        first = lines[0]
        opened = min(l["opened_at"] for l in lines)
        closed = max((l["closed_at"] for l in lines if l["closed_at"]), default=None)
        channel, platform = _channel(first["channel"], first["platform"])
        live = [l for l in lines if not l["voided"]]
        net = sum((l["net"] for l in live), Decimal(0))
        discounts = max((l["discounts"] for l in lines), default=Decimal(0))  # an order-level figure repeated on each row
        status = first["status"] if first["status"] in ORDER_STATUSES else ("voided" if not live else "closed")
        try:
            covers = int(Decimal(first["covers"])) if (first["covers"] or "").strip() else None
        except (InvalidOperation, ValueError):
            covers = None
        day = business_date(opened, conf.timezone, conf.business_day_start)
        values = {"opened_at": opened, "closed_at": closed, "business_date": day, "channel": channel, "platform": platform, "covers": covers,
                  "gross": net + discounts, "discounts": discounts, "net": net, "tax": max((l["tax"] for l in lines), default=Decimal(0)),
                  "tips": max((l["tips"] for l in lines), default=Decimal(0)), "status": status}
        order = existing.get(external)
        if order is None:
            order = SalesOrder(restaurant_id=restaurant_id, connection_key=CONNECTION, external_id=external[:120], **values)
            db.add(order)
            db.flush()
            changed, made = True, True
        else:
            changed, made = _set(order, values), False
        held_lines = {l.line_no: l for l in db.query(SalesOrderLine).filter(SalesOrderLine.restaurant_id == restaurant_id, SalesOrderLine.order_id == order.id)} if not made else {}
        for line_no, line in enumerate(lines, start=1):
            line_values = {"business_date": day, "pos_item_id": line["pos_item_id"], "pos_item_name": line["pos_item_name"], "qty": line["qty"],
                           "net": line["net"], "modifiers": line["modifiers"], "voided": line["voided"], "voided_after_fire": line["voided_after_fire"],
                           "fired_at": line["fired_at"]}
            row = held_lines.pop(line_no, None)
            if row is None:
                db.add(SalesOrderLine(restaurant_id=restaurant_id, order_id=order.id, line_no=line_no, **line_values))
                changed = True
            else:
                changed = _set(row, line_values) or changed
            item = catalog.get(line["pos_item_id"])
            if item is None:
                item = PosItem(restaurant_id=restaurant_id, connection_key=CONNECTION, pos_item_id=line["pos_item_id"], name=line["pos_item_name"],
                               category=line["category"], price=line["price"])
                db.add(item)
                catalog[line["pos_item_id"]] = item
            else:
                _set(item, {k: v for k, v in (("name", line["pos_item_name"]), ("category", line["category"]), ("price", line["price"])) if v is not None})
        for stale in held_lines.values():  # the order has fewer lines than it did
            db.delete(stale)
            changed = True
        result.orders += 1
        result.created += 1 if made else 0
        result.updated += 1 if (changed and not made) else 0
        result.unchanged += 0 if changed else 1
        if changed:
            result.business_dates.append(day)
    db.flush()
    if result.business_dates:
        events.emit(db, restaurant_id, "sales.synced", "sales_orders", CONNECTION,
                    {"business_dates": sorted({d.isoformat() for d in result.business_dates}), "orders": result.orders})
    return result


# --- punches --------------------------------------------------------------------------------------


def import_punches(db: Session, restaurant_id: str, text: str) -> Result:
    result = Result("punches")
    headers, rows = _rows(text)
    cols = _columns(headers, ("punch_id", "employee", "role", "clock_in", "clock_out"))
    missing = [name for name in ("punch_id", "employee", "role", "clock_in") if name not in cols]
    if missing:
        result.problem(1, f"The file has no column for: {', '.join(missing)}.")
        return result
    conf = ml_settings.for_restaurant(db, restaurant_id)
    tz = zone(conf.timezone)
    people = {s.name.strip().lower(): s.id for s in db.query(Staff).filter(Staff.restaurant_id == restaurant_id)}
    existing = {p.external_id: p for p in db.query(LaborPunch).filter(LaborPunch.restaurant_id == restaurant_id, LaborPunch.source == "csv")}
    for number, row in enumerate(rows, start=2):
        result.rows += 1
        try:
            external = (row.get(cols["punch_id"]) or "").strip()
            name = (row.get(cols["employee"]) or "").strip()
            role = re.sub(r"[^a-z0-9]+", "_", (row.get(cols["role"]) or "").strip().lower()).strip("_")
            clock_in = _moment(row.get(cols["clock_in"]), tz)
            clock_out = _moment(row.get(cols["clock_out"]), tz) if "clock_out" in cols else None
            if not external or not name or not role or clock_in is None:
                raise ValueError("needs a punch id, an employee, a role and a clock-in time")
            if clock_out is not None and clock_out <= clock_in:
                raise ValueError("clocks out before it clocks in")
            if clock_out is not None and (clock_out - clock_in).total_seconds() > 24 * 3600:
                raise ValueError("is longer than a day")
        except ValueError as err:
            result.problem(number, f"{err}.".capitalize())
            continue
        values = {"employee_id": people.get(name.lower()), "employee_name": name[:200], "role_key": role[:40], "clock_in": clock_in,
                  "clock_out": clock_out, "business_date": business_date(clock_in, conf.timezone, conf.business_day_start)}
        punch = existing.get(external)
        if punch is None:
            punch = LaborPunch(restaurant_id=restaurant_id, source="csv", external_id=external[:120], **values)
            db.add(punch)
            existing[external] = punch
            result.created += 1
            result.business_dates.append(values["business_date"])
        elif _set(punch, values):
            result.updated += 1
            result.business_dates.append(values["business_date"])
        else:
            result.unchanged += 1
    db.flush()
    if result.business_dates:
        events.emit(db, restaurant_id, "punches.synced", "labor_punches", "csv",
                    {"business_dates": sorted({d.isoformat() for d in result.business_dates})})
    return result


# --- payouts --------------------------------------------------------------------------------------


def import_payouts(db: Session, restaurant_id: str, text: str) -> Result:
    result = Result("payouts")
    headers, rows = _rows(text)
    cols = _columns(headers, ("platform", "order_id", "gross", "commission", "fees", "adjustments", "payout", "paid_at"))
    missing = [name for name in ("platform", "order_id") if name not in cols]
    if missing:
        result.problem(1, f"The file has no column for: {', '.join(missing)}.")
        return result
    tz = zone(ml_settings.for_restaurant(db, restaurant_id).timezone)
    existing = {(p.platform, p.external_order_id): p for p in db.query(ChannelPayout).filter(ChannelPayout.restaurant_id == restaurant_id)}
    orders = {o.external_id: o.id for o in db.query(SalesOrder).filter(SalesOrder.restaurant_id == restaurant_id, SalesOrder.channel == "delivery_platform")}
    for number, row in enumerate(rows, start=2):
        result.rows += 1
        try:
            platform = (row.get(cols["platform"]) or "").strip()
            platform = _PLATFORMS.get(_norm(platform), platform)
            external = (row.get(cols["order_id"]) or "").strip()
            if not platform or not external:
                raise ValueError("needs a platform and an order id")
            money = {name: _decimal(row.get(cols[name])) if name in cols else Decimal(0) for name in ("gross", "commission", "fees", "adjustments", "payout")}
            # Platforms print what they took as a negative or a positive; it is stored as what it cost, positive.
            money["commission"], money["fees"] = abs(money["commission"]), abs(money["fees"])
            paid_at = _moment(row.get(cols["paid_at"]), tz) if "paid_at" in cols else None
        except ValueError as err:
            result.problem(number, f"{err}.".capitalize())
            continue
        values = {**money, "paid_at": paid_at, "order_id": orders.get(external)}
        payout = existing.get((platform, external))
        if payout is None:
            payout = ChannelPayout(restaurant_id=restaurant_id, platform=platform[:40], external_order_id=external[:120], **values)
            db.add(payout)
            existing[(platform, external)] = payout
            result.created += 1
        elif _set(payout, values):
            result.updated += 1
        else:
            result.unchanged += 1
    db.flush()
    if result.created or result.updated:
        events.emit(db, restaurant_id, "payouts.synced", "channel_payouts", "csv", {"rows": result.created + result.updated})
    return result


IMPORTERS = {"orders": import_orders, "punches": import_punches, "payouts": import_payouts}
