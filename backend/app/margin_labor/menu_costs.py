"""Module A, live recipe costing (spec section 5): recost every affected dish when an input changes, flag the
dishes whose food cost passes the target, and suggest the price that would bring one back.

`recost` is the job. It is a pure function of the recipes, the invoice prices and the menu prices: it hashes
those inputs per recipe and writes a new `dish_costs` row only when the hash moved, so running it twice leaves
the same rows. Sub-recipes are costed before the recipes that use them.

Nothing here changes a menu price. A reprice is a `menu_reprice` proposal a person approves and then carries
out in the POS.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from statistics import median

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.margin_labor import costing, price_points
from app.margin_labor import settings as ml_settings
from app.margin_labor.business import business_date
from app.margin_labor.costing import CALC_VERSION, Costed, Line, RecipeDef
from app.margin_labor.models import DishCost, Finding, PosItem
from app.platform import events
from app.recipes.models import CONFIRMED, Recipe, RecipeLine
from app.restaurant.models import MenuItem
from app.sales.models import SalesItem

logger = logging.getLogger(__name__)

FINDING = "menu_margin"
ACTION = "menu_reprice"
#: Pending `menu_reprice` proposals made in one week, per restaurant (ground rule 9).
WEEKLY_CAP = 3
WINDOW_DAYS = 28
CENT = Decimal("0.01")


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass
class World:
    """Everything a recost reads, loaded once."""

    restaurant_id: str
    conf: ml_settings.Settings
    today: date
    rows: dict[str, Recipe]
    recipes: dict[str, RecipeDef]
    items: dict
    points: dict

    def price_of(self, day: date):
        return lambda item_id: price_points.price_as_of(self.points.get(item_id), day, self.conf.costing_price_policy)

    def cost(self, recipe_id: str, day: date | None = None) -> Costed:
        return costing.cost_recipe(self.recipes[recipe_id], self.recipes, self.items, self.price_of(day or self.today))


def load(db: Session, restaurant_id: str, now: datetime | None = None) -> World:
    conf = ml_settings.for_restaurant(db, restaurant_id)
    today = business_date(now or datetime.now(timezone.utc), conf.timezone, conf.business_day_start)
    rows = {r.id: r for r in db.query(Recipe).filter(Recipe.restaurant_id == restaurant_id)}
    lines: dict[str, list[RecipeLine]] = {}
    for line in db.query(RecipeLine).filter(RecipeLine.restaurant_id == restaurant_id).order_by(RecipeLine.position, RecipeLine.id):
        lines.setdefault(line.recipe_id, []).append(line)
    items = price_points.items(db, restaurant_id)
    used_as_sub = {l.sub_recipe_id for ls in lines.values() for l in ls if l.sub_recipe_id}
    recipes = {}
    for row in rows.values():
        kind = kind_of(row, row.id in used_as_sub)
        yield_qty = _dec(row.yield_qty or 1)
        portions = row.portions if row.portions is not None else (yield_qty if kind == "menu_item" else Decimal(1))
        defs = []
        for line in lines.get(row.id, []):
            name = line.ingredient_text or (items[line.item_id].name if line.item_id in items else None) or (
                rows[line.sub_recipe_id].name if line.sub_recipe_id in rows else None) or "A line"
            defs.append(Line(line.id, name, _dec(line.qty), line.unit, line.yield_pct if line.yield_pct is not None else Decimal(1),
                             line.item_id, line.sub_recipe_id))
        recipes[row.id] = RecipeDef(row.id, row.name, tuple(defs), portions, yield_qty, row.yield_unit, kind, row.status == CONFIRMED)
    used = {l.item_id for r in recipes.values() for l in r.lines if l.item_id}
    return World(restaurant_id, conf, today, rows, recipes, items, price_points.load(db, restaurant_id, used))


def kind_of(row: Recipe, used_as_sub: bool) -> str:
    """menu_item or sub_recipe: what was saved, else from how the recipe is used."""
    if row.kind in ("menu_item", "sub_recipe"):
        return row.kind
    if row.pos_item_id or row.menu_item_id:
        return "menu_item"
    from app.margin_labor import units

    return "sub_recipe" if used_as_sub or units.kind_of(row.yield_unit) != "count" else "menu_item"


# --- the menu price and what sold -------------------------------------------------------------------


def menu_price(db: Session, world: World, row: Recipe) -> tuple[Decimal | None, dict]:
    """The dish's price before tax: the POS catalog's, else the app's own menu's, else the median of net / qty over
    the last 14 days. None when there is none: the cost then shows with no percentage."""
    if row.pos_item_id:
        item = (db.query(PosItem).filter(PosItem.restaurant_id == world.restaurant_id, PosItem.pos_item_id == row.pos_item_id, PosItem.price.isnot(None),
                                         PosItem.active.is_(True)).order_by(PosItem.updated_at.desc(), PosItem.id).first())
        if item is not None and item.price > 0:
            return item.price, {"source": "pos_items", "id": item.id, "label": "Point of sale price"}
    if row.menu_item_id:
        item = db.get(MenuItem, row.menu_item_id)
        if item is not None and item.restaurant_id == world.restaurant_id and item.price and item.price > 0:
            return _dec(item.price).quantize(costing.FOUR), {"source": "menu_items", "id": item.id, "label": "Menu price"}
    if row.pos_item_id:
        since = world.today - timedelta(days=14)
        each = [_dec(net) / _dec(qty) for net, qty in db.query(SalesItem.net, SalesItem.qty).filter(
            SalesItem.restaurant_id == world.restaurant_id, SalesItem.pos_item_id == row.pos_item_id, SalesItem.date >= since, SalesItem.date < world.today,
            SalesItem.qty > 0)]
        if each:
            return Decimal(median(each)).quantize(costing.FOUR), {"source": "sales_items", "label": "Median of net sales per item, last 14 days", "days": len(each)}
    return None, {"source": None, "label": "No menu price"}


def units_sold(db: Session, world: World, days: int = WINDOW_DAYS) -> dict[str, Decimal]:
    """POS item id -> units sold over the last `days` business dates: the complete ones, so not today's."""
    since = world.today - timedelta(days=days)
    return {pos: _dec(qty or 0) for pos, qty in db.query(SalesItem.pos_item_id, func.sum(SalesItem.qty)).filter(
        SalesItem.restaurant_id == world.restaurant_id, SalesItem.date >= since, SalesItem.date < world.today).group_by(SalesItem.pos_item_id)}


