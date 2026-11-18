"""Recipes, plate cost and menu margin.

- `propose_recipe` writes one `recipe` proposal (cap 5 a week) from lines the agent inferred: from
  a recipe card, or from the POS item's name and what the restaurant actually buys. Unknown
  ingredients become the proposal's question, never a guess.
- `compute_plate_costs` prices every confirmed recipe for a week: each line × the latest confirmed
  per-unit price from the restaurant's most-used supplier for that item; sub-recipes recurse to
  depth four; the price points used are kept as the basis.
- `menu_cards` opens a menu-margin card where a dish's cost rose 5% or more week over week, or its
  margin fell below the restaurant's median, with the weekly dollar impact. Never a price change:
  `propose_reprice` suggests one, for the owner to make in the POS.
- `onboarding` runs weekly for the first eight weeks after sales arrive: the five top sellers with
  no confirmed recipe get a proposal each.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.agents import caps
from app.agents.crud import add_event, get_definition_by_key
from app.agents.definitions import AGENT_KEY
from app.agents.models import AgentAction, AgentRun
from app.prices import engine as prices
from app.prices.models import PriceItem
from app.recipes.models import CONFIRMED, PROPOSED, PlateCost, Recipe, RecipeLine
from app.sales.models import SalesItem

logger = logging.getLogger(__name__)

MAX_DEPTH = 4
COST_RISE = 0.05
#: Typical ingredients of common dishes, used only to find candidates among what the restaurant buys.
DISH_LEXICON = {
    "caesar": [("kale|romaine|lettuce", 0.25, "lb"), ("parmesan", 0.05, "lb"), ("olive oil", 0.03, "gal"), ("bread|ciabatta|crouton", 0.1, "lb"), ("lemon", 0.5, "ct")],
    "pizza": [("flour", 0.5, "lb"), ("mozzarella", 0.3, "lb"), ("tomato", 0.3, "lb"), ("basil", 0.02, "lb"), ("olive oil", 0.01, "gal")],
    "margherita": [("flour", 0.5, "lb"), ("mozzarella", 0.3, "lb"), ("tomato", 0.3, "lb"), ("basil", 0.02, "lb"), ("olive oil", 0.01, "gal")],
    "tiramisu": [("mascarpone", 0.2, "lb"), ("espresso|coffee", 0.05, "lb"), ("egg", 1, "ct"), ("sugar", 0.1, "lb"), ("cocoa", 0.02, "lb")],
    "sandwich": [("bread|ciabatta", 1, "ct"), ("chicken|turkey|ham", 0.3, "lb"), ("lettuce|romaine", 0.05, "lb"), ("tomato", 0.1, "lb")],
    "salad": [("lettuce|romaine|kale|greens", 0.25, "lb"), ("olive oil", 0.02, "gal"), ("tomato", 0.1, "lb")],
    "pasta": [("pasta|spaghetti|penne", 0.3, "lb"), ("olive oil", 0.02, "gal"), ("parmesan", 0.05, "lb"), ("garlic", 0.02, "lb")],
    "burger": [("beef|ground beef", 0.4, "lb"), ("bun|bread", 1, "ct"), ("cheese|cheddar", 0.1, "lb"), ("lettuce", 0.05, "lb"), ("tomato", 0.1, "lb")],
    "soup": [("stock|broth", 0.5, "qt"), ("onion", 0.2, "lb"), ("carrot", 0.1, "lb"), ("celery", 0.1, "lb")],
    "fries": [("potato", 0.5, "lb"), ("oil|fryer oil", 0.02, "gal")],
    "wings": [("chicken wing|wings", 1.0, "lb"), ("butter", 0.05, "lb"), ("hot sauce", 0.1, "qt")],
}
#: Dish words that carry an ingredient directly ("kale" in "Kale Caesar").
STOP = {"with", "and", "the", "of", "a", "house", "classic", "special", "fresh", "grilled", "crispy", "day", "side", "small", "large"}


@dataclass
class Guess:
    text: str
    item: PriceItem | None
    qty: float | None
    unit: str | None
    score: float = 0.0


def _match(db: Session, restaurant_id: str, text: str) -> tuple[PriceItem | None, float]:
    """The item the ingredient text most resembles, by word overlap with the item's name."""
    best, score = None, 0.0
    words = set(prices.words(text))
    if not words:
        return None, 0.0
    for item in db.query(PriceItem).filter(PriceItem.restaurant_id == restaurant_id):
        name_words = set(prices.words(item.name.split(",")[0]))
        if not name_words:
            continue
        overlap = len(words & name_words) / len(words | name_words)
        if words <= name_words or name_words <= words:
            overlap = max(overlap, 0.6)
        if overlap > score:
            best, score = item, overlap
    return best, score


