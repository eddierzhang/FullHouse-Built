"""Module C, ordering and prep (spec section 7): draft purchase orders and daily prep sheets from the item
forecast, with a running stock estimate and no counts.

A draft is a `purchase_order` proposal: a person reviews, edits and approves it, and then sends the order
themselves. Nothing here sends anything, and the v3 autopilot never touches these drafts (it only acts on its own).
Corrections ("We're out", "We have plenty", an edited quantity) are corrections to the estimate, not counts.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from statistics import median

from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.agents.models import AgentAction
from app.margin_labor import costing, demand, menu_costs, ordering_math, price_points, words
from app.margin_labor.business import zone
from app.margin_labor.models import DishCost, StockEstimate, SupplierSchedule, UsageVariance
from app.platform import events, flags
from app.prices.models import PriceItem
from app.restaurant.models import Supplier
from app.sales.models import SalesItem

logger = logging.getLogger(__name__)

ACTION = "purchase_order"
MODULE = "ordering"
DRAFT_LEAD_HOURS = 4
START_DAYS = 28
CHECK_FIRST_DRAFTS = 2
QTY = Decimal("0.0001")
CENT = Decimal("0.01")


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


# --- when a supplier delivers ----------------------------------------------------------------------------


def upcoming(schedules: list[SupplierSchedule], now_local: datetime, weeks: int = 3) -> list[tuple[datetime, date, SupplierSchedule]]:
    """(cutoff, delivery date, schedule row) for each delivery whose cutoff has not passed, soonest first."""
    out = []
    for row in schedules:
        hour, _, minute = row.order_cutoff_time.partition(":")
        for week in range(weeks):
            day = now_local.date() + timedelta(days=(row.order_cutoff_weekday - now_local.weekday()) % 7 + 7 * week)
            cutoff = datetime.combine(day, time(int(hour), int(minute or 0)), tzinfo=now_local.tzinfo)
            if cutoff < now_local:
                continue
            ahead = (row.delivery_weekday - day.weekday()) % 7
            out.append((cutoff, day + timedelta(days=ahead or 7), row))  # a delivery is after its cutoff's day
    return sorted(out, key=lambda t: (t[0], t[1]))


def next_delivery_after(schedules: list[SupplierSchedule], delivery: date) -> date:
    """The supplier's next delivery date after `delivery`: where this order's coverage period ends."""
    weekdays = {row.delivery_weekday for row in schedules}
    for ahead in range(1, 8):
        if (delivery + timedelta(days=ahead)).weekday() in weekdays:
            return delivery + timedelta(days=ahead)
    return delivery + timedelta(days=7)


# --- the stock estimate ---------------------------------------------------------------------------------


class Stock:
    """What the estimate reads, loaded once: usage by day from sales and recipes, receipts by day from invoices."""

    def __init__(self, db: Session, world):
        self.db, self.world = db, world
        first = world.today - timedelta(days=120)
        self.use: dict[str, dict[str, Decimal]] = {}
        for recipe in sorted(world.recipes.values(), key=lambda r: (r.name, r.id)):
            row = world.rows[recipe.id]
            if recipe.confirmed and recipe.kind == "menu_item" and row.pos_item_id and row.pos_item_id not in self.use:
                self.use[row.pos_item_id] = costing.usage(recipe, world.recipes, world.items)
        self.usage: dict[str, dict[date, Decimal]] = {}
        for pos, day, qty in db.query(SalesItem.pos_item_id, SalesItem.date, func.sum(SalesItem.qty)).filter(
                SalesItem.restaurant_id == world.restaurant_id, SalesItem.date >= first, SalesItem.date < world.today).group_by(SalesItem.pos_item_id, SalesItem.date):
            for item_id, per_portion in self.use.get(pos, {}).items():
                held = self.usage.setdefault(item_id, {})
                held[day] = held.get(day, Decimal(0)) + _dec(qty or 0) * per_portion
        self.points = price_points.load(db, world.restaurant_id, set(self.usage) | {i for u in self.use.values() for i in u})
        self.rows = {r.canonical_item_id: r for r in db.query(StockEstimate).filter(StockEstimate.restaurant_id == world.restaurant_id)}
        self.loss: dict[str, Decimal] = {}
        if flags.enabled(db, world.restaurant_id, flags.ML_USAGE):
            newest = db.query(func.max(UsageVariance.window_end)).filter(UsageVariance.restaurant_id == world.restaurant_id).scalar()
            for row in db.query(UsageVariance).filter(UsageVariance.restaurant_id == world.restaurant_id, UsageVariance.window_end == newest):
                self.loss[row.canonical_item_id] = ordering_math.loss_factor(row.unexplained_qty, row.theoretical_qty, row.state == "live")

    def receipts(self, item_id: str) -> dict[date, Decimal]:
        out: dict[date, Decimal] = {}
        for p in self.points.get(item_id, []):
            if not p.outlier:
                out[p.day] = out.get(p.day, Decimal(0)) + p.qty
        return out

    def median_delivery(self, item_id: str) -> Decimal:
        per_invoice: dict[str, Decimal] = {}
        for p in self.points.get(item_id, []):
            if not p.outlier and p.day >= self.world.today - timedelta(days=56):
                per_invoice[p.invoice_id] = per_invoice.get(p.invoice_id, Decimal(0)) + p.qty
        return Decimal(median(per_invoice.values())) if per_invoice else Decimal(0)

    def supplier_of(self, item_id: str) -> str | None:
        usable = [p for p in self.points.get(item_id, []) if not p.outlier]
        return max(usable, key=lambda p: p.key).supplier_id if usable else None

    def estimate(self, item_id: str) -> StockEstimate:
        """The estimate as of the start of today: replayed from its anchor through yesterday, deliveries in and
        recipe usage out. The first time, the anchor is half a median delivery, 28 days back."""
        today = self.world.today
        row = self.rows.get(item_id)
        if row is None:
            row = StockEstimate(restaurant_id=self.world.restaurant_id, canonical_item_id=item_id, est_on_hand=Decimal(0), as_of=today,
                                basis={"anchor_date": (today - timedelta(days=START_DAYS)).isoformat(), "anchor_qty": str((self.median_delivery(item_id) / 2).quantize(QTY)),
                                       "anchor_kind": "start"})
            self.db.add(row)
            self.rows[item_id] = row
        anchor, est = date.fromisoformat(row.basis["anchor_date"]), Decimal(row.basis["anchor_qty"])
        receipts, usage, loss = self.receipts(item_id), self.usage.get(item_id, {}), self.loss.get(item_id, Decimal(0))
        day = anchor
        while day < today:
            est = ordering_math.next_estimate(est, receipts.get(day, Decimal(0)), usage.get(day, Decimal(0)), loss)
            day += timedelta(days=1)
        est = est.quantize(QTY)
        if row.est_on_hand != est or row.as_of != today:
            row.est_on_hand, row.as_of = est, today
        return row

    def correct(self, item_id: str, qty: Decimal, kind: str) -> StockEstimate:
        """A person's correction: the estimate is `qty` as of today, and is replayed from here."""
        row = self.estimate(item_id)
        row.basis = {"anchor_date": self.world.today.isoformat(), "anchor_qty": str(max(Decimal(0), qty).quantize(QTY)), "anchor_kind": kind}
        row.est_on_hand, row.as_of, row.last_correction_at = max(Decimal(0), qty).quantize(QTY), self.world.today, datetime.now(timezone.utc)
        return row