def target_for(db: Session, world: World, row: Recipe) -> Decimal:
    """The food cost target: the restaurant's, with an optional override for the dish's POS category."""
    by_category = world.conf.overrides.get("target_food_cost_pct_by_category") or {}
    if by_category and row.pos_item_id:
        category = db.query(PosItem.category).filter(PosItem.restaurant_id == world.restaurant_id, PosItem.pos_item_id == row.pos_item_id).limit(1).scalar()
        if category and category in by_category:
            return Decimal(str(by_category[category]))
    return world.conf.target_food_cost_pct


# --- recost -------------------------------------------------------------------------------------------


def _dependents(recipes: dict[str, RecipeDef], recipe_ids: set[str]) -> set[str]:
    """`recipe_ids` and every recipe that uses one of them, however deep."""
    parents: dict[str, set[str]] = {}
    for recipe in recipes.values():
        for sub in costing.sub_ids(recipe):
            parents.setdefault(sub, set()).add(recipe.id)
    out, todo = set(recipe_ids), list(recipe_ids)
    while todo:
        for parent in parents.get(todo.pop(), ()):
            if parent not in out:
                out.add(parent)
                todo.append(parent)
    return out


def _hash(recipe: RecipeDef, costed: Costed, price: Decimal | None, policy: str, price_source: str | None = None) -> str:
    inputs = {"v": CALC_VERSION, "policy": policy, "portions": str(recipe.portions), "yield": [str(recipe.yield_qty), recipe.yield_unit], "kind": recipe.kind,
              "lines": costed.basis, "menu_price": None if price is None else str(price), "menu_price_source": price_source}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def recost(db: Session, restaurant_id: str, *, recipe_ids: set[str] | None = None, item_ids: set[str] | None = None,
           trigger_event_id: str | None = None, now: datetime | None = None) -> dict:
    """Recost the recipes an input change reaches (every confirmed recipe, when none is named), then check each
    dish against the alert rule. Returns what it did."""
    now = now or datetime.now(timezone.utc)
    world = load(db, restaurant_id, now)
    if recipe_ids is None and item_ids is None:
        wanted = set(world.recipes)
    else:
        wanted = _dependents(world.recipes, set(recipe_ids or ())) | costing.users_of(world.recipes, set(item_ids or ()))
    wanted = {r for r in wanted if r in world.recipes and world.recipes[r].confirmed}
    sold = units_sold(db, world)
    written = changed = 0
    for recipe_id in costing.topological(world.recipes, wanted):
        row, recipe = world.rows[recipe_id], world.recipes[recipe_id]
        costed = world.cost(recipe_id)
        price, price_basis = menu_price(db, world, row) if recipe.kind == "menu_item" else (None, {"source": None, "label": "A sub-recipe has no menu price"})
        digest = _hash(recipe, costed, price, world.conf.costing_price_policy, price_basis.get("source"))
        current = db.query(DishCost).filter(DishCost.restaurant_id == restaurant_id, DishCost.recipe_id == recipe_id, DishCost.is_current.is_(True)).first()
        if current is None or current.inputs_hash != digest:
            pct = costing.food_cost_pct(costed.plate_cost, price)
            previous_cost = current.plate_cost if current is not None else None
            if current is not None:
                current.is_current = False
                db.flush()  # the old row gives up "current" before the new one takes it: one transaction, one current row
            current = DishCost(restaurant_id=restaurant_id, recipe_id=recipe_id, week_of=world.today - timedelta(days=world.today.weekday()),
                               effective_at=now, is_current=True, batch_cost=costed.batch_cost, plate_cost=costed.plate_cost, menu_price=price,
                               food_cost_pct=pct, margin_dollars=None if price is None else (price - costed.plate_cost).quantize(costing.FOUR),
                               lines_priced=costed.lines_priced, lines_total=costed.lines_total, calc_version=CALC_VERSION,
                               trigger_event_id=trigger_event_id, inputs_hash=digest, unpriced=costed.unpriced,
                               basis={"lines": costed.basis, "menu_price": {**price_basis, "value": None if price is None else str(price)},
                                      "policy": world.conf.costing_price_policy, "portions": str(recipe.portions), "kind": recipe.kind})
            db.add(current)
            db.flush()
            written += 1
            if previous_cost is None or abs(costed.plate_cost - previous_cost) >= CENT:
                changed += 1
                events.emit(db, restaurant_id, "plate_cost.changed", "dish_costs", current.id,
                            {"recipe_id": recipe_id, "plate_cost": str(costed.plate_cost), "before": None if previous_cost is None else str(previous_cost)})
        if recipe.kind == "menu_item":
            _check_alert(db, world, row, current, costed, sold.get(row.pos_item_id or "", Decimal(0)))
    db.flush()
    return {"recipes": len(wanted), "written": written, "changed": changed}


