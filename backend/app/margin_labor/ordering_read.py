"""What the Ordering and prep pages read (spec sections 10.8 C and 11)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.agents.models import AgentAction
from app.margin_labor import demand, menu_costs, ordering, words
from app.margin_labor.business import zone
from app.margin_labor.models import ForecastInterval, SupplierSchedule
from app.restaurant.models import Supplier

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def schedules_out(db: Session, restaurant_id: str) -> list[dict]:
    suppliers = {s.id: s.name for s in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id)}
    rows = db.query(SupplierSchedule).filter(SupplierSchedule.restaurant_id == restaurant_id).all()
    return sorted(({"id": r.id, "supplier_id": r.supplier_id, "supplier": suppliers.get(r.supplier_id, "Supplier"), "order_cutoff_weekday": r.order_cutoff_weekday,
                    "order_cutoff_time": r.order_cutoff_time, "delivery_weekday": r.delivery_weekday,
                    "minimum_order_amount": None if r.minimum_order_amount is None else str(r.minimum_order_amount)} for r in rows),
                  key=lambda r: (r["supplier"], r["delivery_weekday"]))


def overview(db: Session, restaurant_id: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    world = menu_costs.load(db, restaurant_id, now)
    today = world.today
    schedules = db.query(SupplierSchedule).filter(SupplierSchedule.restaurant_id == restaurant_id).all()
    suppliers = {s.id: s.name for s in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id)}
    sales = demand.Sales(db, restaurant_id, today)
    steps = [{"key": "costs", "done": ordering.has_costed_dish(db, restaurant_id), "count": 0},
             {"key": "schedule", "done": bool(schedules), "count": len({s.supplier_id for s in schedules})},
             {"key": "sales", "done": sales.weeks >= demand.MIN_WEEKS, "count": sales.weeks, "of": demand.MIN_WEEKS}]
    state = "live" if all(s["done"] for s in steps) else "setup"
    out = {"state": state, "setup": steps, "today": today, "computed_at": None, "stale": False, "last_sales_date": sales.open_days[-1] if sales.open_days else None,
           "weeks_of_data": sales.weeks, "schedules": schedules_out(db, restaurant_id),
           "suppliers": [{"id": i, "name": n} for i, n in sorted(suppliers.items(), key=lambda kv: kv[1])],
           "header": None, "tiles": None, "chart": None, "accuracy": [], "drafts": []}
    if state == "setup":
        return out
    made = demand.forecast_items(db, restaurant_id, now)
    db.commit()
    run = demand.latest_run(db, restaurant_id)
    metrics = (run.metrics or {}) if run else {}
    out["computed_at"] = run.finished_at if run else None
    out["stale"] = bool(sales.open_days and (today - sales.open_days[-1]).days > 3)
    del made

    local = now.astimezone(zone(world.conf.timezone))
    ahead = ordering.upcoming(schedules, local)
    next_cutoff = None
    if ahead:
        cutoff, delivery, row = ahead[0]
        next_cutoff = {"supplier_id": row.supplier_id, "supplier": suppliers.get(row.supplier_id, "Supplier"), "at": cutoff.astimezone(timezone.utc), "delivery_date": delivery,
                       "hours_away": round((cutoff - local).total_seconds() / 3600, 1)}
    drafts = []
    for action in db.query(AgentAction).filter(AgentAction.restaurant_id == restaurant_id, AgentAction.action_type == ordering.ACTION, AgentAction.status == "pending").order_by(AgentAction.created_at):
        p = action.payload or {}
        if p.get("module") != ordering.MODULE:
            continue
        drafts.append({"proposal_id": action.id, "supplier_id": p["supplier_id"], "supplier": p["supplier_name"], "delivery_date": p["delivery_date"], "cutoff_at": p["cutoff_at"],
                       "period_start": p["period_start"], "period_end": p["period_end"], "check_quantities": bool(p.get("check_quantities")),
                       "minimum_order_amount": p.get("minimum_order_amount"), "under_minimum": bool(p.get("under_minimum")), "total": p.get("total"),
                       "lines": [{"line_ref": f"{action.id}.{l['line_id']}", "item_id": l["item_id"], "name": l["description"], "suggested": l["suggested_qty"], "qty": l["qty"],
                                  "pack_unit": l["unit"], "pack_base_qty": l["pack_base_qty"], "base_unit": l["base_unit"], "expected_use": l["expected_use"],
                                  "est_on_hand": l["est_on_hand"], "est_on_hand_at_delivery": l["est_on_hand_at_delivery"], "need": l["need"],
                                  "pack_price": l.get("pack_price"), "line_total": l.get("line_total"), "correction": l.get("correction")} for l in p["lines"]]})
    # A supplier can be drafted now when its next delivery (the first whose cutoff has not passed) has no draft yet.
    nearest: dict[str, tuple] = {}
    for cutoff, delivery, row in ahead:
        nearest.setdefault(row.supplier_id, (cutoff, delivery))
    out["can_draft"] = [{"supplier_id": supplier_id, "supplier": suppliers.get(supplier_id, "Supplier"), "delivery_date": delivery, "cutoff_at": cutoff.astimezone(timezone.utc)}
                        for supplier_id, (cutoff, delivery) in sorted(nearest.items(), key=lambda kv: kv[1][0])
                        if ordering.existing_draft(db, restaurant_id, supplier_id, delivery) is None]
    out["drafts"] = drafts
    out["header"] = {"orders": len(drafts), "next_cutoff": next_cutoff}
    out["tiles"] = {"waiting": {"value": len(drafts), "lines": sum(1 for d in drafts for l in d["lines"] if l["qty"])},
                    "next_cutoff": next_cutoff, "average_miss": {"value": metrics.get("wape_28"), "then": metrics.get("wape_prior_28"), "stage": metrics.get("stage")}}
    # Daily demand: 14 days back (what happened, against what the same method expected) and 14 ahead.
    points = [{"date": r["date"], "low": r["p10"], "expected": r["p50"], "high": r["p90"], "actual": r["actual"], "drivers": []} for r in metrics.get("recent_totals", [])]
    for row in db.query(ForecastInterval).filter(ForecastInterval.restaurant_id == restaurant_id, ForecastInterval.grain == "day", ForecastInterval.metric == "items",
                                                 ForecastInterval.channel == "all", ForecastInterval.kind == demand.KIND, ForecastInterval.business_date >= today,
                                                 ForecastInterval.business_date <= today + timedelta(days=demand.HORIZON_DAYS - 1)).order_by(ForecastInterval.business_date):
        points.append({"date": row.business_date.isoformat(), "low": row.p10, "expected": row.p50, "high": row.p90, "actual": None,
                       "drivers": [d["name"] or d["kind"].replace("_", " ") for d in row.drivers_applied or []]})
    out["chart"] = {"points": points, "today_index": len(metrics.get("recent_totals", [])), "coverage": metrics.get("coverage_56")}
    out["accuracy"] = metrics.get("weekly", [])
    return out


def why_line(db: Session, restaurant_id: str, line_ref: str) -> dict | None:
    """The stored basis of one order line, with the estimated stock through its delivery period."""
    action_id, _, item_id = line_ref.partition(".")
    action = db.get(AgentAction, action_id)
    if action is None or action.restaurant_id != restaurant_id or (action.payload or {}).get("module") != ordering.MODULE:
        return None
    line = next((l for l in action.payload["lines"] if l["line_id"] == item_id), None)
    if line is None:
        return None
    b, unit = line["basis"], line["base_unit"]
    amount = lambda q: f"{words.qty(q)} {unit}"  # noqa: E731
    buffer = Decimal(b["period_demand"]) - Decimal(b["sum_p50"])
    service = "80%" if Decimal(b["z"]) < 1 else "95%"
    start, end = date.fromisoformat(b["period_start"]), date.fromisoformat(b["period_end"])
    inputs = [
        {"label": "Expected use", "value": amount(b["sum_p50"]), "source": {"kind": "text", "label": f"{b['days_used']} days from {words.day(start)}, forecast and recipes"}},
        {"label": "Buffer for a busy period", "value": amount(buffer), "source": {"kind": "text", "label": f"{service} service level"}},
        {"label": "Loss allowance", "value": words.percent(b["loss_factor"]), "source": {"kind": "route", "id": "usage", "label": "Usage"}},
        {"label": "Need", "value": amount(b["need"]), "source": None},
        {"label": "Estimated on hand at delivery", "value": amount(b["est_on_hand_at_delivery"]),
         "source": {"kind": "text", "label": f"{amount(b['est_on_hand'])} now, less {amount(b['use_before_delivery'])} used before delivery"}},
        {"label": "Pack", "value": amount(b["pack_size"]), "source": {"kind": "invoice", "label": "Invoice"}},
    ]
    # Estimated stock, day by day: what is on hand at delivery plus this order, less each day's expected use.
    level = Decimal(b["est_on_hand_at_delivery"]) + Decimal(b["pack_size"]) * Decimal(str(line["qty"]))
    stock = []
    for n, day in enumerate(b["days"]):
        stock.append({"date": day["date"], "value": str(max(Decimal(0), level).quantize(Decimal("0.01"))), "marks": ["Delivery"] if n == 0 else []})
        level -= Decimal(day["p50"])
    stock.append({"date": end.isoformat(), "value": str(max(Decimal(0), level).quantize(Decimal("0.01"))), "marks": ["Next delivery"]})
    anchor = (b.get("est_anchor") or {}).get("anchor_kind")
    return {"result": {"value": str(line["suggested_qty"]), "kind": "number", "label": f"{line['unit']} of {line['description']} suggested"},
            "formula": "Order = expected use over the delivery period + a buffer + the loss allowance - estimated stock at delivery, rounded up to whole packs",
            "inputs": inputs,
            "data_basis": f"Delivery {words.day(start)}, covering to {words.day(end - timedelta(days=1))}. The stock estimate is {'new' if anchor == 'start' else 'from your last correction'}, not a count",
            "computed_at": action.created_at, "inputs_changed_on": b["est_as_of"], "fix": {"label": "Open usage", "route": "usage"},
            "series": {"label": "Estimated stock", "unit": unit, "points": stock}}
