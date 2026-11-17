"""Module A's arithmetic (spec section 5): what a dish costs to make. Pure functions of their inputs, in
`Decimal`, so the same recipe and prices always give the same cost, and every figure can show its working.

    ingredient line   purchased_qty = qty_in_base_units / yield_pct
                      line_cost     = purchased_qty * unit_price
    sub-recipe line   line_cost     = qty_in_sub_recipe_yield_unit * (sub_recipe_batch_cost / sub_recipe_yield_qty)
    batch_cost     = sum of line_cost
    plate_cost     = batch_cost / portions
    food_cost_pct  = plate_cost / menu_price          (menu_price excludes tax)
    margin_dollars = menu_price - plate_cost

Money is kept to four places; a line's cost is rounded once, half up, and the batch is the sum of its lines.
Modifiers and add-ons are not costed, and labor is not part of the cost of a plate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

from app.margin_labor import units

CALC_VERSION = "a1"
FOUR = Decimal("0.0001")
MAX_DEPTH = 8


def money(value: Decimal) -> Decimal:
    return value.quantize(FOUR, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Line:
    id: str
    name: str
    qty: Decimal
    unit: str
    yield_pct: Decimal = Decimal(1)
    item_id: str | None = None
    sub_recipe_id: str | None = None


@dataclass(frozen=True)
class RecipeDef:
    id: str
    name: str
    lines: tuple[Line, ...]
    #: Portions one batch makes: 1 for a plated dish.
    portions: Decimal = Decimal(1)
    #: What one batch yields, for a sub-recipe used by weight or volume: 500 g of sauce.
    yield_qty: Decimal = Decimal(1)
    yield_unit: str = "plate"
    kind: str = "menu_item"
    confirmed: bool = True


@dataclass(frozen=True)
class Item:
    id: str
    name: str
    base_unit: str
    #: The item's own conversions: unit -> base units.
    conversions: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Price:
    """A unit price in the item's base unit, and where it came from."""

    unit_price: Decimal
    line_id: str | None = None
    invoice_id: str | None = None
    supplier_id: str | None = None
    day: str | None = None
    policy: str = "latest"


@dataclass
class Costed:
    batch_cost: Decimal
    plate_cost: Decimal
    lines_priced: int
    lines_total: int
    #: One entry per line: what was used to price it, or why it could not be.
    basis: list[dict]
    #: Names of the lines with no cost, with the reason.
    unpriced: list[str]
    #: item id -> purchased quantity per portion, in the item's base unit (after yield, through sub-recipes).
    use_per_portion: dict[str, Decimal]

    @property
    def complete(self) -> bool:
        return self.lines_total > 0 and self.lines_priced == self.lines_total


def cost_recipe(recipe: RecipeDef, recipes: dict[str, RecipeDef], items: dict[str, Item], price_of: Callable[[str], Price | None],
                _stack: tuple[str, ...] = ()) -> Costed:
    """Cost one batch of `recipe`, sub-recipes first. A line that can't be priced is listed, not guessed, and
    the cost is then a floor ("at least")."""
    stack = _stack + (recipe.id,)
    batch = Decimal(0)
    basis: list[dict] = []
    unpriced: list[str] = []
    priced = 0
    use: dict[str, Decimal] = {}
    for line in recipe.lines:
        entry: dict = {"line_id": line.id, "name": line.name, "qty": str(line.qty), "unit": line.unit}
        if line.sub_recipe_id:
            sub = recipes.get(line.sub_recipe_id)
            reason = None
            if sub is None or not sub.confirmed:
                reason = "the sub-recipe is not confirmed"
            elif sub.id in stack or len(stack) >= MAX_DEPTH:
                reason = "the sub-recipe uses this recipe"
            inner = cost_recipe(sub, recipes, items, price_of, stack) if reason is None else None
            qty = units.convert(line.qty, line.unit, sub.yield_unit) if inner is not None else None
            if inner is not None and qty is None:
                reason = f"{line.unit} does not convert to the sub-recipe's {sub.yield_unit}"
            elif inner is not None and not inner.complete:
                reason = "the sub-recipe has unpriced lines: " + ", ".join(inner.unpriced)
            elif inner is not None and sub.yield_qty <= 0:
                reason = "the sub-recipe has no yield"
            if reason is not None:
                unpriced.append(f"{line.name} ({reason})")
                basis.append({**entry, "kind": "sub_recipe", "sub_recipe_id": line.sub_recipe_id, "priced": False, "reason": reason})
                continue
            per_yield_unit = inner.batch_cost / sub.yield_qty
            cost = money(qty * per_yield_unit)
            batch += cost
            priced += 1
            share = qty / sub.yield_qty
            for item_id, per_batch in _per_batch(inner, sub).items():
                use[item_id] = use.get(item_id, Decimal(0)) + per_batch * share
            basis.append({**entry, "kind": "sub_recipe", "sub_recipe_id": sub.id, "priced": True, "sub_batch_cost": str(inner.batch_cost),
                          "sub_yield_qty": str(sub.yield_qty), "sub_yield_unit": sub.yield_unit, "qty_in_yield_unit": str(qty),
                          "unit_price": str(_trim(per_yield_unit)), "line_cost": str(cost)})
            continue
        item = items.get(line.item_id or "")
        if item is None:
            unpriced.append(f"{line.name} (not matched to an invoice item)")
            basis.append({**entry, "kind": "ingredient", "priced": False, "reason": "not matched to an invoice item"})
            continue
        entry.update(kind="ingredient", item_id=item.id, item_name=item.name, base_unit=item.base_unit, yield_pct=str(line.yield_pct))
        in_base = units.to_base(line.qty, line.unit, item.base_unit, item.conversions)
        price = price_of(item.id)
        reason = None
        if in_base is None:
            reason = f"{line.unit} does not convert to {item.base_unit}"
        elif not (Decimal(0) < line.yield_pct <= Decimal(1)):
            reason = "the yield must be more than 0 and at most 1"
        elif price is None:
            reason = "no invoice price"
        if reason is not None:
            unpriced.append(f"{line.name} ({reason})")
            basis.append({**entry, "priced": False, "reason": reason})
            continue
        purchased = in_base / line.yield_pct
        cost = money(purchased * price.unit_price)
        batch += cost
        priced += 1
        use[item.id] = use.get(item.id, Decimal(0)) + purchased
        basis.append({**entry, "priced": True, "qty_in_base": str(_trim(in_base)), "conversion_factor": str(_trim(in_base / line.qty)) if line.qty else "0",
                      "purchased_qty": str(_trim(purchased)), "unit_price": str(_trim(price.unit_price)), "price_point_id": price.line_id,
                      "invoice_id": price.invoice_id, "supplier_id": price.supplier_id, "price_date": price.day, "price_policy": price.policy,
                      "line_cost": str(cost)})
    portions = recipe.portions if recipe.portions > 0 else Decimal(1)
    return Costed(batch_cost=money(batch), plate_cost=money(batch / portions), lines_priced=priced, lines_total=len(recipe.lines), basis=basis,
                  unpriced=unpriced, use_per_portion={k: v / portions for k, v in use.items()})


