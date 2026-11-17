"""What the Menu costs pages read (spec sections 10.8 A and 11). Reads only: current `dish_costs` rows, open
findings, and the same pure costing run at earlier dates for trends."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.agents.models import AgentAction
from app.margin_labor import costing, menu_costs, price_points, words
from app.margin_labor.menu_costs import FINDING, WINDOW_DAYS, World
from app.margin_labor.models import DishCost, Finding
from app.prices.models import Invoice
from app.restaurant.models import Supplier
from app.sales.models import SalesItem

STALE_SALES_DAYS = 3
TREND_WEEKS = 12
HISTORY_WEEKS = 26


def _s(value) -> str | None:
    return None if value is None else str(value)


def _state(db: Session, world: World, current: dict[str, DishCost]) -> tuple[str, list[dict]]:
    """setup until at least one dish has a full cost; then live. The steps are what is missing."""
    rid = world.restaurant_id
    invoices = db.query(func.count(Invoice.id)).filter(Invoice.restaurant_id == rid, Invoice.status == "complete").scalar() or 0
    dishes = [r for r in world.recipes.values() if r.confirmed and r.kind == "menu_item"]
    costed = [r for r in dishes if r.id in current and current[r.id].lines_total and current[r.id].lines_priced == current[r.id].lines_total]
    priced = [r for r in costed if current[r.id].menu_price is not None]
    steps = [
        {"key": "invoices", "done": invoices > 0, "count": invoices},
        {"key": "recipes", "done": bool(dishes), "count": len(dishes)},
        {"key": "costed", "done": bool(costed), "count": len(costed)},
        {"key": "prices", "done": bool(priced), "count": len(priced)},
    ]
    return ("live" if costed else "setup"), steps


def _ensure_costs(db: Session, world: World) -> dict[str, DishCost]:
    current = {c.recipe_id: c for c in db.query(DishCost).filter(DishCost.restaurant_id == world.restaurant_id, DishCost.is_current.is_(True))}
    if any(r.confirmed and r.id not in current for r in world.recipes.values()):
        # A recipe confirmed before the module was switched on has no row yet: derived data is recomputable, so compute it.
        menu_costs.recost(db, world.restaurant_id)
        db.commit()
        current = {c.recipe_id: c for c in db.query(DishCost).filter(DishCost.restaurant_id == world.restaurant_id, DishCost.is_current.is_(True))}
    return current


def _freshness(db: Session, world: World, current: dict[str, DishCost]) -> dict:
    last_sale = db.query(func.max(SalesItem.date)).filter(SalesItem.restaurant_id == world.restaurant_id).scalar()
    stale = last_sale is not None and (world.today - last_sale).days > STALE_SALES_DAYS
    computed = max((c.effective_at for c in current.values()), default=None)
    return {"computed_at": computed, "stale": bool(stale), "last_sales_date": last_sale}


def overview(db: Session, restaurant_id: str, now: datetime | None = None) -> dict:
    world = menu_costs.load(db, restaurant_id, now)
    current = _ensure_costs(db, world)
    state, steps = _state(db, world, current)
    fresh = _freshness(db, world, current)
    out = {"state": state, "setup": steps, "target": str(world.conf.target_food_cost_pct), "today": world.today, **fresh, "dishes": [],
           "header": None, "tiles": None, "left_out": len(price_points.outliers(world.points, world.today - timedelta(days=90)))}
    if state == "setup":
        return out
    sold = menu_costs.units_sold(db, world)
    findings = {f.subject_id: f for f in db.query(Finding).filter(Finding.restaurant_id == restaurant_id, Finding.type == FINDING, Finding.status == "open")}
    proposals = {a.id: a for a in db.query(AgentAction).filter(AgentAction.id.in_([f.proposal_id for f in findings.values() if f.proposal_id] or [""]))}
    rows = []
    cost_sum = sales_sum = cost_then_sum = Decimal(0)
    for recipe in world.recipes.values():
        cost = current.get(recipe.id)
        if not recipe.confirmed or recipe.kind != "menu_item" or cost is None:
            continue
        row = world.rows[recipe.id]
        units = sold.get(row.pos_item_id or "", Decimal(0))
        partial = cost.lines_priced != cost.lines_total
        then = None if partial else menu_costs.cost_then(world, recipe.id)
        trend = []
        for back in range(TREND_WEEKS - 1, -1, -1):
            at = world.cost(recipe.id, world.today - timedelta(days=7 * back) if back else None)
            trend.append(_s(at.plate_cost) if at.complete else None)
        finding = findings.get(recipe.id)
        proposal = proposals.get(finding.proposal_id) if finding is not None and finding.proposal_id else None
        if not partial and cost.menu_price is not None and units > 0:
            cost_sum += cost.plate_cost * units
            sales_sum += cost.menu_price * units
            cost_then_sum += (then if then is not None else cost.plate_cost) * units
        rows.append({
            "recipe_id": recipe.id, "name": recipe.name, "dish_cost_id": cost.id, "price": _s(cost.menu_price), "plate_cost": str(cost.plate_cost),
            "partial": partial, "unpriced": cost.unpriced, "food_cost_pct": _s(cost.food_cost_pct), "margin": _s(cost.margin_dollars),
            "change_28": None if then is None else str(cost.plate_cost - then), "sold_28": str(units), "trend": trend,
            "target": str(menu_costs.target_for(db, world, row)),
            "finding": None if finding is None else {"id": finding.id, "weekly_dollars": str(finding.weekly_dollars), "opened_at": finding.opened_at,
                                                     "proposal_id": finding.proposal_id, "proposal_status": proposal.status if proposal else None,
                                                     "suggested_price": (proposal.payload or {}).get("suggested_price") if proposal else None,
                                                     "food_cost_pct_at_suggested": (proposal.payload or {}).get("food_cost_pct_at_suggested") if proposal else None,
                                                     "cap_applied": bool((proposal.payload or {}).get("cap_applied")) if proposal else False},
        })
    rows.sort(key=lambda r: (-(Decimal(r["finding"]["weekly_dollars"]) if r["finding"] else Decimal(-1)), -Decimal(r["food_cost_pct"] or 0), r["name"]))
    over = [r for r in rows if r["finding"]]
    then_moment = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    over_then = db.query(func.count(Finding.id)).filter(Finding.restaurant_id == restaurant_id, Finding.type == FINDING, Finding.opened_at <= then_moment,
                                                       (Finding.closed_at.is_(None)) | (Finding.closed_at > then_moment), Finding.status != "dismissed").scalar() or 0
    out["dishes"] = rows
    out["header"] = {"dishes_over": len(over), "weekly_dollars": str(sum((Decimal(r["finding"]["weekly_dollars"]) for r in over), Decimal(0))),
                     "dishes": len(rows)}
    out["tiles"] = {
        "dishes_over": {"value": len(over), "then": over_then, "of": len(rows)},
        "average_food_cost": {"value": _s((cost_sum / sales_sum).quantize(costing.FOUR)) if sales_sum > 0 else None,
                              "then": _s((cost_then_sum / sales_sum).quantize(costing.FOUR)) if sales_sum > 0 else None},
        "largest_increase": _largest_increase(world),
    }
    return out


def _largest_increase(world: World) -> dict | None:
    """The ingredient, among those in confirmed recipes, whose unit price rose most over the last 28 days."""
    best = None
    then_day = world.today - timedelta(days=WINDOW_DAYS)
    used = {l.item_id for r in world.recipes.values() if r.confirmed for l in r.lines if l.item_id}
    for item_id in sorted(used):
        now, then = world.price_of()(item_id), world.price_of(then_day)(item_id)
        if now is None or then is None or then.unit_price <= 0 or now.unit_price <= then.unit_price:
            continue
        rise = (now.unit_price - then.unit_price) / then.unit_price
        if best is None or rise > best[0]:
            best = (rise, item_id, now, then)
    if best is None:
        return None
    rise, item_id, now, then = best
    item = world.items[item_id]
    return {"item_id": item_id, "name": item.name, "pct": str(rise.quantize(costing.FOUR)), "base_unit": item.base_unit,
            "now": str(costing._trim(now.unit_price)), "then": str(costing._trim(then.unit_price)), "since": then.day, "invoice_id": now.invoice_id}


def detail(db: Session, restaurant_id: str, recipe_id: str, now: datetime | None = None) -> dict | None:
    world = menu_costs.load(db, restaurant_id, now)
    if recipe_id not in world.recipes:
        return None
    current = _ensure_costs(db, world)
    recipe, row, cost = world.recipes[recipe_id], world.rows[recipe_id], current.get(recipe_id)
    then_day = world.today - timedelta(days=WINDOW_DAYS)
    then = {b["line_id"]: b for b in world.cost(recipe_id, then_day).basis}
    suppliers = {s.id: s.name for s in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id)}
    lines = []
    for b in (cost.basis.get("lines", []) if cost is not None else []):
        before = then.get(b["line_id"])
        change = (Decimal(b["line_cost"]) - Decimal(before["line_cost"])) if b.get("priced") and before and before.get("priced") else None
        lines.append({**b, "supplier": suppliers.get(b.get("supplier_id") or ""), "change_28": _s(change)})
    lines.sort(key=lambda b: -Decimal(b.get("line_cost") or 0))

    history, previous = [], None
    price_rows = db.query(DishCost.effective_at, DishCost.menu_price).filter(DishCost.restaurant_id == restaurant_id, DishCost.recipe_id == recipe_id).order_by(DishCost.effective_at).all()
    for back in range(HISTORY_WEEKS - 1, -1, -1):
        day = world.today - timedelta(days=7 * back)
        at = world.cost(recipe_id, day if back else None)
        prices = {b["item_id"]: b["unit_price"] for b in at.basis if b.get("priced") and b.get("item_id")}
        moved = sorted(world.items[i].name for i, p in prices.items() if previous is not None and i in previous and previous[i] != p)
        end = datetime.combine(day, datetime.max.time(), tzinfo=timezone.utc)
        price = next((p for at_, p in reversed(price_rows) if at_ <= end), cost.menu_price if cost is not None else None)
        history.append({"week": day, "plate_cost": _s(at.plate_cost) if at.complete else None, "menu_price": _s(price), "changed": moved})
        previous = prices
    finding = db.query(Finding).filter(Finding.restaurant_id == restaurant_id, Finding.type == FINDING, Finding.subject_id == recipe_id, Finding.status == "open").first()
    proposal = db.get(AgentAction, finding.proposal_id) if finding is not None and finding.proposal_id else None
    used = {l.item_id for l in recipe.lines if l.item_id}
    left_out = [{"item": world.items[p.item_id].name, "unit_price": str(costing._trim(p.unit_price)), "median": _s(costing._trim(p.median)) if p.median else None,
                 "base_unit": world.items[p.item_id].base_unit, "day": p.day, "invoice_id": p.invoice_id}
                for p in price_points.outliers({i: world.points.get(i, []) for i in used}, world.today - timedelta(days=90))]
    sold = menu_costs.units_sold(db, world).get(row.pos_item_id or "", Decimal(0))
    return {
        "state": "live" if cost is not None else "setup", "today": world.today, "computed_at": cost.effective_at if cost is not None else None,
        "stale": _freshness(db, world, current)["stale"], "target": str(menu_costs.target_for(db, world, row)),
        "recipe": {"id": recipe.id, "name": recipe.name, "kind": recipe.kind, "portions": str(recipe.portions), "yield_qty": str(recipe.yield_qty),
                   "yield_unit": recipe.yield_unit, "confirmed": recipe.confirmed, "pos_item_id": row.pos_item_id,
                   "lines": [{"id": l.id, "name": l.name, "qty": str(l.qty), "unit": l.unit, "yield_pct": str(l.yield_pct), "item_id": l.item_id,
                              "sub_recipe_id": l.sub_recipe_id} for l in recipe.lines]},
        "cost": None if cost is None else {"id": cost.id, "plate_cost": str(cost.plate_cost), "batch_cost": str(cost.batch_cost), "menu_price": _s(cost.menu_price),
                                           "food_cost_pct": _s(cost.food_cost_pct), "margin": _s(cost.margin_dollars), "lines_priced": cost.lines_priced,
                                           "lines_total": cost.lines_total, "unpriced": cost.unpriced, "price_source": cost.basis.get("menu_price", {}).get("label")},
        "sold_28": str(sold), "lines": lines, "history": history, "left_out": left_out,
        "finding": None if finding is None else {"id": finding.id, "weekly_dollars": str(finding.weekly_dollars), "opened_at": finding.opened_at, "title": finding.title},
        "proposal": None if proposal is None else {"id": proposal.id, "status": proposal.status, **{k: (proposal.payload or {}).get(k) for k in (
            "suggested_price", "price_at_target", "price_cap", "cap_applied", "food_cost_pct_at_suggested", "menu_price", "note", "weekly_dollars")}},
    }


# --- the Why panel ------------------------------------------------------------------------------------


def why_dish_cost(db: Session, cost: DishCost, figure: str = "plate_cost") -> dict:
    """The stored basis of one cost row, as the panel shows it. Nothing is recomputed."""
    lines = cost.basis.get("lines", [])
    inputs = []
    latest_price = None
    for b in lines:
        if not b.get("priced"):
            inputs.append({"label": b["name"], "value": f"Not priced: {b.get('reason', 'no price')}", "source": None})
            continue
        if b["kind"] == "sub_recipe":
            value = (f"{words.qty(b['qty_in_yield_unit'])} {b['sub_yield_unit']} x {words.unit_price(b['unit_price'], b['sub_yield_unit'])} = {words.dollars(b['line_cost'])}")
            inputs.append({"label": b["name"], "value": value, "source": {"kind": "recipe", "id": b["sub_recipe_id"], "label": "Sub-recipe"}})
            continue
        waste = "" if Decimal(b["yield_pct"]) == 1 else f" / {words.qty(b['yield_pct'])} yield"
        value = f"{words.qty(b['qty_in_base'])} {b['base_unit']}{waste} x {words.unit_price(b['unit_price'], b['base_unit'])} = {words.dollars(b['line_cost'])}"
        if b.get("price_date") and (latest_price is None or b["price_date"] > latest_price):
            latest_price = b["price_date"]
        inputs.append({"label": b["name"], "value": value, "source": {"kind": "invoice", "id": b.get("invoice_id"), "label": "Invoice", "date": b.get("price_date")}})
    policy = "its latest price" if cost.basis.get("policy") != "avg_30d" else "its average price over 30 days"
    portions = cost.basis.get("portions", "1")
    formula = f"Cost per plate = sum of each ingredient's quantity x {policy}" + ("" if Decimal(portions) == 1 else f", divided by {words.qty(portions)} portions")
    result = {"value": str(cost.plate_cost), "kind": "dollars", "label": "a plate"}
    price = cost.basis.get("menu_price", {})
    if figure == "food_cost_pct" and cost.food_cost_pct is not None:
        formula = "Food cost = cost per plate / menu price, before tax"
        result = {"value": str(cost.food_cost_pct), "kind": "percent", "label": "of the menu price"}
        inputs = [{"label": "Cost per plate", "value": words.dollars(cost.plate_cost), "source": None},
                  {"label": "Menu price", "value": words.dollars(cost.menu_price), "source": {"kind": "text", "label": price.get("label")}}]
    elif figure == "margin" and cost.margin_dollars is not None:
        formula = "Margin = menu price, before tax - cost per plate"
        result = {"value": str(cost.margin_dollars), "kind": "dollars", "label": "a plate"}
        inputs = [{"label": "Menu price", "value": words.dollars(cost.menu_price), "source": {"kind": "text", "label": price.get("label")}},
                  {"label": "Cost per plate", "value": words.dollars(cost.plate_cost), "source": None}]
    invoices = len({b.get("invoice_id") for b in lines if b.get("invoice_id")})
    return {"result": result, "formula": formula, "inputs": inputs,
            "data_basis": f"{cost.lines_priced} of {cost.lines_total} recipe lines priced, from {invoices} invoice{'s' if invoices != 1 else ''}",
            "computed_at": cost.effective_at, "inputs_changed_on": latest_price, "fix": {"label": "Edit recipe", "route": f"menu-costs/{cost.recipe_id}"}}


def why_finding(db: Session, finding: Finding) -> dict:
    b = finding.basis or {}
    inputs = []
    if finding.type == FINDING:
        inputs = [
            {"label": "Cost per plate now", "value": words.dollars(b["plate_cost_now"]), "source": None},
            {"label": "Cost per plate 28 days ago", "value": words.dollars(b["plate_cost_28_days_ago"]) if b.get("plate_cost_28_days_ago") else "Not priced then", "source": None},
            {"label": "Sold in 28 days", "value": words.qty(b["units_sold_28_days"]), "source": {"kind": "text", "label": "Point of sale"}},
        ]
        fix = {"label": "Edit recipe", "route": f"menu-costs/{finding.subject_id}"}
        formula = "Dollars a week = (cost per plate now - cost per plate 28 days ago) x units sold in 28 days / 4"
        basis = "28 days of sales and the invoice prices on both dates"
    else:
        formula = b.get("formula", "")
        inputs = [{"label": i["label"], "value": i["value"], "source": i.get("source")} for i in b.get("inputs", [])]
        fix = b.get("fix")
        basis = b.get("data_basis", "")
    return {"result": {"value": str(finding.weekly_dollars), "kind": "dollars", "label": "a week"}, "formula": formula, "inputs": inputs, "data_basis": basis,
            "computed_at": finding.updated_at, "inputs_changed_on": b.get("as_of"), "fix": fix}
