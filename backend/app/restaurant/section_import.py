"""A restaurant's own data, brought in from spreadsheets: suppliers, dishes, the team and supplier invoices.

Each kind works the way the stock import does (app.restaurant.importer): the CSV's column names are matched to
ours, every row is checked, and the answer is a plan saying what each row would do. A dry run stops there. A real
run carries the whole plan out or, if any row has a problem, none of it.

Uploading the same file twice changes nothing the second time: rows are matched to what is already there by name
(an invoice by supplier, number and date). Hourly rates are written only to the wage table, which is kept out of
the change history, and never appear in a row's messages. Invoices go through the same reader as a typed invoice
(`engine.ingest_invoice`), so lines it cannot place become questions as usual.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.restaurant.importer import ImportRejected, _header_key

MAX_ROWS = 2000

#: kind -> what it is called, our fields with the column names people use for them, the fields a file must have,
#: and a template a restaurant can fill in.
SPECS: dict[str, dict] = {
    "suppliers": {
        "label": "suppliers", "one": "supplier",
        "columns": {
            "name": ("name", "supplier", "supplier name", "vendor", "vendor name", "distributor", "company"),
            "contact_info": ("contact", "contact info", "email", "phone", "rep", "rep contact", "contact details"),
            "lead_time_days": ("lead time", "lead time days", "lead days", "days to deliver", "delivery days"),
        },
        "required": ("name",),
        "template": ["Supplier,Contact,Lead time days", "Green Valley Farms,orders@greenvalley.example 555 0101,2", "Harbor Dairy,rep@harbordairy.example,1"],
    },
    "dishes": {
        "label": "dishes", "one": "dish",
        "columns": {
            "name": ("name", "dish", "item", "menu item", "item name", "dish name", "product"),
            "category": ("category", "section", "course", "type", "menu section", "group"),
            "price": ("price", "menu price", "sell price", "selling price", "sells at"),
            "cost": ("cost", "plate cost", "food cost", "cost per plate", "unit cost"),
            "description": ("description", "notes", "details"),
        },
        "required": ("name", "price"),
        "template": ["Dish,Category,Price,Cost,Description", "Margherita Pizza,Entree,14.00,2.50,Tomato and fresh mozzarella", "Caesar Salad,Salad,11.00,1.50,"],
    },
    "staff": {
        "label": "team members", "one": "team member",
        "columns": {
            "name": ("name", "employee", "employee name", "staff", "staff name", "full name", "team member"),
            "role": ("role", "position", "job", "job title", "title"),
            "email": ("email", "e mail", "email address"),
            "phone": ("phone", "phone number", "mobile", "cell"),
            "hire_date": ("hire date", "hired", "start date", "date hired", "started"),
            "hourly_rate": ("hourly rate", "rate", "wage", "pay rate", "hourly wage", "hourly"),
        },
        "required": ("name", "role"),
        "template": ["Name,Role,Email,Phone,Hire date,Hourly rate", "Alex Rivera,Server,alex@example.com,555 0110,2025-03-01,15.00", "Jordan Lee,Line Cook,,,2024-09-15,22.00"],
    },
    "invoices": {
        "label": "invoice lines", "one": "invoice line",
        "columns": {
            "supplier": ("supplier", "vendor", "supplier name", "vendor name", "distributor"),
            "reference": ("invoice", "invoice number", "invoice no", "number", "reference", "invoice #", "inv", "inv #"),
            "invoice_date": ("date", "invoice date", "billed", "billed on"),
            "description": ("description", "item", "product", "line", "item description", "name"),
            "qty": ("qty", "quantity", "cases", "packs", "units"),
            "pack_text": ("pack", "pack size", "case size", "unit", "uom"),
            "unit_price": ("unit price", "price", "price each", "case price", "cost"),
            "extended": ("extended", "amount", "line total", "total", "ext"),
        },
        "required": ("supplier", "invoice_date", "description", "qty", "unit_price"),
        "template": ["Supplier,Invoice number,Date,Description,Qty,Pack,Unit price,Amount", "Sysco Foods,SYS-1120,2026-11-16,CHKN BRST 40LB,2,40 lb case,87.60,175.20",
                     "Sysco Foods,SYS-1120,2026-11-16,FLOUR 50LB,3,50 lb bag,18.90,56.70"],
    },
}


@dataclass
class Row:
    line: int
    name: str | None = None
    status: str = "new"  # new | update | skip | error
    messages: list[str] = field(default_factory=list)
    values: dict = field(default_factory=dict)

    def problem(self, message: str) -> None:
        self.status = "error"
        self.messages.append(message)


@dataclass
class Plan:
    kind: str
    rows: list[Row]
    ignored_columns: list[str] = field(default_factory=list)
    #: For invoices: how many separate invoices the new lines make.
    groups: int = 0

    def count(self, status: str) -> int:
        return sum(1 for row in self.rows if row.status == status)


def template(kind: str) -> str:
    return "\n".join(SPECS[kind]["template"]) + "\n"


def _read(kind: str, text: str) -> tuple[list[tuple[int, dict]], list[str]]:
    """The file's rows as {our field: text}, with the line each came from, and the columns nothing was made of."""
    spec = SPECS[kind]
    text = text.lstrip("﻿")  # Excel's byte-order mark
    if not text.strip():
        raise ImportRejected("The file is empty.")
    try:
        reader = csv.reader(io.StringIO(text))
        table = [row for row in reader]
    except csv.Error as exc:
        raise ImportRejected(f"That doesn't read as a CSV file ({exc}).") from exc
    table = [row for row in table if any(cell.strip() for cell in row)]
    if len(table) < 2:
        raise ImportRejected("The file has a header row and nothing under it.")
    if len(table) - 1 > MAX_ROWS:
        raise ImportRejected(f"The file has {len(table) - 1:,} rows; the most one import takes is {MAX_ROWS:,}. Split it and import each part.")
    lookup = {alias: key for key, aliases in spec["columns"].items() for alias in aliases}
    header, mapped, ignored = table[0], {}, []
    for index, cell in enumerate(header):
        key = lookup.get(_header_key(cell))
        if key is None or key in mapped.values():
            if cell.strip():
                ignored.append(cell.strip())
        else:
            mapped[index] = key
    missing = [key for key in spec["required"] if key not in mapped.values()]
    if missing:
        wanted = ", ".join(spec["columns"][key][0].title() for key in missing)
        raise ImportRejected(f"The file needs {'a column' if len(missing) == 1 else 'columns'} for: {wanted}. The first row has to be the column names.")
    return [(number, {key: (row[index].strip() if index < len(row) else "") for index, key in mapped.items()}) for number, row in enumerate(table[1:], start=2)], ignored


