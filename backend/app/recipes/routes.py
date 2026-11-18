"""Recipes: the list, one recipe with its plate cost and the price points behind it, an edit as a proposal,
and a reprice suggestion on request."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import current_user, manager
from app.auth.models import User
from app.db.session import get_db
from app.platform import flags
from app.prices import engine as prices
from app.prices.models import PriceItem
from app.recipes import engine
from app.recipes.models import PlateCost, Recipe
from app.restaurant.inventory import scope_for

router = APIRouter(prefix="/api/v1/recipes", tags=["recipes"])


def _scope(db: Session, user: User) -> str:
    restaurant_id = scope_for(db, user)
    if restaurant_id is None:
        raise HTTPException(409, "Finish setup first.")
    if not flags.enabled(db, restaurant_id, flags.V2_DOCUMENTS):
        raise HTTPException(404, "Recipes aren't switched on for this restaurant.")
    return restaurant_id


class LineOut(BaseModel):
    id: str
    ingredient_text: str | None
    item_id: str | None
    item_name: str | None
    sub_recipe_id: str | None
    sub_recipe_name: str | None
    qty: float
    unit: str
    #: This week's cost of the line, when priced.
    cost: float | None


class CostPoint(BaseModel):
    week_of: date
    cost: float


class RecipeOut(BaseModel):
    id: str
    name: str
    pos_item_id: str | None
    yield_qty: float
    yield_unit: str
    status: str
    source: str
    lines: list[LineOut]
    plate_cost: float | None
    plate_cost_week: date | None
    unpriced: list[str]
    basis: list[dict]
    history: list[CostPoint]
    sold_a_week: float | None
    price_a_plate: float | None
    margin_pct: float | None


def _out(db: Session, recipe: Recipe, today: date | None = None) -> RecipeOut:
    today = today or date.today()
    latest = db.query(PlateCost).filter(PlateCost.recipe_id == recipe.id).order_by(PlateCost.week_of.desc()).first()
    by_item = {b.get("item_id"): b.get("cost") for b in (latest.basis if latest else [])}
    by_sub = {b.get("sub_recipe_id"): b.get("cost") for b in (latest.basis if latest else [])}
    lines = []
    for line in recipe.lines:
        item = db.get(PriceItem, line.item_id) if line.item_id else None
        sub = db.get(Recipe, line.sub_recipe_id) if line.sub_recipe_id else None
        lines.append(LineOut(id=line.id, ingredient_text=line.ingredient_text, item_id=line.item_id, item_name=item.name if item else None,
                             sub_recipe_id=line.sub_recipe_id, sub_recipe_name=sub.name if sub else None, qty=line.qty, unit=line.unit,
                             cost=by_item.get(line.item_id) if line.item_id else by_sub.get(line.sub_recipe_id)))
    history = [CostPoint(week_of=r.week_of, cost=r.cost) for r in db.query(PlateCost).filter(PlateCost.recipe_id == recipe.id)
               .order_by(PlateCost.week_of.desc()).limit(8)][::-1]
    sold, price = (engine.weekly_qty(db, recipe.restaurant_id, recipe.pos_item_id, today) if recipe.pos_item_id else (None, None))
    margin = round((price - latest.cost) / price * 100, 1) if (latest and price) else None
    return RecipeOut(id=recipe.id, name=recipe.name, pos_item_id=recipe.pos_item_id, yield_qty=recipe.yield_qty, yield_unit=recipe.yield_unit,
                     status=recipe.status, source=recipe.source, lines=lines, plate_cost=latest.cost if latest else None,
                     plate_cost_week=latest.week_of if latest else None, unpriced=list(latest.unpriced) if latest else [],
                     basis=list(latest.basis) if latest else [], history=history, sold_a_week=sold or None, price_a_plate=price or None, margin_pct=margin)


@router.get("", response_model=list[RecipeOut])
def list_recipes(user: User = Depends(current_user), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    return [_out(db, r) for r in db.query(Recipe).filter(Recipe.restaurant_id == restaurant_id).order_by(Recipe.name)]


def _recipe(db: Session, user: User, recipe_id: str) -> Recipe:
    recipe = db.get(Recipe, recipe_id)
    if recipe is None or recipe.restaurant_id != _scope(db, user):
        raise HTTPException(404, "No such recipe.")
    return recipe


@router.get("/{recipe_id}", response_model=RecipeOut)
def get_recipe(recipe_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _out(db, _recipe(db, user, recipe_id))


@router.post("/{recipe_id}/cost", response_model=RecipeOut)
def cost_now(recipe_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Plate cost for this week, now, for every confirmed recipe."""
    recipe = _recipe(db, user, recipe_id)
    engine.compute_plate_costs(db, recipe.restaurant_id)
    db.commit()
    return _out(db, recipe)


