"""CSV exports from Toast, Square and Clover, and payroll, read by their headers.

`sniff` says what a file is from its header row; `plan` turns it into our rows with a mapping
the person can correct; `carry_out` writes them. A mapping that worked is saved per restaurant
(`csv_profiles`), so the second upload needs none. Rows that can't be read are listed by line
number, and nothing is written until every row is right.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.connectors import writers
from app.sales.models import CsvProfile

MAX_ROWS = 20000

#: Our fields per capability: required first.
FIELDS = {
    "sales_days": (("date", True), ("net_sales", True), ("gross_sales", False), ("tax", False), ("tips", False), ("covers", False)),
    "sales_items": (("date", True), ("pos_item_name", True), ("qty", True), ("net", True), ("pos_item_id", False)),
    "labor_days": (("date", True), ("hours", True), ("dollars", True)),
}

#: Known exports, by the headers that give them away (lower-cased). First match wins.
FORMATS: list[dict] = [
    dict(format="square_items", capability="sales_items", must={"date", "item", "qty", "net sales"},
         mapping={"date": "date", "pos_item_name": "item", "qty": "qty", "net": "net sales", "pos_item_id": "sku"}),
    dict(format="square_summary", capability="sales_days", must={"date", "net sales", "gross sales"},
         mapping={"date": "date", "net_sales": "net sales", "gross_sales": "gross sales", "tax": "tax", "tips": "tip"}),
    dict(format="toast_items", capability="sales_items", must={"order date", "menu item", "item qty", "net price"},
         mapping={"date": "order date", "pos_item_name": "menu item", "qty": "item qty", "net": "net price", "pos_item_id": "menu item id"}),
    dict(format="toast_summary", capability="sales_days", must={"business date", "net sales"},
         mapping={"date": "business date", "net_sales": "net sales", "gross_sales": "gross sales", "tax": "tax amount", "tips": "tip amount"}),
    dict(format="clover_items", capability="sales_items", must={"date", "item name", "items sold"},
         mapping={"date": "date", "pos_item_name": "item name", "qty": "items sold", "net": "net sales", "pos_item_id": "sku"}),
    dict(format="clover_payments", capability="sales_days", must={"payment date", "amount"},
         mapping={"date": "payment date", "net_sales": "amount", "tax": "tax amount", "tips": "tip amount"}),
    dict(format="payroll", capability="labor_days", must={"date", "hours"},
         mapping={"date": "date", "hours": "hours", "dollars": "gross pay"}),
    dict(format="payroll", capability="labor_days", must={"work date", "hours"},
         mapping={"date": "work date", "hours": "hours", "dollars": "gross pay"}),
]
#: Alternate header spellings for a field, tried when the mapped header is missing.
ALIASES = {
    "dollars": ("gross pay", "labor cost", "labor dollars", "dollars", "total pay", "wages", "cost"),
    "hours": ("hours", "regular hours", "total hours", "hours worked"),
    "net": ("net sales", "net price", "net", "amount", "net amount"),
    "net_sales": ("net sales", "net", "net amount", "amount"),
    "gross_sales": ("gross sales", "gross", "gross amount"),
    "tax": ("tax", "tax amount", "taxes"),
    "tips": ("tip", "tips", "tip amount", "gratuity"),
    "covers": ("covers", "guests", "guest count"),
    "qty": ("qty", "quantity", "item qty", "items sold", "count"),
    "pos_item_name": ("item", "item name", "menu item", "name", "product"),
    "pos_item_id": ("sku", "item id", "menu item id", "product id", "plu"),
    "date": ("date", "order date", "business date", "payment date", "work date", "day", "sale date"),
}

_MONEY = re.compile(r"[^0-9.\-]")


@dataclass
class Problem:
    line: int
    message: str


@dataclass
class Plan:
    format: str
    capability: str
    mapping: dict
    headers: list[str]
    rows: list[dict] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    preview: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems and bool(self.rows)


def _headers(text: str) -> tuple[list[str], list[dict]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    rows = []
    for raw in reader:
        rows.append({(k or "").strip(): (v or "").strip() for k, v in raw.items() if isinstance(k, str) and isinstance(v, (str, type(None)))})
        if len(rows) > MAX_ROWS:
            break
    return headers, rows


def sniff(headers: list[str]) -> dict | None:
    lower = {h.lower() for h in headers}
    for spec in FORMATS:
        if spec["must"] <= lower:
            return spec
    return None


def _find(headers: list[str], wanted: str | None, field_name: str) -> str | None:
    lower = {h.lower(): h for h in headers}
    if wanted and wanted.lower() in lower:
        return lower[wanted.lower()]
    for alias in ALIASES.get(field_name, ()):
        if alias in lower:
            return lower[alias]
    return None


def parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y", "%m/%d/%y", "%m/%d/%Y %H:%M",
                "%m/%d/%Y %I:%M %p", "%d/%m/%Y", "%b %d, %Y", "%B %d, %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw[:len(datetime.now().strftime(fmt)) + 2].strip(), fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def parse_number(raw: str) -> float | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    cleaned = _MONEY.sub("", raw)
    if cleaned in ("", "-", "."):
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def plan(text: str, capability: str | None = None, mapping: dict | None = None, profile: dict | None = None) -> Plan:
    """Read the file into our rows. `mapping` (ours -> theirs) and `capability` override the sniff."""
    headers, raw_rows = _headers(text)
    spec = sniff(headers)
    lower_headers = {h.lower() for h in headers}
    profile_fits = bool(
        profile and (capability in (None, profile.get("capability")))
        and all(str(profile["mapping"].get(f, "")).lower() in lower_headers for f, required in FIELDS.get(profile.get("capability", ""), ()) if required)
    )
    if mapping is None and spec is not None and (capability in (None, spec["capability"])):
        capability, mapping, fmt = spec["capability"], dict(spec["mapping"]), spec["format"]
    elif mapping is None and profile_fits:
        capability, mapping, fmt = profile["capability"], dict(profile["mapping"]), profile.get("format", "generic")
    else:
        fmt = "generic"
        capability = capability or "sales_days"
        mapping = dict(mapping or {})
    if capability not in FIELDS:
        return Plan(fmt, capability, mapping, headers, problems=[Problem(0, f"Unknown kind of file {capability!r}.")])
    resolved = {}
    problems: list[Problem] = []
    for field_name, required in FIELDS[capability]:
        header = _find(headers, mapping.get(field_name), field_name)
        if header is None and required:
            problems.append(Problem(0, f"No column for {field_name.replace('_', ' ')}. Headers: {', '.join(headers) or 'none'}."))
        if header is not None:
            resolved[field_name] = header
    if problems:
        return Plan(fmt, capability, resolved, headers, problems=problems)

    out: dict[tuple, dict] = {}
    for number, raw in enumerate(raw_rows, start=2):
        day = parse_date(raw.get(resolved["date"], ""))
        if day is None:
            problems.append(Problem(number, f"Can't read the date {raw.get(resolved['date'], '')!r}."))
            continue
        if capability == "sales_items":
            name = raw.get(resolved["pos_item_name"], "").strip()
            qty = parse_number(raw.get(resolved["qty"], ""))
            net = parse_number(raw.get(resolved["net"], ""))
            if not name or qty is None or net is None:
                problems.append(Problem(number, "An item needs a name, a quantity and a net amount."))
                continue
            key = (day, (raw.get(resolved.get("pos_item_id", ""), "") or name).strip())
            row = out.setdefault(key, {"date": day, "pos_item_name": name, "pos_item_id": key[1], "qty": 0.0, "net": 0.0, "source": "csv"})
            row["qty"] += qty
            row["net"] += net
        elif capability == "sales_days":
            net = parse_number(raw.get(resolved["net_sales"], ""))
            if net is None:
                problems.append(Problem(number, "A day needs net sales."))
                continue
            row = out.setdefault((day,), {"date": day, "net_sales": 0.0, "gross_sales": None, "tax": None, "tips": None, "covers": None, "source": "csv"})
            row["net_sales"] += net
            for field_name in ("gross_sales", "tax", "tips"):
                if field_name in resolved and (value := parse_number(raw.get(resolved[field_name], ""))) is not None:
                    row[field_name] = (row[field_name] or 0.0) + value
            if "covers" in resolved and (value := parse_number(raw.get(resolved["covers"], ""))) is not None:
                row["covers"] = int((row["covers"] or 0) + value)
        else:
            hours = parse_number(raw.get(resolved["hours"], ""))
            dollars = parse_number(raw.get(resolved["dollars"], ""))
            if hours is None or dollars is None:
                problems.append(Problem(number, "A day needs hours and dollars."))
                continue
            row = out.setdefault((day,), {"date": day, "hours": 0.0, "dollars": 0.0, "source": "csv"})
            row["hours"] += hours
            row["dollars"] += dollars
    rows = [dict(r) for r in out.values()]
    if capability == "sales_items":
        # Item lines also make the day's total, so one upload gives both.
        days: dict[date, float] = {}
        for r in rows:
            days[r["date"]] = days.get(r["date"], 0.0) + r["net"]
        for r in rows:
            r["_day_net"] = days[r["date"]]
    preview = [{k: (v.isoformat() if isinstance(v, date) else v) for k, v in r.items() if not k.startswith("_")} for r in rows[:8]]
    return Plan(fmt, capability, resolved, headers, rows=rows, problems=problems, preview=preview)


def carry_out(db: Session, restaurant_id: str, result: Plan, save_profile: bool = True) -> dict:
    """Write the rows and remember the mapping. Returns what was written."""
    if not result.ok:
        raise ValueError("The file has problems; fix them before importing.")
    written = {"sales_days": 0, "sales_items": 0, "labor_days": 0}
    if result.capability == "sales_items":
        written["sales_items"] = writers.write_sales_items(db, restaurant_id, result.rows, None)
        days = {}
        for r in result.rows:
            days[r["date"]] = {"date": r["date"], "net_sales": r["_day_net"], "source": "csv"}
        written["sales_days"] = writers.write_sales_days(db, restaurant_id, list(days.values()), None)
    elif result.capability == "sales_days":
        written["sales_days"] = writers.write_sales_days(db, restaurant_id, result.rows, None)
    else:
        written["labor_days"] = writers.write_labor_days(db, restaurant_id, result.rows, None)
    if save_profile:
        profile = db.query(CsvProfile).filter(CsvProfile.restaurant_id == restaurant_id, CsvProfile.capability == result.capability).first()
        if profile is None:
            profile = CsvProfile(restaurant_id=restaurant_id, capability=result.capability)
            db.add(profile)
        profile.mapping = {"format": result.format, "capability": result.capability, "mapping": result.mapping}
    db.flush()
    from app.platform import events

    touched = sorted({r["date"].isoformat() for r in result.rows})
    events.emit(db, restaurant_id, f"{result.capability}.synced", "csv", result.format,
                {"rows": len(result.rows), "first": touched[0], "last": touched[-1]})
    return written


def profile_for(db: Session, restaurant_id: str, capability: str | None = None) -> dict | None:
    query = db.query(CsvProfile).filter(CsvProfile.restaurant_id == restaurant_id)
    if capability:
        query = query.filter(CsvProfile.capability == capability)
    row = query.order_by(CsvProfile.updated_at.desc()).first()
    return row.mapping if row else None
