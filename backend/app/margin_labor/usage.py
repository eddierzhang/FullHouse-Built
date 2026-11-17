"""Module B, usage variance without counts (spec section 6): per ingredient, what recipes say was used against
what was bought, with the unexplained gap ranked in dollars a week.

`run` is the job. It rewrites the rows of the last eight weekly windows (each 28 business dates ending on a
Sunday), so the same sales, invoices and recipes always give the same rows, then opens and closes findings for the
newest window. Recipe usage comes from Module A (`costing.usage`), yield and sub-recipes included.

What it cannot say: whether a gap is waste, over-portioning or theft. Each finding carries three checks for a
person to make.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.margin_labor import costing, menu_costs, price_points, usage_math, words
from app.margin_labor import settings as ml_settings
from app.margin_labor.models import Finding, SalesOrderLine, UsageVariance
from app.margin_labor.usage_math import CALC_VERSION, NOISE_DAYS, WINDOW_DAYS, Variance
from app.platform import events
from app.prices.models import PriceItem
from app.restaurant.models import Supplier
from app.sales.models import SalesItem

VARIANCE, DATA_GAP = "usage_variance", "usage_data_gap"
WINDOWS = 8
QTY = Decimal("0.0001")


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def window_end(today: date) -> date:
    """The last Sunday before `today`: the end of the newest complete week."""
    return today - timedelta(days=today.weekday() + 1)


def thresholds(db: Session, restaurant_id: str) -> dict:
    return {name: ml_settings.value(db, restaurant_id, name) for name in (
        "usage_min_weekly_dollars", "usage_min_pct_of_expected", "usage_data_gap_pct", "coverage_learning", "coverage_live", "usage_collapse_under_dollars")}


class Inputs:
    """Everything the eight windows read, loaded once."""

    def __init__(self, db: Session, restaurant_id: str, now: datetime | None = None):
        self.world = menu_costs.load(db, restaurant_id, now)
        self.end = window_end(self.world.today)
        first = self.end - timedelta(days=7 * (WINDOWS - 1) + max(WINDOW_DAYS, NOISE_DAYS))
        self.tracked = {i.id: i for i in db.query(PriceItem).filter(PriceItem.restaurant_id == restaurant_id, PriceItem.track_usage.is_(True))}
        self.suppliers = {s.id: s.name for s in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id)}
        # What sold, by POS item and day; and what was made then voided, which used the food all the same.
        self.sold: dict[str, dict[date, Decimal]] = {}
        self.net: dict[str, dict[date, Decimal]] = {}
        self.names: dict[str, str] = {}
        for pos, name, day, qty, net in db.query(SalesItem.pos_item_id, SalesItem.pos_item_name, SalesItem.date, func.sum(SalesItem.qty), func.sum(SalesItem.net)).filter(
                SalesItem.restaurant_id == restaurant_id, SalesItem.date >= first, SalesItem.date <= self.end).group_by(
                SalesItem.pos_item_id, SalesItem.pos_item_name, SalesItem.date):
            self.sold.setdefault(pos, {})[day] = self.sold.get(pos, {}).get(day, Decimal(0)) + _dec(qty or 0)
            self.net.setdefault(pos, {})[day] = self.net.get(pos, {}).get(day, Decimal(0)) + _dec(net or 0)
            self.names[pos] = name
        self.fired: dict[str, dict[date, Decimal]] = {}
        for pos, day, qty in db.query(SalesOrderLine.pos_item_id, SalesOrderLine.business_date, func.sum(SalesOrderLine.qty)).filter(
                SalesOrderLine.restaurant_id == restaurant_id, SalesOrderLine.voided_after_fire.is_(True), SalesOrderLine.business_date >= first,
                SalesOrderLine.business_date <= self.end).group_by(SalesOrderLine.pos_item_id, SalesOrderLine.business_date):
            self.fired.setdefault(pos, {})[day] = _dec(qty or 0)
        # One confirmed dish per POS item: its usage a portion, and whether its cost is complete (for coverage).
        self.dishes: dict[str, dict] = {}
        for recipe in sorted(self.world.recipes.values(), key=lambda r: (r.name, r.id)):
            row = self.world.rows[recipe.id]
            if not recipe.confirmed or recipe.kind != "menu_item" or not row.pos_item_id or row.pos_item_id in self.dishes:
                continue
            self.dishes[row.pos_item_id] = {"recipe_id": recipe.id, "name": recipe.name, "use": costing.usage(recipe, self.world.recipes, self.world.items),
                                            "complete": self.world.cost(recipe.id).complete}

    def between(self, by_day: dict[date, Decimal] | None, start: date, end: date) -> Decimal:
        return sum((v for d, v in (by_day or {}).items() if start <= d <= end), Decimal(0))


def compute_window(inputs: Inputs, end: date, limits: dict) -> tuple[dict[str, tuple[Variance, dict]], Decimal, str, dict]:
    """Every tracked ingredient for the 28 business dates ending `end`: (variance, basis) by item, the recipe
    coverage, the state it gives, and what the coverage rests on."""
    start = end - timedelta(days=WINDOW_DAYS - 1)
    world = inputs.world
    total_net = sum((inputs.between(days, start, end) for days in inputs.net.values()), Decimal(0))
    covered_net = sum((inputs.between(inputs.net.get(pos), start, end) for pos, dish in inputs.dishes.items() if dish["complete"]), Decimal(0))
    coverage = usage_math.coverage(covered_net, total_net)
    state = usage_math.state_for(coverage, limits["coverage_learning"], limits["coverage_live"])

    theoretical: dict[str, Decimal] = {}
    by_dish: dict[str, list[dict]] = {}
    for pos, dish in inputs.dishes.items():
        sold = inputs.between(inputs.sold.get(pos), start, end) + inputs.between(inputs.fired.get(pos), start, end)
        for item_id, per_portion in dish["use"].items():
            if item_id not in inputs.tracked:
                continue
            expected = sold * per_portion
            theoretical[item_id] = theoretical.get(item_id, Decimal(0)) + expected
            by_dish.setdefault(item_id, []).append({"recipe_id": dish["recipe_id"], "name": dish["name"], "pos_item_id": pos, "sold": str(sold),
                                                    "per_portion": str(costing._trim(per_portion)), "expected": str(expected.quantize(QTY))})
    out: dict[str, tuple[Variance, dict]] = {}
    noise_from = end - timedelta(days=NOISE_DAYS - 1)
    for item_id, expected in theoretical.items():
        points = [p for p in world.points.get(item_id, []) if not p.outlier]
        left_out = sum(1 for p in world.points.get(item_id, []) if p.outlier and start <= p.day <= end)
        bought = [p for p in points if start <= p.day <= end]
        credits = sum((p.credited / p.unit_price for p in bought if p.credited), Decimal(0))  # dollars credited back, as a quantity
        purchased = sum((p.qty for p in bought), Decimal(0)) - credits
        per_delivery: dict[str, Decimal] = {}
        for p in points:
            if noise_from <= p.day <= end:
                per_delivery[p.invoice_id] = per_delivery.get(p.invoice_id, Decimal(0)) + p.qty
        quantity = sum((p.qty for p in bought if p.qty > 0), Decimal(0))
        if quantity > 0:
            price = sum((p.unit_price * p.qty for p in bought if p.qty > 0), Decimal(0)) / quantity
        else:
            latest = price_points.price_as_of(world.points.get(item_id), end, "latest")
            price = latest.unit_price if latest is not None else Decimal(0)
        v = usage_math.variance(expected.quantize(QTY), purchased.quantize(QTY), usage_math.noise(list(per_delivery.values())).quantize(QTY),
                                price.quantize(Decimal("0.00000001")))
        dishes = sorted(by_dish.get(item_id, []), key=lambda d: -Decimal(d["expected"]))
        last = max(bought, key=lambda p: p.key) if bought else (max((p for p in points if p.day <= end), key=lambda p: p.key) if any(p.day <= end for p in points) else None)
        basis = {"dishes": dishes, "deliveries_in_window": len({p.invoice_id for p in bought}), "noise_deliveries": len(per_delivery), "credits_qty": str(credits.quantize(QTY)),
                 "prices_left_out": left_out, "coverage": {"covered_net": str(covered_net), "total_net": str(total_net)},
                 "top_dish": dishes[0] if dishes else None,
                 "last_delivery": None if last is None else {"invoice_id": last.invoice_id, "day": last.day.isoformat(), "qty": str(last.qty.quantize(QTY)),
                                                             "supplier": inputs.suppliers.get(last.supplier_id)}}
        out[item_id] = (v, basis)
    return out, coverage, state, {"covered_net": covered_net, "total_net": total_net}


def run(db: Session, restaurant_id: str, now: datetime | None = None) -> dict:
    """Rewrite the last eight weekly windows, then open and close findings for the newest. Idempotent."""
    inputs = Inputs(db, restaurant_id, now)
    limits = thresholds(db, restaurant_id)
    ends = [inputs.end - timedelta(days=7 * k) for k in range(WINDOWS)]
    held = {(r.canonical_item_id, r.window_end): r for r in db.query(UsageVariance).filter(UsageVariance.restaurant_id == restaurant_id, UsageVariance.window_end.in_(ends))}
    seen: set[tuple[str, date]] = set()
    states: dict[date, str] = {}
    results: dict[date, dict[str, tuple[Variance, dict]]] = {}
    for end in ends:
        results[end], coverage, states[end], _ = compute_window(inputs, end, limits)
        for item_id, (v, basis) in results[end].items():
            values = {"window_start": end - timedelta(days=WINDOW_DAYS - 1), "theoretical_qty": v.theoretical_qty, "purchased_qty": v.purchased_qty,
                      "noise_qty": v.noise_qty, "unexplained_qty": v.unexplained_qty, "avg_unit_price": v.avg_unit_price, "weekly_dollars": v.weekly_dollars,
                      "recipe_coverage": coverage, "state": states[end], "basis": basis, "calc_version": CALC_VERSION}
            row = held.get((item_id, end))
            if row is None:
                row = UsageVariance(restaurant_id=restaurant_id, canonical_item_id=item_id, window_end=end, **values)
                db.add(row)
                held[(item_id, end)] = row
            else:
                for name, value in values.items():
                    if getattr(row, name) != value:
                        setattr(row, name, value)
            seen.add((item_id, end))
    for key, row in list(held.items()):
        if key not in seen:  # an ingredient no confirmed recipe uses any more
            db.delete(row)
            del held[key]
    db.flush()
    opened = _findings(db, inputs, limits, results, states, held)
    db.flush()
    events.emit(db, restaurant_id, "usage_variance.computed", "usage_variance", inputs.end.isoformat(),
                {"items": len(results[inputs.end]), "state": states[inputs.end], "open_findings": opened})
    return {"window_end": inputs.end.isoformat(), "items": len(results[inputs.end]), "state": states[inputs.end], "open_findings": opened}


def _findings(db: Session, inputs: Inputs, limits: dict, results: dict, states: dict, rows: dict) -> int:
    """A `usage_variance` finding needs the gap on two consecutive weekly runs with the module live; a data gap
    opens `usage_data_gap` instead. Either closes when it stops being true."""
    rid = inputs.world.restaurant_id
    end, before = inputs.end, inputs.end - timedelta(days=7)
    min_pct, min_dollars, gap_pct = limits["usage_min_pct_of_expected"], limits["usage_min_weekly_dollars"], limits["usage_data_gap_pct"]
    live = states[end] == "live"
    existing: dict[tuple[str, str], Finding] = {}
    for finding in db.query(Finding).filter(Finding.restaurant_id == rid, Finding.type.in_((VARIANCE, DATA_GAP)), Finding.status.in_(("open", "dismissed"))):
        existing[(finding.type, finding.subject_id)] = finding
    wanted: dict[tuple[str, str], tuple[str, Decimal, dict]] = {}
    for item_id, (v, basis) in results[end].items():
        name = inputs.tracked[item_id].name
        unit = inputs.world.items[item_id].base_unit
        row = rows[(item_id, end)]
        checks = {"top_dish": basis["top_dish"], "last_delivery": basis["last_delivery"]}
        common = {"row_id": row.id, "window_start": row.window_start.isoformat(), "window_end": end.isoformat(), "base_unit": unit, "checks": checks,
                  "expected": str(v.theoretical_qty), "purchased": str(v.purchased_qty), "noise": str(v.noise_qty), "unexplained": str(v.unexplained_qty)}
        if live and v.data_gap(gap_pct):
            wanted[(DATA_GAP, item_id)] = (f"{name}: recipes used more than was bought, last 28 days", Decimal(0), common)
            continue
        earlier = results[before].get(item_id)
        if live and states[before] == "live" and v.qualifies(min_pct, min_dollars) and earlier is not None and earlier[0].qualifies(min_pct, min_dollars):
            wanted[(VARIANCE, item_id)] = (f"{name}: {words.dollars(v.weekly_dollars)} a week bought beyond what recipes used", v.weekly_dollars, common)
    now = datetime.now(timezone.utc)
    for key, finding in existing.items():
        if key not in wanted:
            finding.status, finding.closed_at, finding.note = "closed", now, finding.note or "No longer true in the latest 28 days."
    opened = 0
    for (kind, item_id), (title, dollars, basis) in wanted.items():
        finding = existing.get((kind, item_id))
        if finding is None:
            db.add(Finding(restaurant_id=rid, type=kind, subject_table="price_items", subject_id=item_id, title=title[:200], weekly_dollars=dollars, basis=basis))
            opened += 1
        else:  # still true: refreshed, and a dismissed one stays dismissed
            finding.title, finding.weekly_dollars, finding.basis = title[:200], dollars, basis
            opened += 1 if finding.status == "open" else 0
    return opened