def infer_lines(db: Session, restaurant_id: str, dish_name: str) -> list[Guess]:
    """Ingredients for a dish from its name and the restaurant's own items: a starting point with a question, not a fact."""
    lowered = dish_name.lower()
    guesses: dict[str, Guess] = {}
    for key, ingredients in DISH_LEXICON.items():
        if key in lowered:
            for pattern, qty, unit in ingredients:
                best, score = None, 0.0
                for alternative in pattern.split("|"):
                    item, s = _match(db, restaurant_id, alternative)
                    if item is not None and s > score:
                        best, score = item, s
                label = pattern.split("|")[0]
                if best is not None and score >= 0.3:
                    if convert(qty, unit, best.unit) is None:
                        # The lexicon speaks in pounds; the restaurant buys this one by the piece (or the reverse).
                        qty, unit = (1.0 if best.unit in COUNT else qty), best.unit
                    guesses.setdefault(best.id, Guess(text=label, item=best, qty=qty, unit=unit, score=score))
                else:
                    guesses.setdefault(f"?{label}", Guess(text=label, item=None, qty=qty, unit=unit))
    # Words of the name that are themselves ingredients the restaurant buys ("Kale Caesar" -> kale).
    for word in re.findall(r"[a-z]+", lowered):
        if word in STOP or word in DISH_LEXICON:
            continue
        item, score = _match(db, restaurant_id, word)
        if item is not None and score >= 0.5 and item.id not in guesses:
            guesses[item.id] = Guess(text=word, item=item, qty=0.25 if item.unit in ("lb", "kg") else 1, unit=item.unit, score=score)
    return list(guesses.values())


def propose_recipe(db: Session, restaurant_id: str, *, name: str, ingredients: list[dict] | None = None, pos_item_id: str | None = None,
                   yield_qty: float = 1, yield_unit: str = "plate", source: str = "agent", document_id: str | None = None,
                   today: date | None = None, run: AgentRun | None = None) -> AgentAction | None:
    """One `recipe` proposal. `ingredients` as written (from a card or the agent) or inferred from the name."""
    guesses: list[Guess]
    if ingredients:
        guesses = []
        for ing in ingredients:
            text = str(ing.get("text") or "").strip()
            if not text:
                continue
            item, score = _match(db, restaurant_id, text)
            guesses.append(Guess(text=text, item=item if score >= 0.3 else None, qty=ing.get("qty"), unit=ing.get("unit"), score=score))
    else:
        guesses = infer_lines(db, restaurant_id, name)
    if not guesses:
        return None
    existing = db.query(AgentAction).filter(AgentAction.restaurant_id == restaurant_id, AgentAction.action_type == "recipe",
                                            AgentAction.status == "pending").all()
    for action in existing:
        if (action.payload or {}).get("name", "").lower() == name.lower():
            return action
    if not caps.within(db, restaurant_id, "recipe", today):
        return None
    lines = [{"ingredient_text": g.text, "item_id": g.item.id if g.item else None, "item_name": g.item.name if g.item else None,
              "qty": g.qty if g.qty is not None else 1, "unit": g.unit or (g.item.unit if g.item else "each"), "matched": g.item is not None}
             for g in guesses]
    unknown = [g.text for g in guesses if g.item is None]
    question = (f"Is this right for {name}? " + (f"I couldn't find {', '.join(unknown)} on your invoices; add or drop those." if unknown else "Change any quantity before approving."))
    if run is None:
        definition = get_definition_by_key(db, AGENT_KEY)
        run = _system_run(db, restaurant_id, definition, f"Recipe proposed for {name}")
    action = AgentAction(restaurant_id=restaurant_id, run_id=run.id, agent_definition_id=run.agent_definition_id, action_type="recipe", payload={
        "name": name, "pos_item_id": pos_item_id, "yield_qty": yield_qty, "yield_unit": yield_unit, "lines": lines, "source": source,
        "document_id": document_id, "question": question, "unknown": unknown,
        "note": f"{len(lines)} lines, {len(unknown)} not on the invoices. " + question,
    })
    db.add(action)
    db.flush()
    return action


