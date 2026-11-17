"""The Margin & Labor Engine's shared data: importing order-level sales, punches and payouts, and saying how
fresh each source is. Module pages (Menu costs, Usage, Ordering and prep, Labor, Channels) add their own routes
as each phase lands. The tenant always comes from the session."""

from datetime import datetime, timedelta, timezone

from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.deps import current_user, manager
from app.auth.models import User
from app.db.session import get_db
from app.margin_labor import csv_import
from app.margin_labor.models import ChannelPayout, IntervalActual, LaborPunch, SalesOrder
from app.platform import flags, jobs
from app.restaurant.inventory import scope_for

router = APIRouter(prefix="/api/v1/margin-labor", tags=["margin-labor"])

#: How old each source may be before a page that rests on it says so (spec section 4).
FRESHNESS_HOURS = {"orders": 36, "punches": 36}


def _scope(db: Session, user: User) -> str:
    restaurant_id = scope_for(db, user)
    if restaurant_id is None:
        raise HTTPException(404, "Set up your restaurant first.")
    if not flags.any_margin_labor(db, restaurant_id):
        raise HTTPException(404, "None of the margin and labor modules is switched on for this restaurant (Profile, Features).")
    return restaurant_id


class ImportIn(BaseModel):
    kind: str = Field(pattern="^(orders|punches|payouts)$")
    text: str = Field(min_length=1, max_length=40_000_000)


