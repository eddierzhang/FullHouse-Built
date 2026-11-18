"""Module E, channel profitability (spec section 9): what each menu item keeps per unit on each channel after
commission, fees, packaging and food cost, and a price proposal for an item that loses money on a platform.

`run` is the job: a pure function of the window's orders, the payout reports, the contract rates and Module A's
costs. A platform order matched to a payout row on the platform's order id uses its actual commission and fees;
an unmatched order uses the contract rates in effect that day. Nothing here changes a price anywhere: a person
changes it on the platform.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.margin_labor import channel_math, menu_costs, words
from app.margin_labor import settings as ml_settings
from app.margin_labor.channel_math import CALC_VERSION, NOTE, OrderFees
from app.margin_labor.models import ChannelItemMargin, ChannelPayout, ChannelTerms, Finding, SalesOrder, SalesOrderLine
from app.platform import events

logger = logging.getLogger(__name__)

FINDING = "channel_margin"
ACTION = "channel_reprice"
WEEKLY_CAP = 3
WINDOW_DAYS = 28
MIN_UNITS = Decimal(10)
PLATFORM = "delivery_platform"
COUNTED = ("closed", "open")
MATCHED_ENOUGH = Decimal("0.80")
CHANNEL_NAMES = {"dine_in": "Dine in", "takeout": "Takeout", "delivery_own": "Own delivery", "delivery_platform": "Delivery platform", "catering": "Catering"}


def window(today: date) -> tuple[date, date]:
    """The last 28 complete business dates."""
    return today - timedelta(days=WINDOW_DAYS), today - timedelta(days=1)


def terms_on(terms: list[ChannelTerms], platform: str, day: date) -> ChannelTerms | None:
    held = [t for t in terms if t.platform == platform and t.effective_from <= day and (t.effective_to is None or t.effective_to >= day)]
    return max(held, key=lambda t: t.effective_from) if held else None


def run(db: Session, restaurant_id: str, now: datetime | None = None) -> dict:
    world = menu_costs.load(db, restaurant_id, now)
    start, end = window(world.today)
    orders = db.query(SalesOrder).filter(SalesOrder.restaurant_id == restaurant_id, SalesOrder.business_date >= start, SalesOrder.business_date <= end,
                                         SalesOrder.status.in_(COUNTED)).all()
    by_id = {o.id: o for o in orders}
    lines = db.query(SalesOrderLine).filter(SalesOrderLine.restaurant_id == restaurant_id, SalesOrderLine.business_date >= start,
                                            SalesOrderLine.business_date <= end, SalesOrderLine.voided.is_(False)).all()
    terms = db.query(ChannelTerms).filter(ChannelTerms.restaurant_id == restaurant_id).all()
    # Match payouts to orders on the platform's order id. The link is kept, so the next run finds it already made.
    payouts: dict[tuple[str, str], ChannelPayout] = {(p.platform, p.external_order_id): p for p in db.query(ChannelPayout).filter(ChannelPayout.restaurant_id == restaurant_id)}
    matched: dict[str, ChannelPayout] = {}
    for order in orders:
        if order.channel != PLATFORM or not order.platform:
            continue
        payout = payouts.get((order.platform, order.external_id))
        if payout is not None:
            matched[order.id] = payout
            if payout.order_id != order.id:
                payout.order_id = order.id

    dishes: dict[str, str] = {}
    for recipe in sorted(world.recipes.values(), key=lambda r: (r.name, r.id)):
        row = world.rows[recipe.id]
        if recipe.confirmed and recipe.kind == "menu_item" and row.pos_item_id and row.pos_item_id not in dishes:
            dishes[row.pos_item_id] = recipe.id
    cost_cache: dict[tuple[str, date], Decimal | None] = {}

    def plate_cost(pos_item_id: str, day: date) -> Decimal | None:
        recipe_id = dishes.get(pos_item_id)
        if recipe_id is None:
            return None
        if (recipe_id, day) not in cost_cache:
            then = world.cost(recipe_id, day)
            if not then.complete:  # a price that arrived after the order: the cost now is the best that is known
                then = world.cost(recipe_id)
            cost_cache[(recipe_id, day)] = then.plate_cost if then.complete else None
        return cost_cache[(recipe_id, day)]

    def fees_for(order: SalesOrder) -> tuple[OrderFees, str]:
        if order.channel != PLATFORM or not order.platform:
            return OrderFees(), "none"
        contract = terms_on(terms, order.platform, order.business_date)
        packaging = contract.packaging_cost_per_order if contract is not None else Decimal(0)
        payout = matched.get(order.id)
        if payout is not None:
            # The platform's own fixed fees are inside what it reported; packaging is the restaurant's cost and never is.
            return OrderFees(payout.commission, payout.fees, packaging), "payout"
        if contract is None:
            return OrderFees(), "missing"
        return OrderFees(order.net * contract.commission_pct, order.net * contract.processing_pct, contract.fixed_fee_per_order + packaging), "terms"

    groups: dict[tuple[str, str, str], dict] = {}
    order_seen: dict[tuple[str, str, str], dict[str, str]] = {}
    for line in lines:
        order = by_id.get(line.order_id)
        if order is None:
            continue
        fees, source = fees_for(order)
        figures = channel_math.line_figures(line.net, line.qty, order.net, fees, plate_cost(line.pos_item_id, order.business_date))
        if figures is None:
            continue
        key = (line.pos_item_id, order.channel, (order.platform or "") if order.channel == PLATFORM else "")
        group = groups.setdefault(key, {"name": line.pos_item_name, "figures": [], "units": Decimal(0)})
        group["figures"].append((figures, line.qty))
        group["units"] += line.qty
        order_seen.setdefault(key, {})[order.id] = source

    platform_orders = [o for o in orders if o.channel == PLATFORM and o.platform]
    match_rate = (Decimal(len([o for o in platform_orders if o.id in matched])) / len(platform_orders)).quantize(channel_math.SHARE) if platform_orders else None
    held = {(r.pos_item_id, r.channel, r.platform): r for r in db.query(ChannelItemMargin).filter(ChannelItemMargin.restaurant_id == restaurant_id, ChannelItemMargin.window_end == end)}
    rows: dict[tuple[str, str, str], ChannelItemMargin] = {}
    for key, group in sorted(groups.items()):
        mean = channel_math.average(group["figures"])
        if mean is None:
            continue
        sources = list(order_seen[key].values())
        row_matched = Decimal(sources.count("payout")) / len(sources) if key[1] == PLATFORM and sources else None
        contract = terms_on(terms, key[2], end) if key[2] else None
        basis = {"orders": len(sources), "matched_orders": sources.count("payout"), "terms_orders": sources.count("terms"), "missing_fee_orders": sources.count("missing"),
                 "recipe_id": dishes.get(key[0]),
                 "rates": None if contract is None else {"commission_pct": str(contract.commission_pct), "processing_pct": str(contract.processing_pct),
                                                         "fixed_fee_per_order": str(contract.fixed_fee_per_order), "packaging_cost_per_order": str(contract.packaging_cost_per_order)}}
        values = {"pos_item_name": group["name"][:200], "window_start": start, "units": group["units"], "unit_revenue": mean.unit_revenue, "commission_unit": mean.commission_unit,
                  "processing_unit": mean.processing_unit, "fixed_unit": mean.fixed_unit, "plate_cost": mean.plate_cost, "unit_contribution": mean.unit_contribution,
                  "contribution_pct": channel_math.contribution_pct(mean),
                  "payout_match_rate": None if row_matched is None else row_matched.quantize(channel_math.SHARE), "basis": basis, "calc_version": CALC_VERSION}
        row = held.get(key)
        if row is None:
            row = ChannelItemMargin(restaurant_id=restaurant_id, pos_item_id=key[0], channel=key[1], platform=key[2], window_end=end, **values)
            db.add(row)
        else:
            for name, value in values.items():
                if getattr(row, name) != value:
                    setattr(row, name, value)
        rows[key] = row
    for key, row in held.items():
        if key not in rows:
            db.delete(row)
    db.flush()

    state, steps = state_for(db, world, orders, platform_orders, matched, terms, end)
    opened = _findings(db, world, rows, terms, end, live=state == "live")
    db.flush()
    events.emit(db, restaurant_id, "channel_margins.computed", "channel_item_margins", end.isoformat(),
                {"rows": len(rows), "state": state, "open_findings": opened, "match_rate": None if match_rate is None else str(match_rate)})
    return {"window_end": end.isoformat(), "rows": len(rows), "state": state, "open_findings": opened, "match_rate": None if match_rate is None else str(match_rate)}


def state_for(db: Session, world, orders: list, platform_orders: list, matched: dict, terms: list, end: date) -> tuple[str, list[dict]]:
    """Minimum data: Module A is live, orders carry a channel, and each platform in use has a payout report or contract rates."""
    from app.margin_labor.models import DishCost

    costed = db.query(DishCost.id).filter(DishCost.restaurant_id == world.restaurant_id, DishCost.is_current.is_(True), DishCost.lines_total > 0,
                                          DishCost.lines_priced == DishCost.lines_total).first() is not None
    platforms = sorted({o.platform for o in platform_orders})
    covered = [p for p in platforms if terms_on(terms, p, end) is not None or any(o.platform == p and o.id in matched for o in platform_orders)]
    steps = [{"key": "menu_costs", "done": costed, "count": 1 if costed else 0},
             {"key": "orders", "done": bool(orders), "count": len(orders)},
             {"key": "rates", "done": len(covered) == len(platforms), "count": len(covered), "of": len(platforms), "missing": [p for p in platforms if p not in covered]}]
    return ("live" if all(s["done"] for s in steps) else "setup"), steps


def target_contribution(db: Session, world, pos_item_id: str, plate: Decimal, recipe_id: str | None) -> tuple[Decimal | None, Decimal | None]:
    """(target, dine-in menu price). The target is the dine-in menu price less the cost of the plate, unless the
    restaurant has set its own."""
    own = world.conf.overrides.get("channel_target_contribution")
    price = None
    if recipe_id is not None and recipe_id in world.rows:
        price, _ = menu_costs.menu_price(db, world, world.rows[recipe_id])
    if own is not None:
        return Decimal(str(own)), price
    return (None if price is None else price - plate), price


def _findings(db: Session, world, rows: dict, terms: list, end: date, live: bool) -> int:
    """One `channel_margin` finding per item and platform: at least 10 sold there in the window, a fully priced
    recipe, and a contribution below zero or below half of what the item keeps dine-in."""
    rid = world.restaurant_id
    existing = {f.subject_id: f for f in db.query(Finding).filter(Finding.restaurant_id == rid, Finding.type == FINDING, Finding.status.in_(("open", "dismissed")))}
    wanted: dict[str, tuple] = {}
    for (pos_item_id, channel, platform), row in rows.items():
        if not live or channel != PLATFORM or not platform or row.units < MIN_UNITS or row.unit_contribution is None or row.plate_cost is None:
            continue
        target, dine_price = target_contribution(db, world, pos_item_id, row.plate_cost, row.basis.get("recipe_id"))
        dine = rows.get((pos_item_id, "dine_in", ""))
        dine_kept = dine.unit_contribution if dine is not None and dine.unit_contribution is not None else target
        if target is None or not channel_math.below_target(row.unit_contribution, dine_kept):
            continue
        impact = channel_math.weekly_impact(target, row.unit_contribution, row.units)
        wanted[f"{pos_item_id}|{platform}"] = (row, target, dine_price, dine_kept, impact)
    now = datetime.now(timezone.utc)
    for subject, finding in existing.items():
        if subject not in wanted:
            finding.status, finding.closed_at, finding.note = "closed", now, finding.note or "No longer true in the latest 28 days."
    opened = 0
    for subject, (row, target, dine_price, dine_kept, impact) in wanted.items():
        title = f"{row.pos_item_name} keeps {words.dollars(row.unit_contribution)} an item on {row.platform}"
        basis = {"row_id": row.id, "pos_item_id": row.pos_item_id, "platform": row.platform, "target_contribution": str(target), "dine_in_price": None if dine_price is None else str(dine_price),
                 "dine_in_contribution": None if dine_kept is None else str(dine_kept), "unit_contribution": str(row.unit_contribution), "units": str(row.units),
                 "weekly_impact": str(impact), "formula": "(target kept per item - kept per item) x units sold in 28 days / 4", "window_end": end.isoformat()}
        finding = existing.get(subject)
        if finding is None:
            finding = Finding(restaurant_id=rid, type=FINDING, subject_table="pos_items", subject_id=subject[:80], title=title[:200], weekly_dollars=max(impact, Decimal(0)), basis=basis)
            db.add(finding)
            db.flush()
            action = propose_reprice(db, world, row, finding, terms, end)
            if action is not None:
                finding.proposal_id = action.id
            opened += 1
        else:
            finding.title, finding.weekly_dollars, finding.basis = title[:200], max(impact, Decimal(0)), basis
            opened += 1 if finding.status == "open" else 0
    return opened


def rates_for(row: ChannelItemMargin, terms: list, end: date) -> tuple[Decimal, Decimal, str]:
    """The platform's commission and processing rates: the contract's, else what the payouts actually took."""
    contract = terms_on(terms, row.platform, end)
    if contract is not None:
        return contract.commission_pct, contract.processing_pct, "contract"
    if row.unit_revenue > 0:
        return ((row.commission_unit / row.unit_revenue).quantize(channel_math.SHARE), (row.processing_unit / row.unit_revenue).quantize(channel_math.SHARE), "payouts")
    return Decimal(0), Decimal(0), "payouts"


def propose_reprice(db: Session, world, row: ChannelItemMargin, finding: Finding, terms: list, end: date):
    """One `channel_reprice` proposal: the platform price at which the item keeps its target. Held back, and logged,
    past three a week."""
    from app.agents import caps
    from app.agents.crud import get_definition_by_key
    from app.agents.definitions import AGENT_KEY
    from app.agents.models import AgentAction
    from app.recipes.engine import _system_run

    rid = world.restaurant_id
    target = Decimal(finding.basis["target_contribution"])
    commission_pct, processing_pct, source = rates_for(row, terms, end)
    out = channel_math.channel_price(row.plate_cost, row.fixed_unit, target, commission_pct, processing_pct, world.conf.price_ending)
    if out is None or out.suggested_price <= row.unit_revenue:
        return None
    for action in db.query(AgentAction).filter(AgentAction.restaurant_id == rid, AgentAction.action_type == ACTION, AgentAction.status == "pending"):
        if (action.payload or {}).get("finding_subject") == finding.subject_id:
            return action
    if caps.made_this_week(db, rid, ACTION, world.today) >= WEEKLY_CAP:
        logger.info("cap reached: %s for %s this week; holding", ACTION, rid)
        events.emit(db, rid, "proposal.held", "agent_actions", None, {"kind": ACTION, "cap": WEEKLY_CAP, "subject": finding.subject_id})
        return None
    definition = get_definition_by_key(db, AGENT_KEY)
    if definition is None:
        return None
    run_ = _system_run(db, rid, definition, f"Platform price suggested for {row.pos_item_name} on {row.platform}")
    note = (f"{row.pos_item_name} keeps {words.dollars(row.unit_contribution)} an item on {row.platform} at {words.dollars(row.unit_revenue)}. "
            f"{words.dollars(out.suggested_price)} keeps {words.dollars(out.contribution)}, against a target of {words.dollars(target)}. {NOTE} "
            "A person changes the price on the platform; FullHouse never does.")
    action = AgentAction(restaurant_id=rid, run_id=run_.id, agent_definition_id=run_.agent_definition_id, action_type=ACTION, payload={
        "module": "channels", "finding_id": finding.id, "finding_subject": finding.subject_id, "margin_row_id": row.id, "item_name": row.pos_item_name,
        "pos_item_id": row.pos_item_id, "platform": row.platform, "current_price": str(row.unit_revenue), "plate_cost": str(row.plate_cost),
        "fixed_unit": str(row.fixed_unit), "target_contribution": str(target), "commission_pct": str(commission_pct), "processing_pct": str(processing_pct),
        "rates_from": source, "price": str(out.price), "suggested_price": str(out.suggested_price), "contribution_at_suggested": str(out.contribution),
        "unit_contribution": str(row.unit_contribution), "weekly_dollars": str(finding.weekly_dollars), "check": NOTE, "note": note,
    })
    db.add(action)
    db.flush()
    return action