# --- the draft order --------------------------------------------------------------------------------------


def build_lines(db: Session, world, stock: Stock, supplier_id: str, delivery: date, period_end: date, run_id: str | None) -> list[dict]:
    """One line per ingredient this supplier last delivered and a confirmed dish uses, with everything it rests on."""
    need_days = demand.ingredient_demand(db, world, world.today, period_end - timedelta(days=1))
    lines = []
    for item_id in sorted(need_days, key=lambda i: world.items[i].name if i in world.items else i):
        if stock.supplier_of(item_id) != supplier_id or item_id not in world.items:
            continue
        item = db.get(PriceItem, item_id)
        base_unit, factor = price_points.base_unit(item)
        pack = (_dec(item.pack_size or 1) * factor).quantize(QTY)
        by_day = need_days[item_id]
        zero = (Decimal(0), Decimal(0), Decimal(0))
        period = [by_day.get(delivery + timedelta(days=n), zero) for n in range((period_end - delivery).days)]
        before = sum((by_day.get(world.today + timedelta(days=n), zero)[1] for n in range((delivery - world.today).days)), Decimal(0))
        est = stock.estimate(item_id)
        at_delivery = ordering_math.at_delivery(est.est_on_hand, before).quantize(QTY)
        loss = stock.loss.get(item_id, Decimal(0))
        out = ordering_math.order_quantity(period, item.shelf_life_days, loss, at_delivery, pack)
        price = price_points.price_as_of(stock.points.get(item_id), date.max, "latest")
        pack_price = (price.unit_price * pack).quantize(CENT) if price is not None else None
        lines.append({
            "line_id": item_id, "item_id": item_id, "description": item.name, "qty": out.packs, "unit": item.pack_unit or "case",
            "unit_price_expected": None if pack_price is None else float(pack_price), "suggested_qty": out.packs,
            "pack_base_qty": str(pack), "base_unit": base_unit, "expected_use": str(out.sum_p50), "est_on_hand": str(est.est_on_hand),
            "est_on_hand_at_delivery": str(at_delivery), "need": str(out.need), "pack_price": None if pack_price is None else str(pack_price),
            "line_total": str((pack_price * out.packs).quantize(CENT)) if pack_price is not None else None, "correction": None,
            "basis": {"forecast_run_id": run_id, "period_start": delivery.isoformat(), "period_end": period_end.isoformat(), "days_used": out.days_used,
                      "shelf_life_days": item.shelf_life_days, "sum_p50": str(out.sum_p50), "root_sum_variance": str(out.root_sum_variance), "z": str(out.z),
                      "period_demand": str(out.period_demand), "loss_factor": str(loss), "need": str(out.need), "est_on_hand": str(est.est_on_hand),
                      "est_as_of": est.as_of.isoformat(), "est_anchor": est.basis, "use_before_delivery": str(before.quantize(QTY)),
                      "est_on_hand_at_delivery": str(at_delivery), "raw_qty": str(out.raw_qty), "pack_size": str(pack), "packs": out.packs,
                      "calc_version": ordering_math.CALC_VERSION,
                      "days": [{"date": (delivery + timedelta(days=n)).isoformat(), "p10": str(lo.quantize(QTY)), "p50": str(mid.quantize(QTY)), "p90": str(hi.quantize(QTY))}
                               for n, (lo, mid, hi) in enumerate(period)]},
        })
    return lines


