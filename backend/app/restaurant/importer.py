"""Bring existing stock in from a spreadsheet.

A restaurant starting on FullHouse already has an inventory, usually in a
spreadsheet with its own column names ("Item", "Qty", "Par", "Vendor",
"Case size"). This reads a CSV export of it, maps those names to ours,
checks every row, and either reports what it would do (a dry run) or does
all of it -- never half: one bad row and nothing is saved, so the owner
fixes the file and tries again rather than untangling a partial import.

Opening stock goes through the same history as any other change: each new
item's first entry is its "Starting stock".
"""

import csv
import io
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.restaurant import inventory
from app.restaurant.models import InventoryItem, Supplier
from app.restaurant.schemas import MAX_PACK_SIZE, MAX_QUANTITY

MAX_ROWS = 2000

#: Our field -> the column names people use for it (compared lower-case, punctuation-free).
COLUMNS = {
    "name": ("name", "item", "item name", "product", "product name", "ingredient", "description"),
    "unit": ("unit", "units", "uom", "unit of measure", "count unit", "measure"),
    "quantity_on_hand": ("on hand", "quantity", "qty", "stock", "quantity on hand", "count", "current stock", "in stock"),
    "reorder_threshold": ("reorder at", "reorder point", "reorder level", "par", "par level", "min", "minimum"),
    "reorder_qty": ("reorder qty", "reorder quantity", "order qty", "order quantity", "reorder amount"),
    "unit_cost": ("unit cost", "cost", "cost per unit", "price", "unit price"),
    "supplier": ("supplier", "vendor", "distributor", "supplier name", "vendor name"),
    "pack_unit": ("pack unit", "purchase unit", "pack", "case", "case unit", "pack type", "bought by", "packed in"),
    "pack_size": ("pack size", "case size", "units per case", "per case", "case qty", "pack qty", "units per pack"),
}
_HEADER_LOOKUP = {alias: key for key, aliases in COLUMNS.items() for alias in aliases}

#: How units get written in the wild -> ours.
UNIT_ALIASES = {
    "lbs": "lb", "pound": "lb", "pounds": "lb", "#": "lb",
    "ounce": "oz", "ounces": "oz",
    "kilogram": "kg", "kilograms": "kg", "kilo": "kg", "kilos": "kg", "kgs": "kg",
    "gram": "g", "grams": "g", "gr": "g",
    "liter": "L", "liters": "L", "litre": "L", "litres": "L", "l": "L", "ltr": "L",
    "milliliter": "ml", "milliliters": "ml", "millilitre": "ml", "millilitres": "ml",
    "gallon": "gal", "gallons": "gal", "quart": "qt", "quarts": "qt", "floz": "fl oz", "fluid ounce": "fl oz",
    "ea": "each", "pc": "each", "pcs": "each", "piece": "each", "pieces": "each", "unit": "each", "units": "each",
    "item": "each", "items": "each", "ct": "each", "count": "each",
    "cases": "case", "cs": "case", "boxes": "box", "bx": "box", "packs": "pack", "pk": "pack", "packet": "pack",
    "bags": "bag", "bottles": "bottle", "btl": "bottle", "cans": "can", "jars": "jar", "trays": "tray",
    "bunches": "bunch", "heads": "head", "loaves": "loaf", "doz": "dozen", "dozens": "dozen",
}


def _header_key(header: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9# ]", " ", header.lower().replace("_", " "))).strip()


def normalize_import_unit(value: str) -> str:
    text = value.strip()
    return inventory.normalize_unit(UNIT_ALIASES.get(_header_key(text), UNIT_ALIASES.get(text.lower(), text)))


def _number(raw: str, what: str, *, allow_zero: bool = True, maximum: float = MAX_QUANTITY) -> float:
    text = raw.strip().replace("$", "").replace(",", "")
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{what} “{raw.strip()}” isn't a number.") from None
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"{what} isn't a number.")
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{what} must be {'more than' if not allow_zero else 'at least'} 0.")
    if value > maximum:
        raise ValueError(f"{what} {value:g} is more than {maximum:,}.")
    return value


@dataclass
class Row:
    line: int
    name: str | None = None
    status: str = "new"
    messages: list[str] = field(default_factory=list)
    values: dict = field(default_factory=dict)
    supplier: str | None = None


@dataclass
class Plan:
    rows: list[Row]
    new_suppliers: list[str]

    def count(self, status: str) -> int:
        return sum(1 for row in self.rows if row.status == status)


class ImportRejected(ValueError):
    """The file as a whole can't be read (not per-row problems, which the plan reports)."""