def _system_run(db: Session, restaurant_id: str, definition, text: str) -> AgentRun:
    now = datetime.now(timezone.utc)
    run = AgentRun(restaurant_id=restaurant_id, agent_definition_id=definition.id, status="succeeded", trigger_type="scheduled",
                   input=text, output_summary=text, started_at=now, finished_at=now, cost_usd=0.0)
    db.add(run)
    db.flush()
    for kind, payload in (("status_change", {"status": "running"}), ("log", {"text": text}), ("status_change", {"status": "succeeded"})):
        add_event(db, run.id, kind, payload)
    return run


def write_recipe(db: Session, restaurant_id: str, payload: dict, confirmed_by: str | None = None) -> Recipe:
    """The applier's work: a confirmed recipe (or an update of one with the same name) from a proposal's payload."""
    name = str(payload.get("name") or "Recipe")[:200]
    recipe = db.query(Recipe).filter(Recipe.restaurant_id == restaurant_id, Recipe.name.ilike(name)).first()
    if recipe is None:
        recipe = Recipe(restaurant_id=restaurant_id, name=name)
        db.add(recipe)
        db.flush()
    recipe.pos_item_id = payload.get("pos_item_id") or recipe.pos_item_id
    recipe.menu_item_id = payload.get("menu_item_id") or recipe.menu_item_id
    recipe.yield_qty = float(payload.get("yield_qty") or 1)
    recipe.yield_unit = str(payload.get("yield_unit") or "plate")[:32]
    recipe.status = CONFIRMED
    recipe.source = str(payload.get("source") or "agent")[:10]
    recipe.document_id = payload.get("document_id") or recipe.document_id
    for line in list(recipe.lines):
        db.delete(line)
    db.flush()
    for position, raw in enumerate(payload.get("lines") or []):
        if raw.get("drop"):
            continue
        item_id = raw.get("item_id")
        sub_id = raw.get("sub_recipe_id")
        if not item_id and not sub_id:
            continue  # an ingredient nobody matched: left out rather than guessed
        db.add(RecipeLine(restaurant_id=restaurant_id, recipe_id=recipe.id, position=position, item_id=item_id, sub_recipe_id=sub_id,
                          qty=float(raw.get("qty") or 0), unit=str(raw.get("unit") or "each")[:32], ingredient_text=str(raw.get("ingredient_text") or "")[:200] or None))
    db.flush()
    return recipe


# --- plate cost ------------------------------------------------------------------------------


UNIT_FACTORS = {"lb": 1.0, "oz": 1 / 16, "kg": 2.20462, "g": 0.00220462, "gal": 1.0, "qt": 0.25, "pt": 0.125, "cup": 0.0625, "fl oz": 1 / 128, "l": 0.264172, "ml": 0.000264172}
COUNT = {"ct", "each", "ea", "head", "bunch", "loaf", "bottle", "can", "jar", "piece", "pc", "slice", "clove"}


def convert(qty: float, unit: str, item_unit: str) -> float | None:
    """`qty` of `unit` as a number of the item's unit; None when they don't convert."""
    unit, item_unit = (unit or "").lower(), (item_unit or "").lower()
    if unit == item_unit or (unit in COUNT and item_unit in COUNT):
        return qty
    mass = {"lb", "oz", "kg", "g"}
    volume = {"gal", "qt", "pt", "cup", "fl oz", "l", "ml"}
    if (unit in mass and item_unit in mass) or (unit in volume and item_unit in volume):
        return qty * UNIT_FACTORS[unit] / UNIT_FACTORS[item_unit]
    return None