def _retotal(payload: dict) -> None:
    total = sum((Decimal(l["pack_price"]) * Decimal(str(l["qty"])) for l in payload["lines"] if l.get("pack_price")), Decimal(0)).quantize(CENT)
    payload["total"] = str(total)
    minimum = payload.get("minimum_order_amount")
    payload["under_minimum"] = bool(minimum is not None and total < Decimal(minimum))
    for line in payload["lines"]:
        line["line_total"] = str((Decimal(line["pack_price"]) * Decimal(str(line["qty"]))).quantize(CENT)) if line.get("pack_price") else None
    ordered = [l for l in payload["lines"] if Decimal(str(l["qty"])) > 0]
    payload["note"] = (f"{len(ordered)} of {len(payload['lines'])} lines to order from {payload['supplier_name']} for {payload['delivery_label']}, about {words.dollars(total)}."
                       + (f" Under the {words.dollars(minimum)} minimum." if payload["under_minimum"] else "")
                       + (" Check quantities: the stock estimate is new." if payload.get("check_quantities") else "")
                       + " Nothing is sent: you send the order.")


def existing_draft(db: Session, restaurant_id: str, supplier_id: str, delivery: date, statuses=("pending", "approved", "rejected")) -> AgentAction | None:
    for action in db.query(AgentAction).filter(AgentAction.restaurant_id == restaurant_id, AgentAction.action_type == ACTION, AgentAction.status.in_(statuses)).order_by(AgentAction.created_at.desc()):
        p = action.payload or {}
        if p.get("module") == MODULE and p.get("supplier_id") == supplier_id and p.get("delivery_date") == delivery.isoformat():
            return action
    return None