def _money(raw: str, what: str, *, allow_zero: bool = True, maximum: Decimal = Decimal("100000")) -> Decimal:
    try:
        value = Decimal(raw.replace("$", "").replace(",", "").strip())
    except InvalidOperation:
        raise ValueError(f"{what} “{raw}” isn't a number.") from None
    if not value.is_finite() or value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{what} must be {'more than' if not allow_zero else 'at least'} 0.")
    if value > maximum:
        raise ValueError(f"{what} {value} is more than {maximum:,}.")
    return value


def _day(raw: str, what: str) -> date:
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d %b %Y", "%b %d %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw.strip(), pattern).date()
        except ValueError:
            continue
    raise ValueError(f"{what} “{raw}” isn't a date. Use 2026-11-16 or 11/16/2026.")


def plan(db: Session, restaurant_id: str | None, kind: str, text: str, update_existing: bool = False) -> Plan:
    """Read and check every row; decide what each would do. Changes nothing."""
    if kind not in SPECS:
        raise ImportRejected(f"There is no import for “{kind}”.")
    rows, ignored = _read(kind, text)
    out = Plan(kind, [], ignored)
    {"suppliers": _plan_suppliers, "dishes": _plan_dishes, "staff": _plan_staff, "invoices": _plan_invoices}[kind](db, restaurant_id, rows, update_existing, out)
    return out


def _named(db: Session, restaurant_id: str | None, model) -> dict[str, object]:
    return {row.name.strip().lower(): row for row in db.query(model).filter(model.restaurant_id == restaurant_id)}


def _by_name(rows: list[tuple[int, dict]], existing: dict, update_existing: bool, out: Plan, what: str, check) -> None:
    """The shape shared by suppliers, dishes and staff: one row a name, matched to what is there by name."""
    seen: dict[str, int] = {}
    for line, cells in rows:
        row = Row(line, cells.get("name") or None)
        out.rows.append(row)
        if not row.name:
            row.problem(f"This row has no {what} name.")
            continue
        if len(row.name) > 255:
            row.problem("The name is longer than 255 characters.")
            continue
        key = row.name.lower()
        if key in seen:
            row.problem(f"“{row.name}” is also on line {seen[key]}. Keep one.")
            continue
        seen[key] = line
        try:
            row.values = check(cells, row)
        except ValueError as exc:
            row.problem(str(exc))
            continue
        if row.status == "error":
            continue
        if key in existing:
            row.status = "update" if update_existing else "skip"
            row.values["_existing"] = existing[key]
            if not update_existing:
                row.messages.append("Already here.")