# --- the alert rule and the reprice proposal ------------------------------------------------------------


def weekly_impact(now_cost: Decimal, then_cost: Decimal | None, sold_28: Decimal) -> Decimal:
    """(plate_cost_now - plate_cost_28_days_ago) * units_sold_last_28_days / 4"""
    if then_cost is None:
        return Decimal(0)
    return ((now_cost - then_cost) * sold_28 / 4).quantize(costing.FOUR)


def cost_then(world: World, recipe_id: str) -> Decimal | None:
    """What the recipe as it is now cost at the prices of 28 days ago; None when it could not be fully priced then."""
    then = world.cost(recipe_id, world.today - timedelta(days=WINDOW_DAYS))
    return then.plate_cost if then.complete else None


def _check_alert(db: Session, world: World, row: Recipe, cost: DishCost, costed: Costed, sold_28: Decimal) -> None:
    """Open one `menu_margin` finding for a dish when the recipe is confirmed and fully priced, its food cost is at
    least the target plus 0.02, and it sold at least 10 in 28 days. Close it below the target plus 0.01."""
    rid = world.restaurant_id
    target = target_for(db, world, row)
    over = ml_settings.value(db, rid, "margin_alert_over_target")
    close_over = ml_settings.value(db, rid, "margin_alert_close_over_target")
    min_units = Decimal(ml_settings.value(db, rid, "margin_min_units_28d"))
    finding = db.query(Finding).filter(Finding.restaurant_id == rid, Finding.type == FINDING, Finding.subject_id == row.id, Finding.status == "open").first()
    pct = cost.food_cost_pct
    if finding is not None and pct is not None and pct < target + close_over:
        finding.status, finding.closed_at = "closed", datetime.now(timezone.utc)
        finding.note = f"Food cost fell to {pct * 100:.1f}%."
        return
    qualifies = costed.complete and pct is not None and pct >= target + over and sold_28 >= min_units
    if finding is None and not qualifies:
        return
    then = cost_then(world, row.id)
    impact = weekly_impact(cost.plate_cost, then, sold_28)
    basis = {"formula": "(cost per plate now - cost per plate 28 days ago) x units sold in 28 days / 4",
             "plate_cost_now": str(cost.plate_cost), "plate_cost_28_days_ago": None if then is None else str(then), "units_sold_28_days": str(sold_28),
             "weekly_impact": str(impact), "menu_price": None if cost.menu_price is None else str(cost.menu_price),
             "food_cost_pct": None if pct is None else str(pct), "target": str(target), "dish_cost_id": cost.id, "as_of": world.today.isoformat()}
    title = f"{row.name}: food cost {pct * 100:.1f}% against a {target * 100:.1f}% target" if pct is not None else f"{row.name}: no menu price"
    if finding is None:
        finding = Finding(restaurant_id=rid, type=FINDING, subject_table="recipes", subject_id=row.id, title=title[:200],
                          weekly_dollars=max(impact, Decimal(0)), basis=basis)
        db.add(finding)
        db.flush()
        action = propose_reprice(db, world, row, cost, finding)
        if action is not None:
            finding.proposal_id = action.id
    else:
        finding.title, finding.weekly_dollars, finding.basis = title[:200], max(impact, Decimal(0)), basis


