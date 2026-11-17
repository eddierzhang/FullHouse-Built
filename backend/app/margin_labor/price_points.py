"""Price points: the unit price of an ingredient in its base unit, read from confirmed invoice lines.

There is no `price_points` table: an invoice line that is matched to an item, has its pack size, and sits on a
complete invoice is the price point, and its id is the line's id. Prices are decimals from the moment they are read.

Policies (a restaurant setting):
    latest   the price point with the most recent invoice date, across all suppliers
    avg_30d  the quantity-weighted mean over the 30 days to the date asked about

Outlier guard: a price more than 3 times, or less than one third of, the item's median over the 90 days before it
is not used for costing; it is listed for review. Most such cases are unit or extraction errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from sqlalchemy.orm import Session

from app.margin_labor import units
from app.margin_labor.costing import Item, Price
from app.margin_labor.models import ItemConversion
from app.prices.models import Invoice, InvoiceLine, PriceItem

COMPLETE = "complete"
OUTLIER_FACTOR = Decimal(3)
MEDIAN_DAYS = 90


@dataclass
class Point:
    line_id: str
    invoice_id: str
    item_id: str
    supplier_id: str
    day: date
    line_no: int
    #: When the invoice was saved: two invoices dated the same day are taken in the order they arrived.
    saved_at: object
    #: Dollars per base unit.
    unit_price: Decimal
    #: Base units bought on this line.
    qty: Decimal
    #: Dollars credited back against the line.
    credited: Decimal
    outlier: bool = False
    #: The 90-day median it was compared with, when it was.
    median: Decimal | None = None

    @property
    def key(self):
        return (self.day, str(self.saved_at), self.line_no, self.line_id)


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def items(db: Session, restaurant_id: str) -> dict[str, Item]:
    """Every invoice item of the restaurant, with its base unit and its own conversions."""
    own: dict[str, dict[str, Decimal]] = {}
    for c in db.query(ItemConversion).filter(ItemConversion.restaurant_id == restaurant_id):
        own.setdefault(c.canonical_item_id, {})[units.clean(c.from_unit)] = c.to_base_factor
    out = {}
    for item in db.query(PriceItem).filter(PriceItem.restaurant_id == restaurant_id):
        out[item.id] = Item(item.id, item.name, base_unit(item)[0], own.get(item.id, {}))
    return out


def base_unit(item: PriceItem) -> tuple[str, Decimal]:
    """(base unit, base units in one of the item's invoice unit). The stored pair when set, else from the unit."""
    if item.base_unit and item.to_base_factor:
        return item.base_unit, item.to_base_factor
    return units.base_of(item.unit)


def load(db: Session, restaurant_id: str, item_ids: set[str] | None = None) -> dict[str, list[Point]]:
    """Every price point of the restaurant by item, oldest first, with outliers marked."""
    query = (db.query(InvoiceLine.id, InvoiceLine.invoice_id, InvoiceLine.item_id, InvoiceLine.supplier_id, Invoice.invoice_date, InvoiceLine.line_no,
                      Invoice.created_at, InvoiceLine.unit_price, InvoiceLine.pack_size, InvoiceLine.qty, InvoiceLine.credited)
             .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
             .filter(InvoiceLine.restaurant_id == restaurant_id, InvoiceLine.item_id.isnot(None), InvoiceLine.pack_size.isnot(None),
                     Invoice.status == COMPLETE))
    if item_ids is not None:
        if not item_ids:
            return {}
        query = query.filter(InvoiceLine.item_id.in_(sorted(item_ids)))
    factors = {i.id: base_unit(i)[1] for i in db.query(PriceItem).filter(PriceItem.restaurant_id == restaurant_id)}
    out: dict[str, list[Point]] = {}
    for line_id, invoice_id, item_id, supplier_id, day, line_no, saved_at, unit_price, pack_size, qty, credited in query:
        factor = factors.get(item_id)
        if not factor or not pack_size or pack_size <= 0 or unit_price is None or unit_price <= 0:
            continue
        per_base = _dec(unit_price) / _dec(pack_size) / factor
        out.setdefault(item_id, []).append(Point(line_id, invoice_id, item_id, supplier_id, day, line_no, saved_at, per_base,
                                                 _dec(qty) * _dec(pack_size) * factor, _dec(credited or 0)))
    for points in out.values():
        points.sort(key=lambda p: p.key)
        _mark_outliers(points)
    return out


def _mark_outliers(points: list[Point]) -> None:
    for point in points:
        since = point.day - timedelta(days=MEDIAN_DAYS)
        others = [p.unit_price for p in points if p is not point and since <= p.day <= point.day]
        if not others:
            continue
        point.median = Decimal(median(others))
        point.outlier = point.unit_price > point.median * OUTLIER_FACTOR or point.unit_price * OUTLIER_FACTOR < point.median


def price_as_of(points: list[Point] | None, day: date, policy: str = "latest") -> Price | None:
    """The unit price to cost with on `day`: by the policy, from the points dated on or before it, outliers left out."""
    usable = [p for p in points or () if p.day <= day and not p.outlier]
    if not usable:
        return None
    latest = max(usable, key=lambda p: p.key)
    if policy == "avg_30d":
        window = [p for p in usable if p.day > day - timedelta(days=30) and p.qty > 0]
        total = sum((p.qty for p in window), Decimal(0))
        if total > 0:
            mean = sum((p.unit_price * p.qty for p in window), Decimal(0)) / total
            return Price(mean, latest.line_id, latest.invoice_id, latest.supplier_id, latest.day.isoformat(), "avg_30d")
    return Price(latest.unit_price, latest.line_id, latest.invoice_id, latest.supplier_id, latest.day.isoformat(), "latest")


def outliers(points: dict[str, list[Point]], since: date | None = None) -> list[Point]:
    return sorted((p for pts in points.values() for p in pts if p.outlier and (since is None or p.day >= since)), key=lambda p: p.key, reverse=True)