def draft(db: Session, restaurant_id: str, supplier_id: str, now: datetime | None = None, lead_hours: int | None = DRAFT_LEAD_HOURS) -> tuple[AgentAction | None, str]:
    """One draft for the supplier's next delivery whose cutoff has not passed: (the proposal, why not when None).
    With `lead_hours`, only once the cutoff is that near; None drafts it now. One draft per supplier and delivery
    date, ever: a replay finds the one already made."""
    from app.agents import caps
    from app.restaurant.po_draft import _system_run

    now = now or datetime.now(timezone.utc)
    world = menu_costs.load(db, restaurant_id, now)
    supplier = db.get(Supplier, supplier_id)
    schedules = db.query(SupplierSchedule).filter(SupplierSchedule.restaurant_id == restaurant_id, SupplierSchedule.supplier_id == supplier_id).all()
    if supplier is None or supplier.restaurant_id != restaurant_id or not schedules:
        return None, "no_schedule"
    local = now.astimezone(zone(world.conf.timezone))
    ahead = upcoming(schedules, local)
    if not ahead:
        return None, "no_schedule"
    cutoff, delivery, row = ahead[0]
    if lead_hours is not None and cutoff - local > timedelta(hours=lead_hours):
        return None, "too_early"
    held = existing_draft(db, restaurant_id, supplier_id, delivery)
    if held is not None:
        return held, "exists"
    made = demand.forecast_items(db, restaurant_id, now)
    if made["state"] != "live":
        return None, "no_forecast"
    stock = Stock(db, world)
    period_end = next_delivery_after(schedules, delivery)
    lines = build_lines(db, world, stock, supplier_id, delivery, period_end, made.get("run_id"))
    if not lines:
        db.flush()
        return None, "nothing_to_order"
    if not caps.within(db, restaurant_id, ACTION, world.today):
        return None, "cap"
    earlier = sum(1 for a in db.query(AgentAction).filter(AgentAction.restaurant_id == restaurant_id, AgentAction.action_type == ACTION)
                  if (a.payload or {}).get("module") == MODULE and (a.payload or {}).get("supplier_id") == supplier_id)
    run = _system_run(db, restaurant_id, f"Order drafted for {supplier.name}, delivery {delivery.isoformat()}")
    payload = {"module": MODULE, "drafted_by": "margin_labor", "supplier_id": supplier_id, "supplier_name": supplier.name, "delivery_date": delivery.isoformat(),
               "delivery_label": words.day(delivery), "cutoff_at": cutoff.astimezone(timezone.utc).isoformat(), "period_start": delivery.isoformat(),
               "period_end": period_end.isoformat(), "forecast_run_id": made.get("run_id"), "check_quantities": earlier < CHECK_FIRST_DRAFTS,
               "minimum_order_amount": None if row.minimum_order_amount is None else str(row.minimum_order_amount), "lines": lines, "item_name": supplier.name}
    _retotal(payload)
    action = AgentAction(restaurant_id=restaurant_id, run_id=run.id, agent_definition_id=run.agent_definition_id, action_type=ACTION, payload=payload)
    db.add(action)
    db.flush()
    events.emit(db, restaurant_id, "order_draft.created", "agent_actions", action.id, {"supplier_id": supplier_id, "delivery_date": delivery.isoformat(), "lines": len(lines)})
    return action, "created"


def _line(db: Session, restaurant_id: str, line_ref: str) -> tuple[AgentAction, dict]:
    action_id, _, item_id = line_ref.partition(".")
    action = db.get(AgentAction, action_id)
    if action is None or action.restaurant_id != restaurant_id or action.action_type != ACTION or (action.payload or {}).get("module") != MODULE:
        raise LookupError("No such order line.")
    if action.status != "pending":
        raise ValueError("This draft was already decided.")
    for line in action.payload["lines"]:
        if line["line_id"] == item_id:
            return action, line
    raise LookupError("No such order line.")


def correct_line(db: Session, restaurant_id: str, line_ref: str, kind: str) -> AgentAction:
    """"We're out" sets the estimate to 0 and orders the whole need. "We have plenty" sets the line to 0 and the
    estimate to the need. Neither is a count."""
    action, line = _line(db, restaurant_id, line_ref)
    stock = Stock(db, menu_costs.load(db, restaurant_id))
    need, pack = Decimal(line["need"]), Decimal(line["pack_base_qty"])
    if kind == "out":
        stock.correct(line["item_id"], Decimal(0), "out")
        # With nothing on hand, the whole need is ordered, in whole packs.
        line.update(qty=int((need / pack).to_integral_value(rounding="ROUND_CEILING")) if pack > 0 else 0, est_on_hand="0.0000", est_on_hand_at_delivery="0.0000", correction="out")
    elif kind == "plenty":
        stock.correct(line["item_id"], need, "plenty")
        line.update(qty=0, est_on_hand=str(need), est_on_hand_at_delivery=str(need), correction="plenty")
    else:
        raise ValueError("A correction is out or plenty.")
    _retotal(action.payload)
    flag_modified(action, "payload")
    db.flush()
    return action


def set_line_qty(db: Session, restaurant_id: str, line_ref: str, packs: int) -> AgentAction:
    """A person's own quantity. Ordering one pack fewer than the line said adds a pack to the estimate: they know
    something the estimate did not."""
    action, line = _line(db, restaurant_id, line_ref)
    stock = Stock(db, menu_costs.load(db, restaurant_id))
    pack = Decimal(line["pack_base_qty"])
    row = stock.estimate(line["item_id"])
    stock.correct(line["item_id"], row.est_on_hand + (Decimal(int(line["qty"])) - packs) * pack, "edit")
    line.update(qty=packs, correction="edit", est_on_hand=str(stock.rows[line["item_id"]].est_on_hand))
    _retotal(action.payload)
    flag_modified(action, "payload")
    db.flush()
    return action