def plan(db: Session, restaurant_id: str | None, text: str, update_existing: bool) -> Plan:
    """Read and check every row; decide what each would do. Changes nothing."""
    text = text.lstrip("﻿")  # Excel's byte-order mark
    if not text.strip():
        raise ImportRejected("The file is empty.")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        headers = next(reader)
    except (StopIteration, csv.Error) as exc:
        raise ImportRejected("Couldn't read the file as CSV. Save the spreadsheet as CSV and try again.") from exc

    columns = {i: _HEADER_LOOKUP.get(_header_key(h)) for i, h in enumerate(headers)}
    found = {key for key in columns.values() if key}
    missing = [need for need in ("name", "unit") if need not in found]
    if missing:
        raise ImportRejected(
            f"The file needs a column for the item's {' and '.join(missing)}. "
            f"Found: {', '.join(h.strip() for h in headers if h.strip()) or 'no headings'}."
        )

    existing = {
        item.name.lower(): item for item in inventory.items_in(db, restaurant_id)
    }
    # This restaurant's suppliers only: never match another restaurant's by name.
    suppliers = {
        s.name.lower(): s for s in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id)
    }
    new_suppliers: dict[str, str] = {}
    seen: dict[str, int] = {}
    rows: list[Row] = []

    for number, record in enumerate(reader, start=2):
        if not any(cell.strip() for cell in record):
            continue
        if len(rows) >= MAX_ROWS:
            raise ImportRejected(f"The file has more than {MAX_ROWS:,} items. Split it and import each part.")
        raw = {columns[i]: cell for i, cell in enumerate(record) if i in columns and columns[i]}
        row = Row(line=number)
        rows.append(row)

        name = (raw.get("name") or "").strip()
        row.name = name or None
        if not name:
            row.messages.append("No item name.")
        elif len(name) > 255:
            row.messages.append("The name is longer than 255 characters.")
        elif name.lower() in seen:
            row.messages.append(f"Also on line {seen[name.lower()]}; each item once.")
        else:
            seen[name.lower()] = number

        values: dict = {}
        try:
            values["unit"] = normalize_import_unit(raw.get("unit") or "")
        except ValueError as exc:
            row.messages.append(str(exc) if (raw.get("unit") or "").strip() else "No unit.")
        for key, label in (("quantity_on_hand", "On hand"), ("reorder_threshold", "Reorder at"),
                           ("reorder_qty", "Reorder quantity"), ("unit_cost", "Unit cost")):
            if (raw.get(key) or "").strip():
                try:
                    values[key] = _number(raw[key], label)
                except ValueError as exc:
                    row.messages.append(str(exc))

        pack_unit, pack_size = (raw.get("pack_unit") or "").strip(), (raw.get("pack_size") or "").strip()
        if pack_unit or pack_size:
            try:
                if not (pack_unit and pack_size):
                    raise ValueError("Give both the pack (e.g. case) and how many come in one, or neither.")
                values["pack_unit"] = normalize_import_unit(pack_unit)
                values["pack_size"] = _number(pack_size, "Pack size", allow_zero=False, maximum=MAX_PACK_SIZE)
                if values["pack_unit"] == values.get("unit"):
                    raise ValueError("The pack can't be the same unit stock is counted in.")
            except ValueError as exc:
                row.messages.append(str(exc))

        supplier = (raw.get("supplier") or "").strip()
        if supplier:
            row.supplier = supplier
            if len(supplier) > 255:
                row.messages.append("The supplier name is longer than 255 characters.")
            elif supplier.lower() not in suppliers:
                new_suppliers.setdefault(supplier.lower(), supplier)

        row.values = values
        match = existing.get(name.lower()) if name else None
        if row.messages:
            row.status = "error"
        elif match is None:
            row.status = "new"
        elif not update_existing:
            row.status = "skip"
            row.messages.append("Already tracked; left as it is.")
        else:
            row.status = "update"
            if values.get("unit") and values["unit"] != match.unit and match.quantity_on_hand:
                row.status = "error"
                row.messages.append(
                    f"Tracked in {match.unit}, not {values['unit']}. Change the unit on the Inventory page first."
                )

    if not rows:
        raise ImportRejected("The file has headings but no items.")
    return Plan(rows=rows, new_suppliers=sorted(new_suppliers.values(), key=str.lower))


def carry_out(db: Session, restaurant_id: str | None, result: Plan) -> None:
    """Save a plan with no errors, all in the caller's transaction. The caller commits."""
    from app.restaurant.profile import inventory_settings

    prefs = inventory_settings(db)
    suppliers = {s.name.lower(): s for s in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id)}
    for name in result.new_suppliers:
        supplier = Supplier(
            restaurant_id=restaurant_id, name=name, lead_time_days=prefs.default_lead_time_days if prefs else 3
        )
        db.add(supplier)
        suppliers[name.lower()] = supplier
    db.flush()

    existing = {item.name.lower(): item for item in inventory.items_in(db, restaurant_id)}
    for row in result.rows:
        supplier_id = suppliers[row.supplier.lower()].id if row.supplier else None
        details = {k: v for k, v in row.values.items() if k != "quantity_on_hand"}
        if supplier_id:
            details["supplier_id"] = supplier_id
        if row.status == "new":
            item = InventoryItem(restaurant_id=restaurant_id, name=row.name, **details)
            with inventory.stock_change("initial", "Imported"):
                item.quantity_on_hand = row.values.get("quantity_on_hand", 0)
                db.add(item)
                db.flush()
        elif row.status == "update":
            item = existing[row.name.lower()]
            for key, value in details.items():
                setattr(item, key, value)
            if "quantity_on_hand" in row.values and row.values["quantity_on_hand"] != item.quantity_on_hand:
                inventory.lock_items(db, [item.id])
                with inventory.stock_change("count", "Imported"):
                    item.quantity_on_hand = row.values["quantity_on_hand"]
                    db.flush()
