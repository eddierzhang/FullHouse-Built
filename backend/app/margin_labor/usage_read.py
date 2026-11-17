"""What the Usage pages read (spec sections 10.8 B and 11): the newest window's rows, eight weeks of one
ingredient, and what still needs a recipe before the numbers can be trusted."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.margin_labor import usage, words
from app.margin_labor.models import Finding, UsageVariance
from app.margin_labor.usage import DATA_GAP, VARIANCE, WINDOWS, Inputs
from app.margin_labor.usage_math import WINDOW_DAYS
from app.sales.models import SalesItem

STALE_SALES_DAYS = 10  # a weekly window with no sales in its last week is out of date


def _s(value) -> str | None:
    return None if value is None else str(value)


def _ensure(db: Session, restaurant_id: str, inputs: Inputs) -> None:
    newest = db.query(func.max(UsageVariance.window_end)).filter(UsageVariance.restaurant_id == restaurant_id).scalar()
    if newest != inputs.end:
        # Nothing computed for the newest complete week (a new week began, or the module was just switched on).
        usage.run(db, restaurant_id)
        db.commit()


def _missing(inputs: Inputs, limits: dict) -> tuple[list[dict], int]:
    """Top-selling POS items with no confirmed, fully priced recipe, and how many of them would reach live coverage."""
    start = inputs.end - timedelta(days=WINDOW_DAYS - 1)
    net = {pos: inputs.between(days, start, inputs.end) for pos, days in inputs.net.items()}
    total = sum(net.values(), Decimal(0))
    covered = sum((v for pos, v in net.items() if pos in inputs.dishes and inputs.dishes[pos]["complete"]), Decimal(0))
    missing = sorted(((pos, v) for pos, v in net.items() if v > 0 and not (pos in inputs.dishes and inputs.dishes[pos]["complete"])), key=lambda kv: -kv[1])
    needed, reach = 0, covered
    for _pos, value in missing:
        if total <= 0 or reach / total >= limits["coverage_live"]:
            break
        reach += value
        needed += 1
    return ([{"pos_item_id": pos, "name": inputs.names.get(pos, pos), "net": str(v), "share": _s((v / total).quantize(Decimal("0.000001"))) if total > 0 else None,
              "has_recipe": pos in inputs.dishes} for pos, v in missing[:10]], needed)


def overview(db: Session, restaurant_id: str, now: datetime | None = None) -> dict:
    inputs = Inputs(db, restaurant_id, now)
    _ensure(db, restaurant_id, inputs)
    limits = usage.thresholds(db, restaurant_id)
    end, before = inputs.end, inputs.end - timedelta(days=7)
    rows = db.query(UsageVariance).filter(UsageVariance.restaurant_id == restaurant_id, UsageVariance.window_end >= end - timedelta(days=7 * (WINDOWS - 1))).all()
    by_item: dict[str, dict] = {}
    for row in rows:
        by_item.setdefault(row.canonical_item_id, {})[row.window_end] = row
    current = [r for r in rows if r.window_end == end]
    _, coverage, state, _ = usage.compute_window(inputs, end, limits)
    _, coverage_before, _, _ = usage.compute_window(inputs, before, limits)
    last_sale = db.query(func.max(SalesItem.date)).filter(SalesItem.restaurant_id == restaurant_id).scalar()
    missing, needed = _missing(inputs, limits)
    out = {"state": state, "coverage": str(coverage), "coverage_then": str(coverage_before), "coverage_learning": str(limits["coverage_learning"]),
           "coverage_live": str(limits["coverage_live"]), "window_start": end - timedelta(days=WINDOW_DAYS - 1), "window_end": end, "today": inputs.world.today,
           "computed_at": max((r.updated_at for r in current), default=None), "stale": bool(last_sale is None or (inputs.world.today - last_sale).days > STALE_SALES_DAYS),
           "last_sales_date": last_sale, "has_sales": bool(inputs.net), "missing_recipes": missing, "recipes_needed": needed,
           "items": [], "header": None, "tiles": None, "more_under": None}
    if state == "setup":
        return out  # no variance numbers until the recipes cover enough of what sells
    findings = {f.subject_id: f for f in db.query(Finding).filter(Finding.restaurant_id == restaurant_id, Finding.type.in_((VARIANCE, DATA_GAP)), Finding.status == "open")}
    min_pct, min_dollars = limits["usage_min_pct_of_expected"], limits["usage_min_weekly_dollars"]

    def material(row: UsageVariance | None) -> bool:
        return (row is not None and row.unexplained_qty > 0 and row.theoretical_qty > 0 and row.unexplained_qty / row.theoretical_qty >= min_pct
                and row.weekly_dollars >= min_dollars)

    listed, small = [], 0
    for row in current:
        finding = findings.get(row.canonical_item_id)
        if finding is None and row.weekly_dollars < limits["usage_collapse_under_dollars"]:
            small += 1 if row.weekly_dollars > 0 else 0
            continue
        weeks = by_item[row.canonical_item_id]
        listed.append({
            "item_id": row.canonical_item_id, "row_id": row.id, "name": inputs.tracked[row.canonical_item_id].name if row.canonical_item_id in inputs.tracked else "Item",
            "base_unit": inputs.world.items[row.canonical_item_id].base_unit, "expected": str(row.theoretical_qty), "purchased": str(row.purchased_qty),
            "unexplained": str(row.unexplained_qty), "pct": _s((row.unexplained_qty / row.theoretical_qty).quantize(Decimal("0.000001"))) if row.theoretical_qty > 0 else None,
            "weekly_dollars": str(row.weekly_dollars), "material": material(row),
            "trend": [_s(weeks[e].weekly_dollars) if e in weeks else None for e in sorted(end - timedelta(days=7 * k) for k in range(WINDOWS))],
            "finding": None if finding is None else {"id": finding.id, "type": finding.type, "checks": (finding.basis or {}).get("checks")},
        })
    listed.sort(key=lambda r: (-Decimal(r["weekly_dollars"]), r["name"]))
    flagged = [r for r in current if material(r)]
    flagged_then = [r for r in rows if r.window_end == before and material(r)]
    out["items"] = listed
    out["more_under"] = {"count": small, "threshold": str(limits["usage_collapse_under_dollars"])} if small else None
    out["header"] = {"weekly_dollars": str(sum((r.weekly_dollars for r in flagged), Decimal(0))), "items": len(flagged)}
    out["tiles"] = {"unexplained": {"value": out["header"]["weekly_dollars"], "then": str(sum((r.weekly_dollars for r in flagged_then), Decimal(0)))},
                    "coverage": {"value": str(coverage), "then": str(coverage_before), "needed": str(limits["coverage_live"])},
                    "flagged": {"value": len(findings), "then": len(flagged_then)}}
    return out


def detail(db: Session, restaurant_id: str, item_id: str, now: datetime | None = None) -> dict | None:
    inputs = Inputs(db, restaurant_id, now)
    if item_id not in inputs.world.items:
        return None
    _ensure(db, restaurant_id, inputs)
    limits = usage.thresholds(db, restaurant_id)
    rows = db.query(UsageVariance).filter(UsageVariance.restaurant_id == restaurant_id, UsageVariance.canonical_item_id == item_id).order_by(UsageVariance.window_end).all()
    rows = rows[-WINDOWS:]
    current = rows[-1] if rows and rows[-1].window_end == inputs.end else None
    _, coverage, state, _ = usage.compute_window(inputs, inputs.end, limits)
    finding = db.query(Finding).filter(Finding.restaurant_id == restaurant_id, Finding.type.in_((VARIANCE, DATA_GAP)), Finding.subject_id == item_id, Finding.status == "open").first()
    item = inputs.world.items[item_id]
    tracked = item_id in inputs.tracked
    return {
        "state": state if tracked else "live", "coverage": str(coverage), "today": inputs.world.today, "computed_at": current.updated_at if current else None, "stale": False,
        "item": {"id": item_id, "name": item.name, "base_unit": item.base_unit, "track_usage": tracked},
        "current": None if current is None or state == "setup" else {
            "row_id": current.id, "window_start": current.window_start, "window_end": current.window_end, "expected": str(current.theoretical_qty),
            "purchased": str(current.purchased_qty), "noise": str(current.noise_qty), "unexplained": str(current.unexplained_qty),
            "pct": _s((current.unexplained_qty / current.theoretical_qty).quantize(Decimal("0.000001"))) if current.theoretical_qty > 0 else None,
            "weekly_dollars": str(current.weekly_dollars), "avg_unit_price": str(current.avg_unit_price.normalize()),
            "deliveries": current.basis.get("deliveries_in_window"), "prices_left_out": current.basis.get("prices_left_out", 0)},
        "windows": [] if state == "setup" else [{"window_end": r.window_end, "expected": str(r.theoretical_qty), "purchased": str(r.purchased_qty), "noise": str(r.noise_qty),
                                                 "unexplained": str(r.unexplained_qty), "weekly_dollars": str(r.weekly_dollars)} for r in rows],
        "dishes": [] if current is None or state == "setup" else current.basis.get("dishes", []),
        "finding": None if finding is None else {"id": finding.id, "type": finding.type, "title": finding.title, "weekly_dollars": str(finding.weekly_dollars),
                                                 "checks": (finding.basis or {}).get("checks")},
        "checks": None if current is None else {"top_dish": current.basis.get("top_dish"), "last_delivery": current.basis.get("last_delivery")},
    }


def why_row(db: Session, row: UsageVariance, base_unit: str) -> dict:
    """The stored basis of one window's row, for the panel."""
    b = row.basis or {}
    amount = lambda q: f"{words.qty(q)} {base_unit}"  # noqa: E731
    dishes = "; ".join(f"{d['name']} {words.qty(d['sold'])} sold x {words.qty(d['per_portion'])} {base_unit}" for d in b.get("dishes", [])[:4])
    inputs = [
        {"label": "Expected use", "value": amount(row.theoretical_qty), "source": {"kind": "text", "label": dishes or "Recipes and sales"}},
        {"label": "Purchased", "value": amount(row.purchased_qty), "source": {"kind": "invoice", "label": f"{b.get('deliveries_in_window', 0)} deliveries"}},
        {"label": "One typical delivery", "value": amount(row.noise_qty), "source": {"kind": "text", "label": f"Median of {b.get('noise_deliveries', 0)} deliveries, last 8 weeks"}},
        {"label": "Average price", "value": words.unit_price(row.avg_unit_price, base_unit), "source": {"kind": "invoice", "label": "Invoices in the window"}},
    ]
    return {"result": {"value": str(row.weekly_dollars), "kind": "dollars", "label": "a week"},
            "formula": "Unexplained use = purchased - expected use - one typical delivery. Dollars a week = unexplained use x average price / 4",
            "inputs": inputs,
            "data_basis": f"28 days of sales to {words.day(row.window_end)}, {b.get('deliveries_in_window', 0)} deliveries, recipe coverage {words.percent(row.recipe_coverage)}",
            "computed_at": row.updated_at, "inputs_changed_on": row.window_end.isoformat(),
            "fix": {"label": "Review recipe", "route": f"menu-costs/{b['top_dish']['recipe_id']}"} if b.get("top_dish") else None}