def _per_batch(costed: Costed, recipe: RecipeDef) -> dict[str, Decimal]:
    portions = recipe.portions if recipe.portions > 0 else Decimal(1)
    return {k: v * portions for k, v in costed.use_per_portion.items()}


def _trim(value: Decimal) -> Decimal:
    """Eight places is enough to show a price per gram without noise."""
    return value.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP).normalize() if value else Decimal(0)


def food_cost_pct(plate_cost: Decimal, menu_price: Decimal | None) -> Decimal | None:
    if menu_price is None or menu_price <= 0:
        return None
    return (plate_cost / menu_price).quantize(FOUR, rounding=ROUND_HALF_UP)


# --- order and cycles ---------------------------------------------------------------------------------


def sub_ids(recipe: RecipeDef) -> set[str]:
    return {line.sub_recipe_id for line in recipe.lines if line.sub_recipe_id}


def would_cycle(recipes: dict[str, RecipeDef], recipe_id: str, new_sub_ids: set[str]) -> bool:
    """Whether saving `recipe_id` with these sub-recipes would make a recipe (in the end) use itself."""
    seen: set[str] = set()
    todo = list(new_sub_ids)
    while todo:
        current = todo.pop()
        if current == recipe_id:
            return True
        if current in seen or current not in recipes:
            continue
        seen.add(current)
        todo.extend(sub_ids(recipes[current]))
    return False


def topological(recipes: dict[str, RecipeDef], wanted: set[str]) -> list[str]:
    """`wanted` ordered so a sub-recipe comes before every recipe that uses it. A cycle (which a save rejects)
    is cut where it is met rather than looped."""
    order: list[str] = []
    done: set[str] = set()

    def visit(recipe_id: str, stack: tuple[str, ...]) -> None:
        if recipe_id in done or recipe_id in stack or recipe_id not in recipes:
            return
        for sub in sorted(sub_ids(recipes[recipe_id])):
            visit(sub, stack + (recipe_id,))
        done.add(recipe_id)
        if recipe_id in wanted:
            order.append(recipe_id)

    for recipe_id in sorted(wanted):
        visit(recipe_id, ())
    return order


def users_of(recipes: dict[str, RecipeDef], item_ids: set[str]) -> set[str]:
    """Every recipe that uses any of these items, directly or through sub-recipes. One pass builds the reverse
    index; the walk up it is in memory."""
    parents: dict[str, set[str]] = {}
    hit: set[str] = set()
    for recipe in recipes.values():
        for line in recipe.lines:
            if line.item_id in item_ids:
                hit.add(recipe.id)
            if line.sub_recipe_id:
                parents.setdefault(line.sub_recipe_id, set()).add(recipe.id)
    todo = list(hit)
    while todo:
        for parent in parents.get(todo.pop(), ()):
            if parent not in hit:
                hit.add(parent)
                todo.append(parent)
    return hit


# --- the price a dish would need ----------------------------------------------------------------------


def round_to_ending(value: Decimal, ending: Decimal, up: bool) -> Decimal:
    """The nearest price ending in `ending` cents (0.00, 0.50, 0.95, 0.99) at or above `value` (up) or at or below it."""
    whole = (value - ending).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
    return (whole + ending).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class Reprice:
    price_at_target: Decimal
    price_cap: Decimal
    suggested_price: Decimal
    cap_applied: bool
    #: The food cost the suggested price reaches.
    food_cost_pct: Decimal


def suggest_price(plate_cost: Decimal, menu_price: Decimal, target: Decimal, ending: Decimal = Decimal("0.00"), cap_pct: Decimal = Decimal("0.10")) -> Reprice:
    """price_at_target = plate_cost / target, rounded up to the price ending; price_cap = menu_price * 1.10, rounded
    down to it; the suggestion is the lower. A person changes the price in the POS; FullHouse never does."""
    at_target = round_to_ending(plate_cost / target, ending, up=True)
    cap = round_to_ending(menu_price * (1 + cap_pct), ending, up=False)
    suggested = min(at_target, cap)
    return Reprice(at_target, cap, suggested, cap < at_target, food_cost_pct(plate_cost, suggested) or Decimal(0))