def _plan_suppliers(db, restaurant_id, rows, update_existing, out: Plan) -> None:
    from app.restaurant.models import Supplier

    def check(cells: dict, _row: Row) -> dict:
        values: dict = {"name": cells["name"], "contact_info": (cells.get("contact_info") or None)}
        if values["contact_info"] and len(values["contact_info"]) > 255:
            raise ValueError("The contact details are longer than 255 characters.")
        if cells.get("lead_time_days"):
            days = _money(cells["lead_time_days"], "Lead time", maximum=Decimal(60))
            if days != days.to_integral_value():
                raise ValueError("Lead time is a whole number of days.")
            values["lead_time_days"] = int(days)
        return values

    _by_name(rows, _named(db, restaurant_id, Supplier), update_existing, out, "supplier", check)


def _plan_dishes(db, restaurant_id, rows, update_existing, out: Plan) -> None:
    from app.restaurant.models import MenuItem

    def check(cells: dict, _row: Row) -> dict:
        if not cells.get("price"):
            raise ValueError("This dish has no price.")
        values: dict = {"name": cells["name"], "category": (cells.get("category") or "Uncategorized")[:64],
                        "price": float(_money(cells["price"], "Price", allow_zero=False, maximum=Decimal(10000)))}
        if cells.get("cost"):
            values["cost"] = float(_money(cells["cost"], "Cost", maximum=Decimal(10000)))
        if cells.get("description"):
            values["description"] = cells["description"][:512]
        return values

    _by_name(rows, _named(db, restaurant_id, MenuItem), update_existing, out, "dish", check)


def _plan_staff(db, restaurant_id, rows, update_existing, out: Plan) -> None:
    from app.hr import staff as hr
    from app.restaurant import profile
    from app.restaurant.models import Staff

    existing = _named(db, restaurant_id, Staff)
    emails: dict[str, int] = {}

    def check(cells: dict, row: Row) -> dict:
        role = cells.get("role") or ""
        if not role:
            raise ValueError("This person has no role.")
        if problem := profile.role_problem(db, role):
            raise ValueError(problem)
        values: dict = {"name": cells["name"], "role": role[:64], "phone": (cells.get("phone") or None)}
        email = hr.normalize_email(cells.get("email") or None)
        if email:
            mine = existing.get(cells["name"].lower())
            if email in emails:
                raise ValueError(f"{email} is also on line {emails[email]}.")
            if hr.email_in_use(db, restaurant_id, email, mine.id if mine is not None else None):
                raise ValueError(f"Someone on the team already uses {email}.")
            emails[email] = row.line
        values["email"] = email
        if cells.get("hire_date"):
            values["hire_date"] = _day(cells["hire_date"], "Hire date")
        if cells.get("hourly_rate"):
            # Kept apart from the rest: written to the wage table only, and never put in a message.
            values["_rate"] = _money(cells["hourly_rate"], "Hourly rate", allow_zero=False, maximum=Decimal(1000)).quantize(Decimal("0.0001"))
        return values

    _by_name(rows, existing, update_existing, out, "team member", check)


def _plan_invoices(db, restaurant_id, rows, _update_existing, out: Plan) -> None:
    from app.prices.models import Invoice
    from app.restaurant.models import Supplier

    suppliers = _named(db, restaurant_id, Supplier)
    today = date.today()
    known: dict[tuple, bool] = {}
    groups: set[tuple] = set()
    for line, cells in rows:
        row = Row(line, cells.get("description") or None)
        out.rows.append(row)
        try:
            if not cells.get("supplier"):
                raise ValueError("This line has no supplier.")
            if not row.name:
                raise ValueError("This line has no description.")
            day = _day(cells.get("invoice_date") or "", "Invoice date")
            if day > today:
                raise ValueError("That invoice date hasn't happened yet.")
            qty = _money(cells.get("qty") or "", "Quantity", allow_zero=False)
            price = _money(cells.get("unit_price") or "", "Unit price")
            extended = _money(cells["extended"], "Amount", maximum=Decimal("10000000")) if cells.get("extended") else None
        except ValueError as exc:
            row.problem(str(exc))
            continue
        reference = (cells.get("reference") or "")[:60] or None
        key = (cells["supplier"].strip().lower(), reference, day)
        if key not in known:
            supplier = suppliers.get(key[0])
            known[key] = bool(supplier is not None and reference and db.query(Invoice.id).filter(
                Invoice.restaurant_id == restaurant_id, Invoice.supplier_id == supplier.id, Invoice.reference == reference, Invoice.invoice_date == day).first())
        row.values = {"supplier": cells["supplier"].strip(), "reference": reference, "invoice_date": day, "description": row.name[:200], "qty": float(qty),
                      "unit_price": float(price), "extended": None if extended is None else float(extended), "pack_text": (cells.get("pack_text") or None), "_group": key}
        if known[key]:
            row.status = "skip"
            row.messages.append(f"Invoice {reference} from {row.values['supplier']} on {day.isoformat()} is already here.")
        else:
            groups.add(key)
    out.groups = len(groups)


