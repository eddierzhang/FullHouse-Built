"""What the Channels page reads (spec sections 10.8 E and 11): the newest window's rows by item, channel and
platform, with the per-unit breakdown behind each."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.agents.models import AgentAction
from app.margin_labor import channel_math, channels, menu_costs, words
from app.margin_labor.channels import CHANNEL_NAMES, FINDING, MATCHED_ENOUGH, PLATFORM
from app.margin_labor.models import ChannelItemMargin, ChannelTerms, Finding, SalesOrder

STALE_ORDER_DAYS = 3


def _s(value) -> str | None:
    return None if value is None else str(value)


def overview(db: Session, restaurant_id: str, now: datetime | None = None) -> dict:
    world = menu_costs.load(db, restaurant_id, now)
    start, end = channels.window(world.today)
    newest = db.query(func.max(ChannelItemMargin.window_end)).filter(ChannelItemMargin.restaurant_id == restaurant_id).scalar()
    summary = None
    if newest != end:
        summary = channels.run(db, restaurant_id, now)
        db.commit()
    rows = db.query(ChannelItemMargin).filter(ChannelItemMargin.restaurant_id == restaurant_id, ChannelItemMargin.window_end == end).all()
    orders = db.query(SalesOrder).filter(SalesOrder.restaurant_id == restaurant_id, SalesOrder.business_date >= start, SalesOrder.business_date <= end,
                                         SalesOrder.status.in_(channels.COUNTED)).all()
    platform_orders = [o for o in orders if o.channel == PLATFORM and o.platform]
    terms = db.query(ChannelTerms).filter(ChannelTerms.restaurant_id == restaurant_id).all()
    from app.margin_labor.models import ChannelPayout

    matched_ids = {p.order_id for p in db.query(ChannelPayout.order_id).filter(ChannelPayout.restaurant_id == restaurant_id, ChannelPayout.order_id.isnot(None))}
    matched = {o.id: True for o in platform_orders if o.id in matched_ids}
    state, steps = channels.state_for(db, world, orders, platform_orders, matched, terms, end)
    match_rate = (Decimal(len(matched)) / len(platform_orders)).quantize(channel_math.SHARE) if platform_orders else None
    last_order = db.query(func.max(SalesOrder.business_date)).filter(SalesOrder.restaurant_id == restaurant_id).scalar()
    del summary
    out = {"state": state, "setup": steps, "window_start": start, "window_end": end, "today": world.today,
           "computed_at": max((r.updated_at for r in rows), default=None),
           "stale": bool(last_order is not None and (world.today - last_order).days > STALE_ORDER_DAYS), "last_order_date": last_order,
           "match_rate": _s(match_rate), "contract_rates": bool(match_rate is not None and match_rate < MATCHED_ENOUGH),
           "platforms": sorted({o.platform for o in platform_orders}), "terms": terms_out(terms, world.today),  # the rates in effect now: what the editor shows
           "header": None, "tiles": [], "chart": None, "items": []}
    if state == "setup":
        return out
    findings = {f.subject_id: f for f in db.query(Finding).filter(Finding.restaurant_id == restaurant_id, Finding.type == FINDING, Finding.status == "open")}
    proposals = {a.id: a for a in db.query(AgentAction).filter(AgentAction.id.in_([f.proposal_id for f in findings.values() if f.proposal_id] or [""]))}

    items = []
    for row in rows:
        if row.channel != PLATFORM:
            continue
        finding = findings.get(f"{row.pos_item_id}|{row.platform}")
        proposal = proposals.get(finding.proposal_id) if finding is not None and finding.proposal_id else None
        payload = (proposal.payload or {}) if proposal else {}
        items.append({
            "row_id": row.id, "pos_item_id": row.pos_item_id, "name": row.pos_item_name, "platform": row.platform, "price": str(row.unit_revenue),
            "fees": str(row.commission_unit + row.processing_unit + row.fixed_unit), "commission": str(row.commission_unit), "processing": str(row.processing_unit),
            "fixed": str(row.fixed_unit), "plate_cost": _s(row.plate_cost), "kept": _s(row.unit_contribution), "kept_pct": _s(row.contribution_pct),
            "units": str(row.units), "match_rate": _s(row.payout_match_rate), "recipe_id": row.basis.get("recipe_id"),
            "finding": None if finding is None else {
                "id": finding.id, "weekly_dollars": str(finding.weekly_dollars), "target": finding.basis.get("target_contribution"),
                "proposal_id": finding.proposal_id, "proposal_status": proposal.status if proposal else None, "suggested_price": payload.get("suggested_price"),
                "kept_at_suggested": payload.get("contribution_at_suggested")},
        })
    items.sort(key=lambda r: (-(Decimal(r["finding"]["weekly_dollars"]) if r["finding"] else Decimal(-1)), Decimal(r["kept"]) if r["kept"] is not None else Decimal(10**6), r["name"]))
    flagged = [i for i in items if i["finding"]]
    flagged_platforms = sorted({i["platform"] for i in flagged})
    out["items"] = items
    out["header"] = {"items": len(flagged), "weekly_dollars": str(sum((Decimal(i["finding"]["weekly_dollars"]) for i in flagged), Decimal(0))),
                     "platform": flagged_platforms[0] if len(flagged_platforms) == 1 else None, "delivery_items": len(items),
                     "losing": any(Decimal(i["kept"]) < 0 for i in flagged)}

    # One tile per channel (and platform), the three that sell most: what an item keeps, as a share of its price.
    by_channel: dict[tuple[str, str], dict] = {}
    for row in rows:
        if row.unit_contribution is None:
            continue
        held = by_channel.setdefault((row.channel, row.platform), {"units": Decimal(0), "revenue": Decimal(0), "kept": Decimal(0)})
        held["units"] += row.units
        held["revenue"] += row.unit_revenue * row.units
        held["kept"] += row.unit_contribution * row.units
    tiles = [{"channel": channel, "platform": platform or None, "label": platform or CHANNEL_NAMES.get(channel, channel), "units": str(v["units"]),
              "kept_pct": _s((v["kept"] / v["revenue"]).quantize(channel_math.SHARE)) if v["revenue"] > 0 else None,
              "kept": _s((v["kept"] / v["units"]).quantize(channel_math.FOUR)) if v["units"] > 0 else None,
              "price": _s((v["revenue"] / v["units"]).quantize(channel_math.FOUR)) if v["units"] > 0 else None}
             for (channel, platform), v in by_channel.items()]
    order = list(CHANNEL_NAMES)
    tiles.sort(key=lambda t: (-Decimal(t["units"]), order.index(t["channel"]) if t["channel"] in order else 99, t["label"]))
    out["tiles"] = tiles[:3]

    # The ten items that sell most on delivery, with what each keeps on every channel it sells on (four series at most).
    delivery_units: dict[str, Decimal] = {}
    for row in rows:
        if row.channel == PLATFORM:
            delivery_units[row.pos_item_id] = delivery_units.get(row.pos_item_id, Decimal(0)) + row.units
    top = [pos for pos, _u in sorted(delivery_units.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]
    series = [t["label"] for t in tiles][:4]
    chart = []
    for pos in top:
        values = {}
        name = pos
        for row in rows:
            if row.pos_item_id == pos:
                name = row.pos_item_name
                label = row.platform or CHANNEL_NAMES.get(row.channel, row.channel)
                if label in series and row.unit_contribution is not None:
                    values[label] = str(row.unit_contribution)
        chart.append({"pos_item_id": pos, "name": name, "values": values})
    out["chart"] = {"series": series, "items": chart}
    return out


def terms_out(terms: list, day) -> list[dict]:
    current = {}
    for t in sorted(terms, key=lambda t: t.effective_from):
        if t.effective_from <= day and (t.effective_to is None or t.effective_to >= day):
            current[t.platform] = t
    return [{"platform": t.platform, "commission_pct": str(t.commission_pct), "processing_pct": str(t.processing_pct), "fixed_fee_per_order": str(t.fixed_fee_per_order),
             "packaging_cost_per_order": str(t.packaging_cost_per_order), "effective_from": t.effective_from} for t in current.values()]


def why_row(db: Session, row: ChannelItemMargin) -> dict:
    """The stored breakdown of one row, for the panel and its waterfall."""
    b = row.basis or {}
    where = row.platform or CHANNEL_NAMES.get(row.channel, row.channel)
    fee_source = ("Payout reports" if b.get("matched_orders") and not b.get("terms_orders") else "Contract rates" if b.get("terms_orders") and not b.get("matched_orders")
                  else "Payout reports and contract rates" if b.get("matched_orders") else "No fees on this channel")
    inputs = [
        {"label": "Price", "value": words.dollars(row.unit_revenue), "source": {"kind": "text", "label": f"{b.get('orders', 0)} orders, net of discounts, before tax"}},
        {"label": "Commission", "value": words.dollars(row.commission_unit), "source": {"kind": "text", "label": fee_source}},
        {"label": "Processing", "value": words.dollars(row.processing_unit), "source": {"kind": "text", "label": fee_source}},
        {"label": "Fixed fees and packaging", "value": words.dollars(row.fixed_unit), "source": {"kind": "text", "label": "Contract rates"}},
        {"label": "Cost per plate", "value": words.dollars(row.plate_cost) if row.plate_cost is not None else "No costed recipe",
         "source": {"kind": "recipe", "id": b.get("recipe_id"), "label": "Menu costs"} if b.get("recipe_id") else None},
    ]
    kept = row.unit_contribution if row.unit_contribution is not None else Decimal(0)
    return {"result": {"value": str(kept), "kind": "dollars", "label": f"kept per item on {where}"},
            "formula": "Kept per item = price - commission - processing - fixed fees and packaging - cost per plate",
            "inputs": inputs,
            "data_basis": f"{b.get('orders', 0)} orders in 28 days to {words.day(row.window_end)}; {b.get('matched_orders', 0)} matched a payout report",
            "computed_at": row.updated_at, "inputs_changed_on": row.window_end.isoformat(),
            "fix": {"label": "Edit recipe", "route": f"menu-costs/{b['recipe_id']}"} if b.get("recipe_id") else {"label": "Open channels", "route": "channels"},
            "waterfall": None if row.plate_cost is None else {
                "start": {"label": "Price", "amount": str(row.unit_revenue)},
                "steps": [{"label": "Commission", "amount": str(row.commission_unit)}, {"label": "Processing", "amount": str(row.processing_unit)},
                          {"label": "Fixed fees, packaging", "amount": str(row.fixed_unit)}, {"label": "Food cost", "amount": str(row.plate_cost)}],
                "end": {"label": "Kept per item", "amount": str(kept)}}}