# --- the prep sheet ---------------------------------------------------------------------------------------


def prep_lines(db: Session, world, day: date) -> list[dict]:
    """Each prepped item (a confirmed sub-recipe a dish uses) with its par for the day: the 75th percentile of the
    day's usage. The cook preps up to par; FullHouse does not record what was on hand."""
    from app.margin_labor import units
    from app.margin_labor.models import ItemForecast

    forecasts = {f.pos_item_id: f for f in db.query(ItemForecast).filter(ItemForecast.restaurant_id == world.restaurant_id, ItemForecast.business_date == day)}
    need: dict[str, list[Decimal]] = {}
    used_by: dict[str, list[str]] = {}
    seen: set[str] = set()
    for recipe in sorted(world.recipes.values(), key=lambda r: (r.name, r.id)):
        row = world.rows[recipe.id]
        f = forecasts.get(row.pos_item_id or "")
        if not recipe.confirmed or recipe.kind != "menu_item" or f is None or row.pos_item_id in seen:
            continue
        seen.add(row.pos_item_id)
        portions = recipe.portions if recipe.portions > 0 else Decimal(1)
        for line in recipe.lines:
            sub = world.recipes.get(line.sub_recipe_id or "")
            if sub is None or not sub.confirmed:
                continue
            qty = units.convert(line.qty, line.unit, sub.yield_unit)
            if qty is None:
                continue
            per_portion = qty / portions
            held = need.setdefault(sub.id, [Decimal(0), Decimal(0), Decimal(0)])
            for i, value in enumerate((f.p10, f.p50, f.p90)):
                held[i] += Decimal(str(value)) * per_portion
            used_by.setdefault(sub.id, []).append(recipe.name)
    lines = []
    for sub_id, (low, mid, high) in need.items():
        sub = world.recipes[sub_id]
        par = ordering_math.par_today(low, mid, high)
        batches = int((par / sub.yield_qty).to_integral_value(rounding="ROUND_CEILING")) if sub.yield_qty > 0 else 0
        lines.append({"item_id": sub_id, "item_name": sub.name, "qty": float(par.quantize(Decimal("0.01"))), "unit": sub.yield_unit, "from": [{"recipe": n} for n in used_by[sub_id]],
                      "station": "Kitchen", "par": str(par), "expected": str(mid.quantize(QTY)), "batch_size": format(sub.yield_qty.normalize(), "f"), "batches_to_par": batches,
                      "shelf_life_days": None, "used_by": used_by[sub_id]})
    return sorted(lines, key=lambda l: l["item_name"])


def prep_sheet(db: Session, restaurant_id: str, day: date, now: datetime | None = None) -> tuple[AgentAction | None, str]:
    """One `prep_sheet` proposal per business date. A replay, or a sheet already proposed for the date by anyone,
    finds the one that exists."""
    from app.restaurant.po_draft import _system_run

    for action in db.query(AgentAction).filter(AgentAction.restaurant_id == restaurant_id, AgentAction.action_type == "prep_sheet"):
        if (action.payload or {}).get("date") == day.isoformat():
            return action, "exists"
    world = menu_costs.load(db, restaurant_id, now)
    if demand.forecast_items(db, restaurant_id, now)["state"] != "live":
        return None, "no_forecast"
    lines = prep_lines(db, world, day)
    if not lines:
        return None, "nothing_to_prep"
    run = _system_run(db, restaurant_id, f"Prep sheet for {day.isoformat()}")
    action = AgentAction(restaurant_id=restaurant_id, run_id=run.id, agent_definition_id=run.agent_definition_id, action_type="prep_sheet", payload={
        "module": MODULE, "date": day.isoformat(), "lines": lines, "item_name": day.isoformat(),
        "note": f"{len(lines)} prepped {'item' if len(lines) == 1 else 'items'} for {words.day(day)}, each with a par from the forecast: "
                + "; ".join(f"{l['item_name']} {words.qty(l['par'])} {l['unit']}" for l in lines[:6]) + "."})
    db.add(action)
    db.flush()
    return action, "created"


def has_costed_dish(db: Session, restaurant_id: str) -> bool:
    return db.query(DishCost.id).filter(DishCost.restaurant_id == restaurant_id, DishCost.is_current.is_(True), DishCost.lines_total > 0,
                                        DishCost.lines_priced == DishCost.lines_total).first() is not None
