"""What the Labor pages read (spec sections 10.8 D and 11): the week's forecast and people needed, and the
standards behind them. No wage appears here: hours only."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.margin_labor import demand, labor, labor_math
from app.margin_labor.models import ForecastInterval, LaborRequirement, LaborStandard, SalesOrder

STALE_ORDER_DAYS = 3


def default_week(today: date) -> date:
    """The week being planned: the Monday after today (today, on a Monday)."""
    return today + timedelta(days=(7 - today.weekday()) % 7)


def _ensure(db: Session, restaurant_id: str, now: datetime | None):
    made = labor.forecast_labor(db, restaurant_id, now)
    db.commit()
    return made


def week(db: Session, restaurant_id: str, week_of: date | None = None, now: datetime | None = None) -> dict:
    service = labor.Service(db, restaurant_id, now)
    made = _ensure(db, restaurant_id, now)
    roles = labor.ensure_roles(db, restaurant_id)
    db.commit()
    held = labor.standards(db, restaurant_id, service.today)
    state = labor.state_for(service.weeks, bool(held))
    week_of = week_of or default_week(service.today)
    week_of -= timedelta(days=week_of.weekday())
    days = [week_of + timedelta(days=n) for n in range(7)]
    run = labor.latest_run(db, restaurant_id)
    last_order = db.query(func.max(SalesOrder.business_date)).filter(SalesOrder.restaurant_id == restaurant_id).scalar()
    out = {"state": state, "setup": [{"key": "orders", "done": service.weeks >= 4, "count": service.weeks, "of": 4}, {"key": "standards", "done": bool(held), "count": len(held)}],
           "weeks_of_data": service.weeks, "primary": service.primary, "week_of": week_of, "today": service.today, "computed_at": run.finished_at if run else None,
           "stale": bool(last_order is not None and (service.today - last_order).days > STALE_ORDER_DAYS), "last_order_date": last_order,
           "target_labor_pct": None if service.conf.target_labor_pct is None else str(service.conf.target_labor_pct),
           "days": [], "roles": [], "totals": None, "intervals": {}, "horizon_end": service.today + timedelta(days=labor.HORIZON_DAYS)}
    if state == "setup":
        return out
    del made
    forecasts = {r.business_date: r for r in db.query(ForecastInterval).filter(
        ForecastInterval.restaurant_id == restaurant_id, ForecastInterval.kind == labor.KIND, ForecastInterval.grain == "day", ForecastInterval.channel == "all",
        ForecastInterval.metric == service.primary, ForecastInterval.business_date.in_(days))}
    net = {r.business_date: r for r in db.query(ForecastInterval).filter(
        ForecastInterval.restaurant_id == restaurant_id, ForecastInterval.kind == labor.KIND, ForecastInterval.grain == "day", ForecastInterval.channel == "all",
        ForecastInterval.metric == "net", ForecastInterval.business_date.in_(days))}
    actual = service.daily(service.series(service.primary))
    extra = demand.drivers_for(db, restaurant_id, days[0], days[-1])
    for day in days:
        f = forecasts.get(day)
        out["days"].append({"date": day, "low": None if f is None else f.p10, "expected": None if f is None else f.p50, "high": None if f is None else f.p90,
                            "last_week": actual.get(day - timedelta(days=7)), "drivers": [d.name or d.kind.replace("_", " ") for d in extra.get(day, [])],
                            "net_expected": None if day not in net else net[day].p50})
    rows = db.query(LaborRequirement).filter(LaborRequirement.restaurant_id == restaurant_id, LaborRequirement.kind == labor.KIND, LaborRequirement.business_date.in_(days)).all()
    names = {r.key: r.name for r in roles}
    by_role: dict[str, dict] = {}
    for row in rows:
        local = row.interval_start.astimezone(service.tz) if row.interval_start.tzinfo else row.interval_start.replace(tzinfo=timezone.utc).astimezone(service.tz)
        role = by_role.setdefault(row.role_key, {"role_key": row.role_key, "name": names.get(row.role_key, row.role_key), "hours_p50": 0.0, "hours_p90": 0.0, "peak": 0,
                                                  "peak_at": None, "by_day": {d.isoformat(): {"date": d, "hours_p50": 0.0, "hours_p90": 0.0} for d in days}})
        role["hours_p50"] += row.need_p50 * 0.5
        role["hours_p90"] += row.need_p90 * 0.5
        role["by_day"][row.business_date.isoformat()]["hours_p50"] += row.need_p50 * 0.5
        role["by_day"][row.business_date.isoformat()]["hours_p90"] += row.need_p90 * 0.5
        if row.need_p50 > role["peak"]:
            role["peak"], role["peak_at"] = row.need_p50, row.interval_start
        out["intervals"].setdefault(row.business_date.isoformat(), {}).setdefault(row.role_key, []).append(
            {"start": row.interval_start, "label": f"{local.hour % 12 or 12}:{local.minute:02d}", "need": row.need_p50, "need_if_busy": row.need_p90, "scheduled": 0,
             "units": row.units_p50})
    for per_day in out["intervals"].values():
        for steps in per_day.values():
            steps.sort(key=lambda s: s["start"])
    for role in by_role.values():
        role["by_day"] = list(role["by_day"].values())
    out["roles"] = sorted(by_role.values(), key=lambda r: -r["hours_p50"])
    out["totals"] = {"hours_p50": sum(r["hours_p50"] for r in out["roles"]), "hours_p90": sum(r["hours_p90"] for r in out["roles"]),
                     "expected": sum(d["expected"] or 0 for d in out["days"]), "low": sum(d["low"] or 0 for d in out["days"]), "high": sum(d["high"] or 0 for d in out["days"]),
                     "net_expected": sum(d["net_expected"] or 0 for d in out["days"]), "days_forecast": sum(1 for d in out["days"] if d["expected"] is not None)}
    return out


def histogram(values: list[float], bins: int = 10) -> list[dict]:
    if not values:
        return []
    low, high = min(values), max(values)
    if high == low:
        return [{"from": round(low, 2), "to": round(high, 2), "count": len(values)}]
    width = (high - low) / bins
    counts = [0] * bins
    for v in values:
        counts[min(bins - 1, int((v - low) / width))] += 1
    return [{"from": round(low + i * width, 2), "to": round(low + (i + 1) * width, 2), "count": c} for i, c in enumerate(counts)]


def standards_page(db: Session, restaurant_id: str, now: datetime | None = None) -> dict:
    service = labor.Service(db, restaurant_id, now)
    _ensure(db, restaurant_id, now)
    roles = labor.ensure_roles(db, restaurant_id)
    db.commit()
    held = labor.standards(db, restaurant_id, service.today)
    learned = {}
    for row in db.query(LaborStandard).filter(LaborStandard.restaurant_id == restaurant_id, LaborStandard.source == "learned").order_by(LaborStandard.effective_from):
        learned[row.role_key] = row
    run = labor.latest_run(db, restaurant_id)
    metrics = (run.metrics or {}) if run else {}
    out_roles = []
    for role in roles:
        current = held.get(role.key)
        metric, filt = (current.driver_metric, current.driver_filter or {}) if current is not None else labor.default_driver(role.key, service.has_covers)
        seen, weeks = labor.observations(db, service, role.key, metric, filt) if metric != "none" else ([], 0)
        rates = labor_math.rates(seen)
        was = learned.get(role.key)
        out_roles.append({
            "role_key": role.key, "name": role.name, "driver_metric": metric, "driver_filter": filt,
            "units_per_labor_hour": None if current is None or current.units_per_labor_hour is None else str(current.units_per_labor_hour),
            "min_on_floor": current.min_on_floor if current else 0, "open_lead_minutes": current.open_lead_minutes if current else 0,
            "close_lag_minutes": current.close_lag_minutes if current else 0, "source": current.source if current else None,
            "learned": None if was is None else {"units_per_labor_hour": str(was.units_per_labor_hour), "sample_size": was.sample_size, "effective_from": was.effective_from},
            "observed_intervals": len(rates), "punch_weeks": weeks, "needed_intervals": labor_math.MIN_QUALIFYING, "histogram": histogram(rates)})
    return {"state": labor.state_for(service.weeks, bool(held)), "weeks_of_data": service.weeks, "today": service.today, "computed_at": run.finished_at if run else None,
            "primary": service.primary, "roles": out_roles,
            "accuracy": {"dayparts": metrics.get("dayparts", []), "coverage": metrics.get("coverage_56"), "target": labor_math.COVERAGE_TARGET,
                         "calibration_factor": metrics.get("calibration_factor"), "stage": metrics.get("stage")}}
