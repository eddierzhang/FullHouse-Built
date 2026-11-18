"""The Margin & Labor Engine's shared data: importing order-level sales, punches and payouts, and saying how
fresh each source is. Module pages (Menu costs, Usage, Ordering and prep, Labor, Channels) add their own routes
as each phase lands. The tenant always comes from the session."""

from datetime import date as _date
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


# --- Module E: Channels -----------------------------------------------------------------------------


@router.get("/channels")
def channels_list(response: Response, if_none_match: str | None = Header(default=None), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Items by channel and platform with the per-unit breakdown: price, fees, cost per plate, kept per item."""
    from app.margin_labor import channels_read

    restaurant_id = _module(db, user, flags.ML_CHANNELS)
    out = channels_read.overview(db, restaurant_id)
    statuses = [(i["finding"] or {}).get("proposal_status") for i in out["items"]]
    if _etag(response, if_none_match, out["computed_at"], out["state"], out["stale"], out["window_end"], out["match_rate"], out["header"], statuses, out["terms"]):
        return Response(status_code=304, headers={"ETag": response.headers["ETag"]})
    return out


class TermsIn(BaseModel):
    platform: str = Field(min_length=1, max_length=40)
    commission_pct: Decimal = Field(ge=0, lt=1, max_digits=8, decimal_places=6)
    processing_pct: Decimal = Field(default=Decimal(0), ge=0, lt=1, max_digits=8, decimal_places=6)
    fixed_fee_per_order: Decimal = Field(default=Decimal(0), ge=0, max_digits=12, decimal_places=4)
    packaging_cost_per_order: Decimal = Field(default=Decimal(0), ge=0, max_digits=12, decimal_places=4)
    effective_from: _date | None = None


@router.put("/channels/terms")
def channels_terms(body: TermsIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """A delivery platform's contract rates from a date on. They price every order with no payout row; the rates
    before that date are kept for the orders they applied to."""
    from app.margin_labor import channels, channels_read, menu_costs
    from app.margin_labor.models import ChannelTerms

    restaurant_id = _module(db, user, flags.ML_CHANNELS)
    if body.commission_pct + body.processing_pct >= 1:
        raise HTTPException(422, "Commission and processing together must be under 100%.")
    today = menu_costs.load(db, restaurant_id).today
    platform = body.platform.strip()
    held = db.query(ChannelTerms).filter(ChannelTerms.restaurant_id == restaurant_id, ChannelTerms.platform == platform).order_by(ChannelTerms.effective_from).all()
    # With no date, the first rates given for a platform cover its history; later ones start today.
    start = body.effective_from or (today if held else _date(2000, 1, 1))
    values = {"commission_pct": body.commission_pct, "processing_pct": body.processing_pct, "fixed_fee_per_order": body.fixed_fee_per_order,
              "packaging_cost_per_order": body.packaging_cost_per_order}
    same = next((t for t in held if t.effective_from == start), None)
    if same is not None:
        for name, value in values.items():
            setattr(same, name, value)
    else:
        for earlier in held:
            if earlier.effective_from < start and (earlier.effective_to is None or earlier.effective_to >= start):
                earlier.effective_to = start - timedelta(days=1)
        later = min((t.effective_from for t in held if t.effective_from > start), default=None)
        db.add(ChannelTerms(restaurant_id=restaurant_id, platform=platform, effective_from=start, effective_to=None if later is None else later - timedelta(days=1), **values))
    db.flush()
    channels.run(db, restaurant_id)
    db.commit()
    terms = db.query(ChannelTerms).filter(ChannelTerms.restaurant_id == restaurant_id).all()
    return {"terms": channels_read.terms_out(terms, today)}


@router.post("/channels/{row_id}/reprice")
def channels_reprice(row_id: str, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Ask for a platform price for a flagged item. A proposal: a person approves it, then changes the price on the platform."""
    from app.margin_labor import channels, menu_costs
    from app.margin_labor.models import ChannelItemMargin, ChannelTerms, Finding

    restaurant_id = _module(db, user, flags.ML_CHANNELS)
    row = db.get(ChannelItemMargin, row_id)
    if row is None or row.restaurant_id != restaurant_id:
        raise HTTPException(404, "No such item.")
    finding = db.query(Finding).filter(Finding.restaurant_id == restaurant_id, Finding.type == channels.FINDING, Finding.subject_id == f"{row.pos_item_id}|{row.platform}",
                                       Finding.status == "open").first()
    if finding is None:
        raise HTTPException(409, "This item is not flagged on this platform.")
    world = menu_costs.load(db, restaurant_id)
    terms = db.query(ChannelTerms).filter(ChannelTerms.restaurant_id == restaurant_id).all()
    action = channels.propose_reprice(db, world, row, finding, terms, row.window_end)
    if action is None:
        db.commit()
        raise HTTPException(409, "No price to suggest: the platform's rates leave no room, the price is already high enough, "
                                 "or three platform prices were already suggested this week.")
    finding.proposal_id = action.id
    db.commit()
    return {"proposal_id": action.id, "suggested_price": action.payload.get("suggested_price")}


# --- Forecast core and Module C: Ordering and prep ------------------------------------------------------


@router.get("/ordering/drafts")
def ordering_overview(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Open purchase order drafts with their lines and basis, the next cutoff, the demand chart and the average miss."""
    from app.margin_labor import ordering_read

    return ordering_read.overview(db, _module(db, user, flags.ML_ORDERING))


class DraftIn(BaseModel):
    supplier_id: str = Field(min_length=1, max_length=32)


@router.post("/ordering/drafts")
def ordering_draft(body: DraftIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Draft the order for a supplier's next delivery now, rather than four hours before its cutoff. Asking twice
    returns the draft already made. A person reviews, edits and sends."""
    from app.margin_labor import ordering

    restaurant_id = _module(db, user, flags.ML_ORDERING)
    action, why_not = ordering.draft(db, restaurant_id, body.supplier_id, lead_hours=None)
    db.commit()
    if action is None:
        reasons = {"no_schedule": "This supplier has no delivery schedule.", "no_forecast": "There are under 4 weeks of item sales to forecast from.",
                   "nothing_to_order": "No ingredient in a confirmed recipe was last delivered by this supplier.",
                   "cap": "Three order drafts were already made this week; this one is held."}
        raise HTTPException(409, reasons.get(why_not, "No draft was made."))
    return {"proposal_id": action.id, "status": action.status, "made": why_not == "created"}


class CorrectionIn(BaseModel):
    kind: str = Field(pattern="^(out|plenty)$")


def _ordering_change(db: Session, restaurant_id: str, change) -> dict:
    try:
        action = change()
    except LookupError as err:
        raise HTTPException(404, str(err)) from err
    except ValueError as err:
        raise HTTPException(409, str(err)) from err
    db.commit()
    return {"proposal_id": action.id, "total": action.payload.get("total"), "under_minimum": action.payload.get("under_minimum")}


@router.post("/ordering/lines/{line_ref}/correction")
def ordering_correction(line_ref: str, body: CorrectionIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """"We're out" sets the stock estimate to 0. "We have plenty" sets the line to 0 and the estimate to the need.
    A correction, not a count."""
    from app.margin_labor import ordering

    restaurant_id = _module(db, user, flags.ML_ORDERING)
    return _ordering_change(db, restaurant_id, lambda: ordering.correct_line(db, restaurant_id, line_ref, body.kind))


class LineQtyIn(BaseModel):
    qty: int = Field(ge=0, le=10_000)


@router.put("/ordering/lines/{line_ref}")
def ordering_line(line_ref: str, body: LineQtyIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Set a line's packs. Ordering fewer than suggested raises the stock estimate by the difference."""
    from app.margin_labor import ordering

    restaurant_id = _module(db, user, flags.ML_ORDERING)
    return _ordering_change(db, restaurant_id, lambda: ordering.set_line_qty(db, restaurant_id, line_ref, body.qty))


class ScheduleIn(BaseModel):
    supplier_id: str = Field(min_length=1, max_length=32)
    order_cutoff_weekday: int = Field(ge=0, le=6)
    order_cutoff_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    delivery_weekday: int = Field(ge=0, le=6)
    minimum_order_amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=4)


@router.put("/ordering/schedules")
def ordering_schedule(body: ScheduleIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """One delivery day of a supplier: when its order must be in, and the smallest order they take."""
    from app.margin_labor import ordering_read
    from app.margin_labor.models import SupplierSchedule
    from app.restaurant.models import Supplier

    restaurant_id = _module(db, user, flags.ML_ORDERING)
    supplier = db.get(Supplier, body.supplier_id)
    if supplier is None or supplier.restaurant_id != restaurant_id:
        raise HTTPException(404, "No such supplier.")
    row = db.query(SupplierSchedule).filter(SupplierSchedule.restaurant_id == restaurant_id, SupplierSchedule.supplier_id == supplier.id,
                                            SupplierSchedule.delivery_weekday == body.delivery_weekday).first()
    if row is None:
        row = SupplierSchedule(restaurant_id=restaurant_id, supplier_id=supplier.id, delivery_weekday=body.delivery_weekday, order_cutoff_weekday=body.order_cutoff_weekday)
        db.add(row)
    row.order_cutoff_weekday, row.order_cutoff_time, row.minimum_order_amount = body.order_cutoff_weekday, body.order_cutoff_time, body.minimum_order_amount
    db.commit()
    return {"schedules": ordering_read.schedules_out(db, restaurant_id)}


@router.delete("/ordering/schedules/{schedule_id}")
def ordering_schedule_remove(schedule_id: str, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    from app.margin_labor import ordering_read
    from app.margin_labor.models import SupplierSchedule

    restaurant_id = _module(db, user, flags.ML_ORDERING)
    row = db.get(SupplierSchedule, schedule_id)
    if row is None or row.restaurant_id != restaurant_id:
        raise HTTPException(404, "No such delivery day.")
    db.delete(row)
    db.commit()
    return {"schedules": ordering_read.schedules_out(db, restaurant_id)}


@router.get("/prep/{business_date}")
def prep_for(business_date: _date, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """The prep sheet for a date: each prepped item with its par, batch size and shelf life. "On hand" and "Prep" are
    filled in on paper; FullHouse does not record them."""
    from app.agents.models import AgentAction
    from app.margin_labor import demand, menu_costs, ordering

    restaurant_id = _module(db, user, flags.ML_ORDERING)
    world = menu_costs.load(db, restaurant_id)
    made = demand.forecast_items(db, restaurant_id)
    db.commit()
    lines = ordering.prep_lines(db, world, business_date) if made["state"] == "live" else []
    proposal = next((a for a in db.query(AgentAction).filter(AgentAction.restaurant_id == restaurant_id, AgentAction.action_type == "prep_sheet")
                     if (a.payload or {}).get("date") == business_date.isoformat()), None)
    return {"state": made["state"], "date": business_date, "today": world.today, "lines": lines,
            "proposal": None if proposal is None else {"id": proposal.id, "status": proposal.status}}


@router.post("/prep/{business_date}/propose")
def prep_propose(business_date: _date, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Propose the day's prep sheet now, rather than at 04:30. One per business date."""
    from app.margin_labor import ordering

    restaurant_id = _module(db, user, flags.ML_ORDERING)
    action, why_not = ordering.prep_sheet(db, restaurant_id, business_date)
    db.commit()
    if action is None:
        raise HTTPException(409, "No prep sheet: there is no forecast yet, or no dish uses a prepped item.")
    return {"proposal_id": action.id, "status": action.status, "made": why_not == "created"}


@router.get("/forecast/daily")
def forecast_daily(metric: str = "items", start: _date | None = None, end: _date | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """The daily forecast with its range and the drivers applied, and what happened on the days that have passed."""
    from app.margin_labor.models import ForecastInterval, IntervalActual
    from app.sales.models import SalesItem

    restaurant_id = _scope(db, user)
    if metric not in ("items", "orders", "covers", "net"):
        raise HTTPException(422, "The metric is items, orders, covers or net.")
    rows = db.query(ForecastInterval).filter(ForecastInterval.restaurant_id == restaurant_id, ForecastInterval.grain == "day", ForecastInterval.metric == metric,
                                             ForecastInterval.channel == "all", ForecastInterval.kind == "weekly")
    if start:
        rows = rows.filter(ForecastInterval.business_date >= start)
    if end:
        rows = rows.filter(ForecastInterval.business_date <= end)
    rows = rows.order_by(ForecastInterval.business_date).limit(120).all()
    days = [r.business_date for r in rows]
    actuals: dict = {}
    if days and metric == "items":
        actuals = dict(db.query(SalesItem.date, func.sum(SalesItem.qty)).filter(SalesItem.restaurant_id == restaurant_id, SalesItem.date.in_(days)).group_by(SalesItem.date))
    elif days:
        column = {"orders": IntervalActual.orders, "covers": IntervalActual.covers, "net": IntervalActual.net}[metric]
        actuals = dict(db.query(IntervalActual.business_date, func.sum(column)).filter(IntervalActual.restaurant_id == restaurant_id, IntervalActual.business_date.in_(days)).group_by(IntervalActual.business_date))
    return {"metric": metric, "days": [{"date": r.business_date, "p10": r.p10, "p50": r.p50, "p90": r.p90, "drivers_applied": r.drivers_applied,
                                        "actual": None if actuals.get(r.business_date) is None else float(actuals[r.business_date])} for r in rows]}


class DriverIn(BaseModel):
    date: _date
    kind: str = Field(pattern="^(local_event|promo|closure|holiday)$")
    name: str = Field(min_length=1, max_length=120)
    size: str | None = Field(default=None, pattern="^(small|medium|large)$")
    promo_type: str | None = Field(default=None, max_length=60)
    expected_multiplier: float | None = Field(default=None, ge=0.6, le=1.6)


@router.get("/drivers")
def drivers_list(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    """Events, promotions and closures the owner has entered, newest first."""
    from app.margin_labor.models import DemandDriver

    restaurant_id = _scope(db, user)
    rows = db.query(DemandDriver).filter(DemandDriver.restaurant_id == restaurant_id).order_by(DemandDriver.date.desc()).limit(200)
    return [{"id": r.id, "date": r.date, "kind": r.kind, "name": r.name, "payload": r.payload, "source": r.source} for r in rows]


@router.post("/drivers")
def drivers_add(body: DriverIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Add an event, a promotion, a closure or a holiday the calendar lacks. Adding the same one twice changes nothing."""
    from app.margin_labor.models import DemandDriver
    from app.platform import events

    restaurant_id = _scope(db, user)
    payload = {k: v for k, v in (("size", body.size), ("type", body.promo_type), ("expected_multiplier", body.expected_multiplier)) if v is not None}
    row = db.query(DemandDriver).filter(DemandDriver.restaurant_id == restaurant_id, DemandDriver.date == body.date, DemandDriver.slot == -1,
                                        DemandDriver.kind == body.kind, DemandDriver.name == body.name.strip()).first()
    if row is None:
        row = DemandDriver(restaurant_id=restaurant_id, date=body.date, slot=-1, kind=body.kind, name=body.name.strip(), source="owner")
        db.add(row)
    row.payload = payload
    db.flush()
    events.emit(db, restaurant_id, "drivers.updated", "demand_drivers", row.id, {"date": body.date.isoformat(), "kind": body.kind})
    db.commit()
    return {"id": row.id, "date": row.date, "kind": row.kind, "name": row.name, "payload": row.payload}


@router.delete("/drivers/{driver_id}")
def drivers_remove(driver_id: str, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    from app.margin_labor.models import DemandDriver
    from app.platform import events

    restaurant_id = _scope(db, user)
    row = db.get(DemandDriver, driver_id)
    if row is None or row.restaurant_id != restaurant_id:
        raise HTTPException(404, "No such entry.")
    db.delete(row)
    events.emit(db, restaurant_id, "drivers.updated", "demand_drivers", driver_id, {"removed": True})
    db.commit()
    return {"removed": driver_id}


# --- Module D: Labor (forecast, standards and requirements) -------------------------------------------


@router.get("/labor/weeks/{week_of}")
def labor_week(week_of: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """One week: the forecast by day with its range, and the people needed per role by half hour. `next` is the week being planned."""
    from app.margin_labor import labor_read, schedule_read

    restaurant_id = _module(db, user, flags.ML_LABOR)
    try:
        day = None if week_of == "next" else _date.fromisoformat(week_of)
    except ValueError as err:
        raise HTTPException(422, "The week is a date, or next.") from err
    out = labor_read.week(db, restaurant_id, day)
    return schedule_read.attach(db, restaurant_id, out, user)


@router.get("/labor/standards")
def labor_standards(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """One row per role: its driver, standard, minimum on floor, where the standard came from, and the data behind it."""
    from app.margin_labor import labor_read

    return labor_read.standards_page(db, _module(db, user, flags.ML_LABOR))


class StandardIn(BaseModel):
    role_key: str = Field(min_length=1, max_length=40, pattern="^[a-z0-9_]+$")
    driver_metric: str | None = Field(default=None, pattern="^(covers|orders|items|net|none)$")
    units_per_labor_hour: Decimal = Field(gt=0, le=100000, max_digits=12, decimal_places=4)
    min_on_floor: int = Field(default=0, ge=0, le=50)
    open_lead_minutes: int = Field(default=0, ge=0, le=480)
    close_lag_minutes: int = Field(default=0, ge=0, le=480)


@router.post("/labor/standards/preview")
def labor_standard_preview(body: StandardIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """What a changed standard would do to the role's hours over the next 7 days. Nothing is saved."""
    from app.margin_labor import labor

    restaurant_id = _module(db, user, flags.ML_LABOR)
    return labor.preview(db, restaurant_id, body.role_key, body.units_per_labor_hour, body.min_on_floor)


@router.put("/labor/standards")
def labor_standard_save(body: StandardIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Set a role's standard. An owner's value always wins over the learned one; requirements are worked out again."""
    from app.margin_labor import labor, labor_read
    from app.margin_labor.models import LaborRole, LaborStandard

    restaurant_id = _module(db, user, flags.ML_LABOR)
    service = labor.Service(db, restaurant_id)
    role = db.query(LaborRole).filter(LaborRole.restaurant_id == restaurant_id, LaborRole.key == body.role_key).first()
    if role is None:
        db.add(LaborRole(restaurant_id=restaurant_id, key=body.role_key, name=body.role_key.replace("_", " ").capitalize()))
    current = labor.standards(db, restaurant_id, service.today).get(body.role_key)
    metric, filt = (current.driver_metric, current.driver_filter or {}) if current is not None else labor.default_driver(body.role_key, service.has_covers)
    if body.driver_metric is not None and body.driver_metric != metric:
        metric, filt = body.driver_metric, {}
    row = db.query(LaborStandard).filter(LaborStandard.restaurant_id == restaurant_id, LaborStandard.role_key == body.role_key, LaborStandard.source == "manual",
                                         LaborStandard.effective_from == service.today).first()
    if row is None:
        row = LaborStandard(restaurant_id=restaurant_id, role_key=body.role_key, source="manual", effective_from=service.today, driver_metric=metric)
        db.add(row)
    row.driver_metric, row.driver_filter, row.units_per_labor_hour = metric, filt, body.units_per_labor_hour
    row.min_on_floor, row.open_lead_minutes, row.close_lag_minutes = body.min_on_floor, body.open_lead_minutes, body.close_lag_minutes
    db.flush()
    labor.forecast_labor(db, restaurant_id)
    db.commit()
    return labor_read.standards_page(db, restaurant_id)


@router.delete("/labor/standards/{role_key}")
def labor_standard_reset(role_key: str, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Drop the owner's value for a role and go back to the learned one."""
    from app.margin_labor import labor, labor_read
    from app.margin_labor.models import LaborStandard

    restaurant_id = _module(db, user, flags.ML_LABOR)
    removed = db.query(LaborStandard).filter(LaborStandard.restaurant_id == restaurant_id, LaborStandard.role_key == role_key, LaborStandard.source == "manual").all()
    if not removed:
        raise HTTPException(404, "This role has no value of your own to drop.")
    for row in removed:
        db.delete(row)
    db.flush()
    labor.forecast_labor(db, restaurant_id)
    db.commit()
    return labor_read.standards_page(db, restaurant_id)


# --- Module D: Labor (schedule generation, edits and publishing) --------------------------------------


def _schedule_body(db: Session, restaurant_id: str, schedule, user: User) -> dict:
    from app.margin_labor import schedule_read

    return schedule_read.out(db, restaurant_id, schedule, user)


@router.post("/labor/weeks/{week_of}/generate")
def labor_generate(week_of: _date, idempotency_key: str | None = Header(default=None), user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Queue the week's schedule to be generated, or regenerated around its locked shifts. Returns a job id to watch."""
    restaurant_id = _module(db, user, flags.ML_LABOR)
    monday = week_of - timedelta(days=week_of.weekday())
    job = jobs.enqueue(db, "derive", f"derive:{restaurant_id}:schedule_generate:{monday.isoformat()}",
                       {"restaurant_id": restaurant_id, "job": "schedule_generate", "week_of": monday.isoformat()}, restaurant_id=restaurant_id, max_attempts=1)
    db.commit()
    return {"job_id": job.id, "week_of": monday}


@router.get("/jobs/{job_id}")
def job_status(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """A queued job's status: queued, running, done or failed."""
    from app.platform.models import Job

    restaurant_id = _scope(db, user)
    job = db.get(Job, job_id)
    if job is None or job.restaurant_id != restaurant_id:
        raise HTTPException(404, "No such job.")
    return {"job_id": job.id, "status": {"queued": "queued", "active": "running", "done": "done", "failed": "failed"}.get(job.status, job.status),
            "error": "The job failed. Try again." if job.status == "failed" else None}


class ShiftIn(BaseModel):
    schedule_id: str = Field(min_length=1, max_length=32)
    date: _date | None = None
    #: Minutes from local midnight of the shift's business date, on the half hour.
    start: int | None = Field(default=None, ge=0, le=1800)
    end: int | None = Field(default=None, ge=30, le=1800)
    role_key: str | None = Field(default=None, max_length=40, pattern="^[a-z0-9_]+$")
    staff_id: str | None = Field(default=None, max_length=32)
    #: True to make it an open shift.
    unassign: bool = False
    locked: bool | None = None
    note: str | None = Field(default=None, max_length=200)


def _shift_change(db: Session, restaurant_id: str, user: User, change) -> dict:
    from app.margin_labor import schedule as schedules

    try:
        out = change()
    except LookupError as err:
        db.rollback()
        raise HTTPException(404, str(err)) from err
    except schedules.ScheduleError as err:
        db.rollback()  # a refused edit leaves nothing behind
        raise HTTPException(422, str(err)) from err
    db.commit()
    schedule = out[0] if isinstance(out, tuple) else out
    return _schedule_body(db, restaurant_id, schedule, user)


@router.post("/labor/shifts")
def labor_shift_add(body: ShiftIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Add a shift to a schedule. Returns the schedule with its totals worked out again."""
    from app.margin_labor import schedule as schedules

    restaurant_id = _module(db, user, flags.ML_LABOR)
    return _shift_change(db, restaurant_id, user, lambda: schedules.save_shift(
        db, restaurant_id, body.schedule_id, None, day=body.date, start=body.start, end=body.end, role_key=body.role_key,
        staff_id=None if body.unassign or body.staff_id is None else body.staff_id, locked=body.locked, note=body.note))


@router.patch("/labor/shifts/{shift_id}")
def labor_shift_edit(shift_id: str, body: ShiftIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Change a shift's times, role, person, lock or note. A lock keeps it through a regenerate."""
    from app.margin_labor import schedule as schedules

    restaurant_id = _module(db, user, flags.ML_LABOR)
    staff = None if body.unassign else (body.staff_id if body.staff_id is not None else "keep")
    return _shift_change(db, restaurant_id, user, lambda: schedules.save_shift(
        db, restaurant_id, body.schedule_id, shift_id, start=body.start, end=body.end, role_key=body.role_key, staff_id=staff, locked=body.locked, note=body.note))


@router.delete("/labor/shifts/{shift_id}")
def labor_shift_remove(shift_id: str, schedule_id: str, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    from app.margin_labor import schedule as schedules

    restaurant_id = _module(db, user, flags.ML_LABOR)
    return _shift_change(db, restaurant_id, user, lambda: schedules.delete_shift(db, restaurant_id, schedule_id, shift_id))


class PublishIn(BaseModel):
    confirm: bool = False


@router.post("/labor/schedules/{schedule_id}/publish")
def labor_publish(schedule_id: str, body: PublishIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Publish a schedule. Warnings (late notice, open shifts, a broken rule) come back first and need `confirm`.
    Publishing notifies nobody: a person tells the team."""
    from app.margin_labor import schedule as schedules
    from app.margin_labor.models import Schedule

    restaurant_id = _module(db, user, flags.ML_LABOR)
    try:
        out = schedules.publish(db, restaurant_id, schedule_id, user.email, body.confirm)
    except LookupError as err:
        raise HTTPException(404, str(err)) from err
    db.commit()
    return {**out, "schedule": _schedule_body(db, restaurant_id, db.get(Schedule, schedule_id), user)}


@router.get("/labor/schedules/{schedule_id}/export.csv")
def labor_export(schedule_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    """The schedule as a CSV file: who works when. It holds no wage and no cost."""
    from app.margin_labor import schedule as schedules

    restaurant_id = _module(db, user, flags.ML_LABOR)
    try:
        text = schedules.export_csv(db, restaurant_id, schedule_id)
    except LookupError as err:
        raise HTTPException(404, str(err)) from err
    return Response(content=text, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="schedule.csv"'})


class RulesIn(BaseModel):
    weekly_overtime_hours: float | None = Field(default=None, gt=0, le=168)
    daily_overtime_hours: float | None = Field(default=None, gt=0, le=24)
    min_rest_hours: float | None = Field(default=None, ge=0, le=48)
    min_shift_hours: float | None = Field(default=None, gt=0, le=16)
    max_shift_hours: float | None = Field(default=None, gt=0, le=16)
    max_hours_per_day: float | None = Field(default=None, gt=0, le=24)
    max_consecutive_days: int | None = Field(default=None, ge=1, le=14)
    break_after_hours: float | None = Field(default=None, gt=0, le=16)
    break_minutes: int | None = Field(default=None, ge=0, le=120)
    min_paid_hours: float | None = Field(default=None, ge=0, le=16)
    advance_notice_days: int | None = Field(default=None, ge=0, le=60)
    allow_on_call: bool | None = None
    minor_max_hours_per_day: float | None = Field(default=None, gt=0, le=24)
    minor_latest_end: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


def _rules_out(row) -> dict:
    from app.margin_labor import scheduling

    values = {name: (getattr(row, name) if row is not None else None) for name in scheduling.RULE_NAMES}
    minor = (row.minor_rules or {}) if row is not None else {}
    return {**values, "minor_max_hours_per_day": minor.get("max_hours_per_day"), "minor_latest_end": minor.get("latest_end"),
            "confirmed_by": row.confirmed_by if row is not None else None, "confirmed_at": row.confirmed_at if row is not None else None}


@router.get("/labor/rules")
def labor_rules(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """The restaurant's labor rules. Empty until the owner confirms them; FullHouse gives no legal advice."""
    from app.margin_labor.models import LaborRules

    restaurant_id = _module(db, user, flags.ML_LABOR)
    return _rules_out(db.query(LaborRules).filter(LaborRules.restaurant_id == restaurant_id).first())


@router.put("/labor/rules")
def labor_rules_save(body: RulesIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """The owner confirms the rules for their location. A rule left empty is not enforced, and drafts say so."""
    from app.auth.models import OWNER
    from app.margin_labor import scheduling
    from app.margin_labor.models import LaborRules

    restaurant_id = _module(db, user, flags.ML_LABOR)
    if user.role != OWNER:
        raise HTTPException(403, "Only the owner confirms labor rules.")
    if body.min_shift_hours is not None and body.max_shift_hours is not None and body.min_shift_hours > body.max_shift_hours:
        raise HTTPException(422, "The shortest shift is longer than the longest.")
    row = db.query(LaborRules).filter(LaborRules.restaurant_id == restaurant_id).first()
    if row is None:
        row = LaborRules(restaurant_id=restaurant_id)
        db.add(row)
    for name in scheduling.RULE_NAMES:
        setattr(row, name, getattr(body, name))
    row.minor_rules = {k: v for k, v in (("max_hours_per_day", body.minor_max_hours_per_day), ("latest_end", body.minor_latest_end)) if v is not None}
    row.confirmed_by, row.confirmed_at = user.email, datetime.now(timezone.utc)
    db.commit()
    return _rules_out(row)


class AvailabilityIn(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^(([01]\d|2[0-3]):[0-5]\d|24:00)$")


class TeamMemberIn(BaseModel):
    role_keys: list[str] = Field(default_factory=list, max_length=12)
    max_weekly_hours: float | None = Field(default=None, gt=0, le=168)
    desired_weekly_hours: float | None = Field(default=None, ge=0, le=168)
    min_weekly_hours: float | None = Field(default=None, ge=0, le=168)
    is_minor: bool = False
    active: bool = True
    #: Sent only to change it; left out, the rate stays as it is. Null removes it.
    hourly_rate: Decimal | None = Field(default=None, ge=0, le=1000, max_digits=12, decimal_places=4)
    set_rate: bool = False
    availability: list[AvailabilityIn] | None = None


def _team(db: Session, restaurant_id: str) -> list[dict]:
    from app.margin_labor import labor
    from app.margin_labor.models import EmployeeAvailability, EmployeeProfile, EmployeeWage
    from app.restaurant.models import Staff

    profiles = {p.staff_id: p for p in db.query(EmployeeProfile).filter(EmployeeProfile.restaurant_id == restaurant_id)}
    wages = {w.staff_id: w.hourly_rate for w in db.query(EmployeeWage).filter(EmployeeWage.restaurant_id == restaurant_id)}
    windows: dict[str, list] = {}
    for a in db.query(EmployeeAvailability).filter(EmployeeAvailability.restaurant_id == restaurant_id).order_by(EmployeeAvailability.weekday, EmployeeAvailability.start_time):
        windows.setdefault(a.staff_id, []).append({"weekday": a.weekday, "start_time": a.start_time, "end_time": a.end_time})
    out = []
    for staff in db.query(Staff).filter(Staff.restaurant_id == restaurant_id).order_by(Staff.name):
        p = profiles.get(staff.id)
        out.append({"staff_id": staff.id, "name": staff.name, "role_keys": list(p.role_keys) if p is not None and p.role_keys else [labor.slug(staff.role)],
                    "max_weekly_hours": p.max_weekly_hours if p else None, "desired_weekly_hours": p.desired_weekly_hours if p else None,
                    "min_weekly_hours": p.min_weekly_hours if p else None, "is_minor": bool(p.is_minor) if p else False, "active": p.active if p else True,
                    "hourly_rate": None if staff.id not in wages else str(wages[staff.id]), "availability": windows.get(staff.id, [])})
    return out


@router.get("/labor/team")
def labor_team(user: User = Depends(manager), db: Session = Depends(get_db)) -> list[dict]:
    """The staff list as scheduling sees it, with hourly rates. Owners and managers only: this is the one place a rate is returned."""
    return _team(db, _module(db, user, flags.ML_LABOR))


@router.put("/labor/team/{staff_id}")
def labor_team_save(staff_id: str, body: TeamMemberIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> list[dict]:
    """What scheduling knows about one person: their roles, hours, availability and, for costing only, their rate."""
    from app.margin_labor.models import EmployeeAvailability, EmployeeProfile, EmployeeWage
    from app.platform import events
    from app.restaurant.models import Staff

    restaurant_id = _module(db, user, flags.ML_LABOR)
    staff = db.get(Staff, staff_id)
    if staff is None or staff.restaurant_id != restaurant_id:
        raise HTTPException(404, "No such person.")
    profile = db.query(EmployeeProfile).filter(EmployeeProfile.restaurant_id == restaurant_id, EmployeeProfile.staff_id == staff_id).first()
    if profile is None:
        profile = EmployeeProfile(restaurant_id=restaurant_id, staff_id=staff_id)
        db.add(profile)
    profile.role_keys, profile.max_weekly_hours, profile.desired_weekly_hours = body.role_keys, body.max_weekly_hours, body.desired_weekly_hours
    profile.min_weekly_hours, profile.is_minor, profile.active = body.min_weekly_hours, body.is_minor, body.active
    if body.availability is not None:
        for window in body.availability:
            if window.end_time <= window.start_time:
                raise HTTPException(422, "An availability window ends after it starts.")
        db.query(EmployeeAvailability).filter(EmployeeAvailability.restaurant_id == restaurant_id, EmployeeAvailability.staff_id == staff_id).delete(synchronize_session=False)
        for window in body.availability:
            db.add(EmployeeAvailability(restaurant_id=restaurant_id, staff_id=staff_id, weekday=window.weekday, start_time=window.start_time, end_time=window.end_time))
    if body.set_rate:
        wage = db.query(EmployeeWage).filter(EmployeeWage.restaurant_id == restaurant_id, EmployeeWage.staff_id == staff_id).first()
        if body.hourly_rate is None:
            if wage is not None:
                db.delete(wage)
        elif wage is None:
            db.add(EmployeeWage(restaurant_id=restaurant_id, staff_id=staff_id, hourly_rate=body.hourly_rate))
        else:
            wage.hourly_rate = body.hourly_rate
        # The change is on record, with who made it. The rate itself is not: it never enters a log.
        events.emit(db, restaurant_id, "wage.changed", "staff", staff_id, {"changed_by": user.email})
    db.commit()
    return _team(db, restaurant_id)


# --- Module D: Labor (today) -------------------------------------------------------------------------


def _as_of(at: datetime | None) -> datetime | None:
    """The moment to read Today as of: now, unless the site allows a given moment (browser tests, demos)."""
    from app.config import settings

    if at is None or not settings.allow_time_travel:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


@router.get("/labor/today")
def labor_today(at: datetime | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Pace against the forecast, the revised forecast for the rest of the day, people on against people needed, and
    the open suggestion (for owners and managers)."""
    from app.margin_labor import intraday

    return intraday.today(db, _module(db, user, flags.ML_LABOR), user, _as_of(at))


def _decide(db: Session, user: User, adjustment_id: str, decision: str, at: datetime | None) -> dict:
    from app.margin_labor import intraday

    restaurant_id = _module(db, user, flags.ML_LABOR)
    try:
        row = intraday.decide(db, restaurant_id, adjustment_id, decision, user.email, _as_of(at))
    except LookupError as err:
        raise HTTPException(404, str(err)) from err
    except ValueError as err:
        db.commit()
        raise HTTPException(409, str(err)) from err
    db.commit()
    return {"id": row.id, "status": row.status}


@router.post("/labor/adjustments/{adjustment_id}/accept")
def labor_adjustment_accept(adjustment_id: str, at: datetime | None = None, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Record that the manager took the suggestion. The manager tells the employee; FullHouse messages nobody."""
    return _decide(db, user, adjustment_id, "accepted", at)


@router.post("/labor/adjustments/{adjustment_id}/dismiss")
def labor_adjustment_dismiss(adjustment_id: str, at: datetime | None = None, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Set the suggestion aside. No new one is made for that role for an hour."""
    return _decide(db, user, adjustment_id, "dismissed", at)