def carry_out(db: Session, restaurant_id: str | None, plan_: Plan) -> dict:
    """Do what a clean plan says. The caller commits. Returns counts for the reply."""
    if plan_.count("error"):
        raise ImportRejected("The file still has rows to fix.")
    return {"suppliers": _write_suppliers, "dishes": _write_dishes, "staff": _write_staff, "invoices": _write_invoices}[plan_.kind](db, restaurant_id, plan_)


def _apply(target, values: dict) -> None:
    for key, value in values.items():
        if not key.startswith("_") and value is not None:
            setattr(target, key, value)


def _write_suppliers(db, restaurant_id, plan_: Plan) -> dict:
    from app.restaurant import profile
    from app.restaurant.models import Supplier

    prefs = profile.inventory_settings(db)
    default = prefs.default_lead_time_days if prefs else 3
    for row in plan_.rows:
        if row.status == "new":
            db.add(Supplier(restaurant_id=restaurant_id, name=row.values["name"], contact_info=row.values.get("contact_info"),
                            lead_time_days=row.values.get("lead_time_days", default)))
        elif row.status == "update":
            _apply(row.values["_existing"], row.values)
    db.flush()
    return {}


def _write_dishes(db, restaurant_id, plan_: Plan) -> dict:
    from app.restaurant.models import MenuItem

    for row in plan_.rows:
        if row.status == "new":
            db.add(MenuItem(restaurant_id=restaurant_id, **{k: v for k, v in row.values.items() if not k.startswith("_")}))
        elif row.status == "update":
            _apply(row.values["_existing"], row.values)
    db.flush()
    return {}


def _write_staff(db, restaurant_id, plan_: Plan) -> dict:
    from app.margin_labor.models import EmployeeWage
    from app.restaurant.models import Staff

    rates = 0
    for row in plan_.rows:
        if row.status not in ("new", "update"):
            continue
        if row.status == "new":
            person = Staff(restaurant_id=restaurant_id, **{k: v for k, v in row.values.items() if not k.startswith("_")})
            db.add(person)
            db.flush()
        else:
            person = row.values["_existing"]
            _apply(person, row.values)
        rate = row.values.get("_rate")
        if rate is not None:
            wage = db.query(EmployeeWage).filter(EmployeeWage.restaurant_id == restaurant_id, EmployeeWage.staff_id == person.id).first()
            if wage is None:
                db.add(EmployeeWage(restaurant_id=restaurant_id, staff_id=person.id, hourly_rate=rate))
            else:
                wage.hourly_rate = rate
            rates += 1
    db.flush()
    return {"rates_set": rates}


def _write_invoices(db, restaurant_id, plan_: Plan) -> dict:
    from app.prices import engine, schemas
    from app.prices.models import OPEN
    from app.restaurant.models import Supplier

    grouped: dict[tuple, list[Row]] = {}
    for row in plan_.rows:
        if row.status == "new":
            grouped.setdefault(row.values["_group"], []).append(row)
    questions = 0
    for (_name, reference, day), rows in sorted(grouped.items(), key=lambda item: (item[0][2], item[0][0], item[0][1] or "")):
        name = rows[0].values["supplier"]
        supplier = db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id, Supplier.name.ilike(name)).first()
        if supplier is None:
            supplier = Supplier(restaurant_id=restaurant_id, name=name)
            db.add(supplier)
            db.flush()
        lines = [schemas.LineIn(description=r.values["description"], qty=r.values["qty"], unit_price=r.values["unit_price"], extended=r.values["extended"],
                                pack_text=r.values["pack_text"]) for r in rows]
        _invoice, asked = engine.ingest_invoice(db, restaurant_id, supplier, day, lines, "typed", reference)
        questions += sum(1 for q in asked if q.status == OPEN)
    if grouped:
        engine.build_digest(db, restaurant_id)
    return {"invoices": len(grouped), "questions": questions}