def propose_reprice(db: Session, world: World, row: Recipe, cost: DishCost, finding: Finding | None = None):
    """One `menu_reprice` proposal for a dish: the price that reaches the target, capped at 10% over today's price.
    None when there is nothing to suggest, one is already waiting, or three were made this week (held, and logged)."""
    from app.agents import caps
    from app.agents.crud import get_definition_by_key
    from app.agents.definitions import AGENT_KEY
    from app.agents.models import AgentAction
    from app.recipes.engine import _system_run

    rid = world.restaurant_id
    if cost.menu_price is None or cost.lines_priced != cost.lines_total:
        return None
    target = target_for(db, world, row)
    out = costing.suggest_price(cost.plate_cost, cost.menu_price, target, world.conf.price_ending, ml_settings.value(db, rid, "reprice_cap_pct"))
    if out.suggested_price <= cost.menu_price:
        return None  # the cap rounds down to today's price: nothing to suggest
    for action in db.query(AgentAction).filter(AgentAction.restaurant_id == rid, AgentAction.action_type == ACTION, AgentAction.status == "pending"):
        if (action.payload or {}).get("recipe_id") == row.id:
            return action
    if caps.made_this_week(db, rid, ACTION, world.today) >= WEEKLY_CAP:
        logger.info("cap reached: %s for %s this week; holding", ACTION, rid)
        events.emit(db, rid, "proposal.held", "agent_actions", None, {"kind": ACTION, "cap": WEEKLY_CAP, "recipe_id": row.id})
        return None
    definition = get_definition_by_key(db, AGENT_KEY)
    if definition is None:
        return None
    run = _system_run(db, rid, definition, f"Reprice suggested for {row.name}")
    reaches = f"{out.food_cost_pct * 100:.1f}%"
    note = (f"{row.name} costs ${cost.plate_cost:.2f} a plate, {cost.food_cost_pct * 100:.1f}% of its ${cost.menu_price:.2f} price. "
            f"${out.suggested_price:.2f} brings food cost to {reaches}"
            + (f"; the price is held to 10% over today's, short of the {target * 100:.1f}% target." if out.cap_applied else ".")
            + " Change it in your POS; FullHouse never writes to a menu.")
    then = (finding.basis or {}).get("plate_cost_28_days_ago") if finding is not None else None
    action = AgentAction(restaurant_id=rid, run_id=run.id, agent_definition_id=run.agent_definition_id, action_type=ACTION, payload={
        # The keys the Recommendations page already reads (display figures, derived from the decimals below).
        "recipe_id": row.id, "item_name": row.name, "pos_item_id": row.pos_item_id, "current_price": float(cost.menu_price.quantize(CENT)),
        "suggested_delta": float((out.suggested_price - cost.menu_price).quantize(CENT)), "plate_cost_before": None if then is None else float(Decimal(then)),
        "plate_cost_after": float(cost.plate_cost), "cited_line_ids": [b["price_point_id"] for b in cost.basis.get("lines", []) if b.get("price_point_id")],
        "note": note,
        # Module A's own, exact.
        "module": "menu_costs", "finding_id": finding.id if finding is not None else None, "dish_cost_id": cost.id,
        "plate_cost": str(cost.plate_cost), "menu_price": str(cost.menu_price), "target": str(target), "price_ending": str(world.conf.price_ending),
        "price_at_target": str(out.price_at_target), "price_cap": str(out.price_cap), "suggested_price": str(out.suggested_price),
        "cap_applied": out.cap_applied, "food_cost_pct_now": str(cost.food_cost_pct), "food_cost_pct_at_suggested": str(out.food_cost_pct),
        "weekly_dollars": str(finding.weekly_dollars) if finding is not None else "0",
    })
    db.add(action)
    db.flush()
    return action