@router.post("/data/import")
def import_csv(body: ImportIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Import one CSV export. Read-only for the source, and safe to send twice: every row is matched on its own
    id, so the same file again changes nothing. Rows that can't be read are left out and listed."""
    restaurant_id = _scope(db, user)
    result = csv_import.IMPORTERS[body.kind](db, restaurant_id, body.text)
    db.commit()
    jobs.drain(db, queues=("derive",), limit=400)  # small imports show at once; a large one finishes in the worker
    db.commit()
    return result.as_dict()


@router.get("/data/status")
def data_status(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """What has been imported, how far it reaches, and whether each source is older than its limit."""
    restaurant_id = _scope(db, user)
    now = datetime.now(timezone.utc)

    def source(model, moment, name: str) -> dict:
        count, first, last, synced = db.query(func.count(model.id), func.min(moment), func.max(moment), func.max(model.updated_at)).filter(
            model.restaurant_id == restaurant_id).one()
        limit = FRESHNESS_HOURS.get(name)
        stale = bool(count and limit and synced is not None and _aware(synced) < now - timedelta(hours=limit))
        return {"rows": count, "first": first, "last": last, "last_import_at": synced, "stale": stale, "limit_hours": limit}

    orders = source(SalesOrder, SalesOrder.opened_at, "orders")
    days = db.query(func.count(func.distinct(SalesOrder.business_date))).filter(SalesOrder.restaurant_id == restaurant_id).scalar() or 0
    rolled = db.query(func.count(func.distinct(IntervalActual.business_date))).filter(IntervalActual.restaurant_id == restaurant_id).scalar() or 0
    return {"orders": {**orders, "business_dates": days, "rolled_up_dates": rolled},
            "punches": source(LaborPunch, LaborPunch.clock_in, "punches"),
            "payouts": source(ChannelPayout, ChannelPayout.paid_at, "payouts"),
            "modules": {flag: flags.enabled(db, restaurant_id, flag) for flag in flags.ML_FLAGS}}


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


# --- shared: the Why panel, digest cards ------------------------------------------------------------


def _module(db: Session, user: User, flag: str) -> str:
    """The signed-in user's restaurant, when this module is switched on for it."""
    restaurant_id = scope_for(db, user)
    if restaurant_id is None:
        raise HTTPException(404, "Set up your restaurant first.")
    if not flags.enabled(db, restaurant_id, flag):
        raise HTTPException(404, "This module is not switched on for this restaurant (Profile, Features).")
    return restaurant_id


def _etag(response: Response, if_none_match: str | None, *parts) -> bool:
    """Set the ETag from what the page was computed from; True when the caller already has it."""
    import hashlib

    tag = '"' + hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:24] + '"'
    response.headers["ETag"] = tag
    return if_none_match == tag


@router.get("/why/{table}/{row_id}")
def why(table: str, row_id: str, figure: str = "plate_cost", user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """The stored basis of one derived row, for the Why panel. It reads what was stored; it never recomputes."""
    from app.margin_labor import whys

    restaurant_id = _scope(db, user)
    out = whys.explain(db, restaurant_id, table, row_id, figure, user)
    if out is None:
        raise HTTPException(404, "No such figure.")
    return out


@router.get("/digest-cards")
def digest_cards(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    """At most one card per module that is on, for the Digest."""
    from app.margin_labor import cards

    restaurant_id = _scope(db, user)
    return cards.build(db, restaurant_id, user)


# --- Module A: Menu costs ---------------------------------------------------------------------------


@router.get("/menu-costs")
def menu_costs_list(response: Response, if_none_match: str | None = Header(default=None), user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    """Dishes with their current cost, food cost, 28-day change and units sold, largest dollars a week first."""
    from app.margin_labor import menu_costs_read

    restaurant_id = _module(db, user, flags.ML_MENU_COSTS)
    out = menu_costs_read.overview(db, restaurant_id)
    proposals = [(d["finding"] or {}).get("proposal_status") for d in out["dishes"]]
    if _etag(response, if_none_match, out["computed_at"], out["state"], out["stale"], out["today"], len(out["dishes"]), out["header"], proposals):
        return Response(status_code=304, headers={"ETag": response.headers["ETag"]})
    return out


@router.get("/menu-costs/{recipe_id}")
def menu_cost_detail(recipe_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """One dish: its lines, 26 weeks of cost, and its open finding."""
    from app.margin_labor import menu_costs_read

    restaurant_id = _module(db, user, flags.ML_MENU_COSTS)
    out = menu_costs_read.detail(db, restaurant_id, recipe_id)
    if out is None:
        raise HTTPException(404, "No such dish.")
    return out


@router.post("/menu-costs/{recipe_id}/reprice")
def menu_cost_reprice(recipe_id: str, idempotency_key: str | None = Header(default=None), user: User = Depends(manager),
                      db: Session = Depends(get_db)) -> dict:
    """Ask for a reprice suggestion for a dish. It is a proposal: a person approves it, then changes the price in
    the POS. Asking twice returns the one already waiting."""
    from app.margin_labor import menu_costs
    from app.margin_labor.models import DishCost, Finding

    restaurant_id = _module(db, user, flags.ML_MENU_COSTS)
    world = menu_costs.load(db, restaurant_id)
    cost = db.query(DishCost).filter(DishCost.restaurant_id == restaurant_id, DishCost.recipe_id == recipe_id, DishCost.is_current.is_(True)).first()
    if cost is None or recipe_id not in world.rows:
        raise HTTPException(404, "No such dish.")
    finding = db.query(Finding).filter(Finding.restaurant_id == restaurant_id, Finding.type == menu_costs.FINDING, Finding.subject_id == recipe_id,
                                       Finding.status == "open").first()
    action = menu_costs.propose_reprice(db, world, world.rows[recipe_id], cost, finding)
    if action is None:
        db.commit()  # a hold-back is logged
        raise HTTPException(409, "No price to suggest: the dish has no menu price or an unpriced line, the price cap leaves no room, "
                                 "or three reprice suggestions were already made this week.")
    if finding is not None and finding.proposal_id != action.id:
        finding.proposal_id = action.id
    db.commit()
    return {"proposal_id": action.id, "suggested_price": action.payload.get("suggested_price"), "note": action.payload.get("note")}


class RecipeLineIn(BaseModel):
    ingredient_text: str | None = Field(default=None, max_length=200)
    item_id: str | None = Field(default=None, max_length=32)
    sub_recipe_id: str | None = Field(default=None, max_length=32)
    qty: Decimal = Field(gt=0, max_digits=12, decimal_places=4)
    unit: str = Field(min_length=1, max_length=32)
    yield_pct: Decimal = Field(default=Decimal(1), gt=0, le=1, max_digits=6, decimal_places=4)


class RecipeIn(BaseModel):
    kind: str | None = Field(default=None, pattern="^(menu_item|sub_recipe)$")
    portions: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=4)
    yield_qty: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=4)
    yield_unit: str | None = Field(default=None, min_length=1, max_length=32)
    lines: list[RecipeLineIn] = Field(min_length=1, max_length=80)


@router.put("/recipes/{recipe_id}")
def save_recipe(recipe_id: str, body: RecipeIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Edit a recipe and its lines, then recost it and every recipe that uses it. A save that would make a recipe
    use itself, through any chain of sub-recipes, is rejected."""
    from app.margin_labor import costing, menu_costs
    from app.prices.models import PriceItem
    from app.recipes.models import Recipe, RecipeLine

    restaurant_id = _module(db, user, flags.ML_MENU_COSTS)
    recipe = db.get(Recipe, recipe_id)
    if recipe is None or recipe.restaurant_id != restaurant_id:
        raise HTTPException(404, "No such recipe.")
    for line in body.lines:
        if bool(line.item_id) == bool(line.sub_recipe_id):
            raise HTTPException(422, "Each line is one invoice item or one sub-recipe.")
        if line.item_id:
            item = db.get(PriceItem, line.item_id)
            if item is None or item.restaurant_id != restaurant_id:
                raise HTTPException(422, "A line names an item this restaurant does not have.")
        else:
            sub = db.get(Recipe, line.sub_recipe_id)
            if sub is None or sub.restaurant_id != restaurant_id:
                raise HTTPException(422, "A line names a sub-recipe this restaurant does not have.")
    world = menu_costs.load(db, restaurant_id)
    if costing.would_cycle(world.recipes, recipe_id, {l.sub_recipe_id for l in body.lines if l.sub_recipe_id}):
        raise HTTPException(422, "This would make the recipe use itself through its sub-recipes. Remove that line.")
    if body.kind is not None:
        recipe.kind = body.kind
    if body.portions is not None:
        recipe.portions = body.portions
    if body.yield_qty is not None:
        recipe.yield_qty = float(body.yield_qty)
    if body.yield_unit is not None:
        recipe.yield_unit = body.yield_unit
    for line in list(recipe.lines):
        db.delete(line)
    db.flush()
    for position, line in enumerate(body.lines):
        db.add(RecipeLine(restaurant_id=restaurant_id, recipe_id=recipe.id, position=position, item_id=line.item_id, sub_recipe_id=line.sub_recipe_id,
                          qty=float(line.qty), unit=line.unit, yield_pct=line.yield_pct, ingredient_text=(line.ingredient_text or "").strip() or None))
    db.flush()
    db.expire(recipe)
    done = menu_costs.recost(db, restaurant_id, recipe_ids={recipe_id})
    db.commit()
    return {"recipe_id": recipe_id, **done}


# --- Module B: Usage --------------------------------------------------------------------------------


@router.get("/usage")
def usage_list(response: Response, if_none_match: str | None = Header(default=None), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Ingredients with expected use, purchased, unexplained use and dollars a week, largest first."""
    from app.margin_labor import usage_read

    restaurant_id = _module(db, user, flags.ML_USAGE)
    out = usage_read.overview(db, restaurant_id)
    if _etag(response, if_none_match, out["computed_at"], out["state"], out["stale"], out["window_end"], out["coverage"], out["header"], len(out["items"])):
        return Response(status_code=304, headers={"ETag": response.headers["ETag"]})
    return out


@router.get("/usage/{item_id}")
def usage_detail(item_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """One ingredient: eight weekly windows and the dishes that use it."""
    from app.margin_labor import usage_read

    restaurant_id = _module(db, user, flags.ML_USAGE)
    out = usage_read.detail(db, restaurant_id, item_id)
    if out is None:
        raise HTTPException(404, "No such ingredient.")
    return out


class TrackIn(BaseModel):
    track_usage: bool


@router.put("/usage/{item_id}/tracking")
def usage_tracking(item_id: str, body: TrackIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Leave an ingredient out of Usage (salt, fryer oil), or bring it back. The windows are worked out again."""
    from app.margin_labor import usage
    from app.prices.models import PriceItem

    restaurant_id = _module(db, user, flags.ML_USAGE)
    item = db.get(PriceItem, item_id)
    if item is None or item.restaurant_id != restaurant_id:
        raise HTTPException(404, "No such ingredient.")
    item.track_usage = body.track_usage
    db.flush()
    done = usage.run(db, restaurant_id)
    db.commit()
    return {"item_id": item_id, "track_usage": item.track_usage, **done}


@router.post("/findings/{finding_id}/dismiss")
def dismiss_finding(finding_id: str, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Set a finding aside. It stays aside while it remains true, and closes when it stops being true."""
    from app.margin_labor.models import Finding

    restaurant_id = _scope(db, user)
    finding = db.get(Finding, finding_id)
    if finding is None or finding.restaurant_id != restaurant_id:
        raise HTTPException(404, "No such finding.")
    if finding.status == "open":
        finding.status, finding.note = "dismissed", f"Dismissed by {user.email}."
    db.commit()
    return {"id": finding.id, "status": finding.status}