# --- working out what a file is --------------------------------------------------------------------------

#: Every kind of CSV the app takes, with the section it belongs to. The first four are read here; stock by
#: app.restaurant.importer; orders, punches and payouts by app.margin_labor.csv_import.
KINDS = {
    "suppliers": ("suppliers", "Suppliers & ordering"), "dishes": ("dishes", "Menu"), "staff": ("team members", "Labor and staffing"),
    "invoices": ("invoice lines", "Invoices & prices"), "inventory": ("stock items", "Inventory"), "orders": ("sales orders", "Menu and Labor and staffing"),
    "punches": ("clock-ins", "Labor and staffing"), "payouts": ("delivery payouts", "Menu"),
}

#: kind -> (column names that mark it, each worth a point; names that rule it out). Names are compared with
#: spaces and punctuation removed, so "Clock In" and "clock_in" are one name.
_SIGNS: dict[str, tuple[tuple[tuple[str, ...], ...], tuple[str, ...]]] = {
    "punches": ((("clockin", "timein", "intime"), ("clockout", "timeout", "outtime"), ("employee", "employeename", "name", "staff", "teammember")), ()),
    "payouts": ((("payout", "netpayout", "totalpayout"), ("commission", "commissionfee", "marketplacefee"), ("gross", "subtotal", "grosssales", "platform", "provider")), ()),
    "orders": ((("orderid", "order", "ordernumber", "checkid", "check", "ticket", "ticketid", "transactionid"), ("itemname", "item", "menuitem", "itemid", "sku"),
                ("openedat", "opened", "createdat", "orderdate", "datetime", "ordertime", "closedat", "channel", "ordertype")), ("supplier", "vendor", "invoice", "invoicenumber")),
    "invoices": ((("supplier", "vendor", "suppliername", "vendorname", "distributor"), ("unitprice", "price", "priceeach", "caseprice"), ("qty", "quantity", "cases"),
                  ("invoice", "invoicenumber", "invoiceno", "invoicedate", "date", "reference")), ("onhand", "reorderat", "par", "parlevel", "clockin", "orderid")),
    "inventory": ((("onhand", "quantityonhand", "currentstock", "instock", "stock"), ("reorderat", "reorderpoint", "par", "parlevel", "reorderqty", "reorderquantity"),
                   ("unit", "units", "uom"), ("item", "name", "itemname", "product", "ingredient")), ("invoice", "invoicenumber", "clockin", "orderid", "role")),
    "staff": ((("role", "position", "job", "jobtitle", "title"), ("name", "employee", "employeename", "staff", "fullname", "teammember"),
               ("hiredate", "hired", "startdate", "hourlyrate", "wage", "payrate", "email", "phone")), ("clockin", "clockout", "price", "supplier", "vendor")),
    "dishes": ((("price", "menuprice", "sellprice", "sellingprice", "sellsat"), ("dish", "menuitem", "dishname", "name", "item", "itemname"), ("category", "section", "course", "menusection")),
               ("supplier", "vendor", "qty", "quantity", "onhand", "orderid", "invoice", "unitprice", "clockin", "role")),
    "suppliers": ((("supplier", "vendor", "suppliername", "vendorname", "distributor", "company", "name"), ("contact", "contactinfo", "rep", "email", "phone", "leadtime", "leadtimedays", "leaddays")),
                  ("price", "unitprice", "qty", "quantity", "onhand", "role", "invoice", "clockin", "orderid", "category")),
}


def detect(text: str) -> tuple[str | None, float, list[str]]:
    """(kind, how sure from 0 to 1, the column names it went by). Sure means every marking column is there and none
    that rules the kind out; under 0.8 a person says what the file is before anything is read from it."""
    first = next((line for line in text.lstrip("\ufeff").splitlines() if line.strip()), "")
    try:
        cells = next(csv.reader(io.StringIO(first)), [])
    except csv.Error:
        return None, 0.0, []
    names = {"".join(ch for ch in cell.lower() if ch.isalnum()): cell.strip() for cell in cells if cell.strip()}
    best: tuple[str | None, float, list[str]] = (None, 0.0, [])
    for kind, (signs, against) in _SIGNS.items():
        found = [next((names[a] for a in group if a in names), None) for group in signs]
        hit = [f for f in found if f]
        score = len(hit) / len(signs)
        if any(a in names for a in against):
            score *= 0.4
        if score > best[1]:
            best = (kind, round(score, 2), hit)
    return best