class LineIn(BaseModel):
    ingredient_text: str | None = Field(default=None, max_length=200)
    item_id: str | None = None
    sub_recipe_id: str | None = None
    qty: float = Field(gt=0)
    unit: str = Field(max_length=32)


class EditIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    yield_qty: float | None = Field(default=None, gt=0)
    yield_unit: str | None = Field(default=None, max_length=32)
    pos_item_id: str | None = None
    lines: list[LineIn]


@router.post("/{recipe_id}/edit")
def edit_recipe(recipe_id: str, body: EditIn, user: User = Depends(manager), db: Session = Depends(get_db)):
    """An edit is a `recipe` proposal, not a direct write: approving it confirms the new lines."""
    recipe = _recipe(db, user, recipe_id)
    for line in body.lines:
        if bool(line.item_id) == bool(line.sub_recipe_id):
            raise HTTPException(422, "Each line is one item or one sub-recipe.")
    action = engine.propose_recipe(
        db, recipe.restaurant_id, name=body.name or recipe.name, pos_item_id=body.pos_item_id or recipe.pos_item_id, source="user",
        yield_qty=body.yield_qty or recipe.yield_qty, yield_unit=body.yield_unit or recipe.yield_unit,
        ingredients=[{"text": l.ingredient_text or (db.get(PriceItem, l.item_id).name if l.item_id and db.get(PriceItem, l.item_id) else "line"),
                      "qty": l.qty, "unit": l.unit} for l in body.lines],
    )
    if action is None:
        raise HTTPException(409, "Five recipe proposals already this week; the edit is held until next week.")
    # The owner's own lines are exact: carry item and sub-recipe ids through rather than re-matching.
    payload = dict(action.payload)
    payload["lines"] = [{"ingredient_text": l.ingredient_text, "item_id": l.item_id, "sub_recipe_id": l.sub_recipe_id,
                         "item_name": (db.get(PriceItem, l.item_id).name if l.item_id and db.get(PriceItem, l.item_id) else None),
                         "qty": l.qty, "unit": l.unit, "matched": True} for l in body.lines]
    payload["unknown"] = []
    payload["question"] = f"Confirm the edited recipe for {payload['name']}."
    payload["note"] = payload["question"]
    action.payload = payload
    db.commit()
    return {"proposal_id": action.id}


@router.post("/{recipe_id}/reprice")
def reprice(recipe_id: str, user: User = Depends(manager), db: Session = Depends(get_db)):
    """A reprice suggestion as a proposal, for the owner to carry out in the POS."""
    recipe = _recipe(db, user, recipe_id)
    action = engine.propose_reprice(db, recipe.restaurant_id, recipe)
    if action is None:
        raise HTTPException(409, "No plate cost yet, or five reprice suggestions already this week.")
    db.commit()
    return {"proposal_id": action.id, "note": action.payload["note"]}


@router.post("/onboarding/run")
def run_onboarding(user: User = Depends(manager), db: Session = Depends(get_db)):
    """Propose recipes for the top sellers with none, now rather than at the weekly run."""
    restaurant_id = _scope(db, user)
    made = engine.onboarding(db, restaurant_id)
    db.commit()
    return {"proposed": len(made)}