@dataclass
class Priced:
    cost: float
    basis: list[dict] = field(default_factory=list)
    unpriced: list[str] = field(default_factory=list)


def _latest_prices(db: Session, restaurant_id: str, today: date) -> dict[str, dict]:
    """item_id -> the latest confirmed per-unit price from the restaurant's most-used supplier for that item in the window."""
    facts = prices.gather(db, restaurant_id, today)
    out = {}
    for item_id, f in facts.items():
        by_supplier: dict[str, list] = {}
        for p in f.priced:
            by_supplier.setdefault(p.supplier_id, []).append(p)
        most_used = max(by_supplier.items(), key=lambda kv: (len(kv[1]), max(p.day for p in kv[1])))[1]
        latest = max(most_used, key=lambda p: (p.day, p.line.line_no))
        out[item_id] = {"per_unit": latest.per_unit, "line_id": latest.line.id, "supplier_id": latest.supplier_id, "day": latest.day.isoformat()}
    return out


def price_recipe(db: Session, recipe: Recipe, latest: dict[str, dict], depth: int = 0, seen: set | None = None) -> Priced:
    seen = seen or set()
    if depth > MAX_DEPTH or recipe.id in seen:
        return Priced(0.0, unpriced=[f"{recipe.name} (too deep)"])
    seen = seen | {recipe.id}
    total, basis, unpriced = 0.0, [], []
    for line in recipe.lines:
        if line.sub_recipe_id:
            sub = db.get(Recipe, line.sub_recipe_id)
            if sub is None or sub.status != CONFIRMED:
                unpriced.append(line.ingredient_text or "a sub-recipe")
                continue
            inner = price_recipe(db, sub, latest, depth + 1, seen)
            per_yield = inner.cost / (sub.yield_qty or 1)
            total += per_yield * line.qty
            basis.append({"sub_recipe_id": sub.id, "qty": line.qty, "unit": line.unit, "cost": round(per_yield * line.qty, 4)})
            unpriced.extend(inner.unpriced)
            continue
        item = db.get(PriceItem, line.item_id) if line.item_id else None
        point = latest.get(line.item_id) if line.item_id else None
        if item is None or point is None:
            unpriced.append(line.ingredient_text or (item.name if item else "an ingredient"))
            continue
        amount = convert(line.qty, line.unit, item.unit)
        if amount is None:
            unpriced.append(f"{item.name} ({line.unit} against {item.unit})")
            continue
        cost = amount * point["per_unit"]
        total += cost
        basis.append({"item_id": item.id, "item_name": item.name, "line_id": point["line_id"], "supplier_id": point["supplier_id"],
                      "per_unit": point["per_unit"], "qty": line.qty, "unit": line.unit, "cost": round(cost, 4)})
    return Priced(round(total / (recipe.yield_qty or 1), 4), basis, unpriced)


def compute_plate_costs(db: Session, restaurant_id: str, week_of: date | None = None, today: date | None = None) -> list[PlateCost]:
    today = today or date.today()
    week_of = prices.week_start(week_of or today)
    latest = _latest_prices(db, restaurant_id, today)
    rows = []
    for recipe in db.query(Recipe).filter(Recipe.restaurant_id == restaurant_id, Recipe.status == CONFIRMED):
        priced = price_recipe(db, recipe, latest)
        row = db.query(PlateCost).filter(PlateCost.recipe_id == recipe.id, PlateCost.week_of == week_of).first()
        if row is None:
            row = PlateCost(restaurant_id=restaurant_id, recipe_id=recipe.id, week_of=week_of, cost=0)
            db.add(row)
        row.cost, row.basis, row.unpriced = priced.cost, priced.basis, priced.unpriced
        rows.append(row)
    db.flush()
    from app.platform import events

    events.emit(db, restaurant_id, "plate_cost.computed", "plate_costs", week_of.isoformat(), {"recipes": len(rows)})
    return rows


# --- menu margin ----------------------------------------------------------------------------------


def weekly_qty(db: Session, restaurant_id: str, pos_item_id: str, today: date, weeks: int = 4) -> tuple[float, float]:
    """(units a week, net a unit) over the last `weeks` of sales for one POS item."""
    since = today - timedelta(days=7 * weeks)
    qty, net = db.query(func.coalesce(func.sum(SalesItem.qty), 0), func.coalesce(func.sum(SalesItem.net), 0)).filter(
        SalesItem.restaurant_id == restaurant_id, SalesItem.pos_item_id == pos_item_id, SalesItem.date > since, SalesItem.date <= today).one()
    qty, net = float(qty or 0), float(net or 0)
    return qty / weeks, (net / qty if qty else 0.0)


def menu_cards(db: Session, restaurant_id: str, today: date | None = None) -> list[dict]:
    """Menu-margin cards for this week: cost up 5%+ week over week, or margin below the restaurant's median."""
    today = today or date.today()
    week = prices.week_start(today)
    recipes_ = db.query(Recipe).filter(Recipe.restaurant_id == restaurant_id, Recipe.status == CONFIRMED, Recipe.pos_item_id.isnot(None)).all()
    if not recipes_:
        return []
    figures = []
    for recipe in recipes_:
        now_row = db.query(PlateCost).filter(PlateCost.recipe_id == recipe.id, PlateCost.week_of == week).first()
        if now_row is None:
            continue
        before = db.query(PlateCost).filter(PlateCost.recipe_id == recipe.id, PlateCost.week_of < week).order_by(PlateCost.week_of.desc()).first()
        qty, price = weekly_qty(db, restaurant_id, recipe.pos_item_id, today)
        margin = (price - now_row.cost) / price if price > 0 else None
        figures.append((recipe, now_row, before, qty, price, margin))
    margins = sorted(m for *_, m in figures if m is not None)
    median = margins[len(margins) // 2] if margins else None
    cards = []
    for recipe, now_row, before, qty, price, margin in figures:
        reasons, impact = [], 0.0
        if before is not None and before.cost > 0 and now_row.cost >= before.cost * (1 + COST_RISE):
            delta = now_row.cost - before.cost
            impact = round(delta * qty, 2)
            reasons.append(f"Plate cost {prices.money(before.cost)} to {prices.money(now_row.cost)}, up {delta / before.cost * 100:.0f}% since the week of {prices.short_date(before.week_of)}.")
        if margin is not None and median is not None and len(margins) >= 3 and margin < median - 0.05:
            reasons.append(f"Margin {margin * 100:.0f}% against your median {median * 100:.0f}% at {prices.money(price)} a plate.")
            impact = max(impact, round((median - margin) * price * qty, 2))
        if not reasons or qty <= 0:
            continue
        sources = [{"kind": "invoice", "id": db.query(prices.InvoiceLine).get(b["line_id"]).invoice_id, "label": f"{b['item_name']}: {prices.money(b['per_unit'])} per unit"}
                   for b in now_row.basis[:3] if b.get("line_id")]
        cards.append({
            "section": "margin", "item_id": recipe.id, "recipe_id": recipe.id, "item_name": recipe.name, "impact": max(impact, 0.01),
            "sentence": " ".join(reasons) + f" {qty:g} sold a week.", "action": {"kind": "reprice", "label": "Reprice or reduce portion"},
            "sources": sources,
        })
    return cards


def propose_reprice(db: Session, restaurant_id: str, recipe: Recipe, today: date | None = None, run: AgentRun | None = None) -> AgentAction | None:
    """A `menu_reprice` proposal: suggested delta from the plate cost move; the owner changes the price in the POS."""
    today = today or date.today()
    week = prices.week_start(today)
    now_row = db.query(PlateCost).filter(PlateCost.recipe_id == recipe.id).order_by(PlateCost.week_of.desc()).first()
    before = db.query(PlateCost).filter(PlateCost.recipe_id == recipe.id, PlateCost.week_of < (now_row.week_of if now_row else week)).order_by(PlateCost.week_of.desc()).first()
    if now_row is None:
        return None
    for action in db.query(AgentAction).filter(AgentAction.restaurant_id == restaurant_id, AgentAction.action_type == "menu_reprice", AgentAction.status == "pending"):
        if (action.payload or {}).get("recipe_id") == recipe.id:
            return action
    if not caps.within(db, restaurant_id, "menu_reprice", today):
        return None
    qty, price = weekly_qty(db, restaurant_id, recipe.pos_item_id or "", today) if recipe.pos_item_id else (0.0, 0.0)
    delta_cost = now_row.cost - (before.cost if before else now_row.cost)
    target_margin = 0.70
    suggested = round(max(delta_cost / (1 - target_margin), 0.25) * 4) / 4 if delta_cost > 0 else round(max(now_row.cost / (1 - target_margin) - price, 0), 2)
    if run is None:
        definition = get_definition_by_key(db, AGENT_KEY)
        run = _system_run(db, restaurant_id, definition, f"Reprice suggested for {recipe.name}")
    action = AgentAction(restaurant_id=restaurant_id, run_id=run.id, agent_definition_id=run.agent_definition_id, action_type="menu_reprice", payload={
        "recipe_id": recipe.id, "item_name": recipe.name, "pos_item_id": recipe.pos_item_id, "current_price": round(price, 2) if price else None,
        "suggested_delta": round(suggested, 2), "plate_cost_before": round(before.cost, 4) if before else None, "plate_cost_after": round(now_row.cost, 4),
        "cited_line_ids": [b["line_id"] for b in now_row.basis if b.get("line_id")],
        "note": (f"Plate cost {prices.money(before.cost) if before else '?'} to {prices.money(now_row.cost)}. "
                 f"At {prices.money(price)} a plate" if price else f"Plate cost {prices.money(now_row.cost)}.") +
                (f", a {prices.money(suggested)} rise keeps the margin. Change it in your POS; FullHouse never writes to a menu." if price else
                 " Change the price in your POS; FullHouse never writes to a menu."),
    })
    db.add(action)
    db.flush()
    return action


# --- onboarding -------------------------------------------------------------------------------------


def onboarding(db: Session, restaurant_id: str, today: date | None = None) -> list[AgentAction]:
    """The five top-selling POS items with no confirmed recipe, proposed, weekly for the first eight weeks of sales."""
    today = today or date.today()
    first = db.query(func.min(SalesItem.date)).filter(SalesItem.restaurant_id == restaurant_id).scalar()
    if first is None:
        return []
    confirmed = {r.pos_item_id for r in db.query(Recipe).filter(Recipe.restaurant_id == restaurant_id, Recipe.status == CONFIRMED) if r.pos_item_id}
    proposed_names = {(a.payload or {}).get("pos_item_id") for a in db.query(AgentAction).filter(
        AgentAction.restaurant_id == restaurant_id, AgentAction.action_type == "recipe", AgentAction.status != "rejected")}
    since = today - timedelta(days=28)
    rows = (db.query(SalesItem.pos_item_id, SalesItem.pos_item_name, func.sum(SalesItem.qty).label("q"))
            .filter(SalesItem.restaurant_id == restaurant_id, SalesItem.date > since)
            .group_by(SalesItem.pos_item_id, SalesItem.pos_item_name).order_by(func.sum(SalesItem.qty).desc()).all())
    if (today - first).days > 56:
        # After eight weeks: only items that appeared since the last run (no confirmed recipe and never proposed).
        rows = [r for r in rows if r.pos_item_id not in proposed_names]
    made = []
    for pos_item_id, name, _ in rows:
        if len(made) >= 5:
            break
        if pos_item_id in confirmed or pos_item_id in proposed_names:
            continue
        action = propose_recipe(db, restaurant_id, name=name, pos_item_id=pos_item_id, today=today)
        if action is not None:
            made.append(action)
    return made
