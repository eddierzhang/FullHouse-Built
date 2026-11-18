"""Item demand (spec sections 7 and 8.2): daily quantity per POS item with a range, from the forecast core.

`forecast_items` is the `forecast_daily` job for items. It reads daily item sales, picks the stage that has earned
its place on an 8-week backtest, forecasts the next 14 days, and records how well the same method would have done
over the last 12 weeks. Items that average under 5 units a day are forecast as their share of their category.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.margin_labor import forecasting, menu_costs
from app.margin_labor.business import day_bounds
from app.margin_labor.forecasting import Driver, Drivers, History, Quantiles
from app.margin_labor.models import DemandDriver, ForecastInterval, ForecastRun, ItemForecast, PosItem
from app.platform import events
from app.restaurant.models import Restaurant
from app.sales.models import SalesItem

HORIZON_DAYS = 14
HISTORY_DAYS = 400
SMALL_ITEM_UNITS = 5.0
MIN_WEEKS = 4
KIND = "weekly"


class Sales:
    """Daily item sales, zero-filled on the days the restaurant sold anything."""

    def __init__(self, db: Session, restaurant_id: str, today: date):
        self.today = today
        self.items: dict[str, History] = {}
        self.names: dict[str, str] = {}
        raw: dict[str, dict[date, float]] = {}
        for pos, name, day, qty in db.query(SalesItem.pos_item_id, SalesItem.pos_item_name, SalesItem.date, SalesItem.qty).filter(
                SalesItem.restaurant_id == restaurant_id, SalesItem.date >= today - timedelta(days=HISTORY_DAYS), SalesItem.date < today):
            raw.setdefault(pos, {})[day] = raw.get(pos, {}).get(day, 0.0) + float(qty or 0)
            self.names[pos] = name
        self.open_days = sorted({d for days in raw.values() for d in days})
        for pos, days in raw.items():
            first = min(days)
            self.items[pos] = {d: days.get(d, 0.0) for d in self.open_days if d >= first}
        self.total: History = {d: sum(days.get(d, 0.0) for days in raw.values()) for d in self.open_days}
        self.categories = {pos: (category or "Other") for pos, category in db.query(PosItem.pos_item_id, PosItem.category).filter(PosItem.restaurant_id == restaurant_id)}

    @property
    def weeks(self) -> int:
        return len(self.open_days) // 7

    def recent(self, pos: str, days: int = 28) -> float:
        return sum(v for d, v in self.items.get(pos, {}).items() if d >= self.today - timedelta(days=days))

    def average(self, pos: str, days: int = 28) -> float:
        open_ = [d for d in self.open_days if d >= self.today - timedelta(days=days)]
        return self.recent(pos, days) / len(open_) if open_ else 0.0


def drivers_for(db: Session, restaurant_id: str, start: date, end: date) -> Drivers:
    """The outside conditions for each day: the built-in holiday calendar, and what the owner has entered."""
    restaurant = db.get(Restaurant, restaurant_id)
    out = forecasting.holiday_drivers(restaurant.country if restaurant else None, start, end)
    for row in db.query(DemandDriver).filter(DemandDriver.restaurant_id == restaurant_id, DemandDriver.date >= start, DemandDriver.date <= end):
        payload = row.payload or {}
        # An event is learned per size and a promotion per type; anything else per name.
        name = str(payload.get("size") or "medium") if row.kind == "local_event" else str(payload.get("type") or row.name) if row.kind == "promo" else row.name
        expected = payload.get("expected_multiplier")
        out.setdefault(row.date, []).append(Driver(row.kind, name, float(expected) if expected is not None else None))
    return out


def _hash(sales: Sales, drivers: Drivers, stage: str) -> str:
    body = {"stage": stage, "today": sales.today.isoformat(), "items": {pos: sorted((d.isoformat(), v) for d, v in days.items()) for pos, days in sales.items.items()},
            "drivers": sorted((d.isoformat(), sorted((x.kind, x.name, x.expected) for x in held)) for d, held in drivers.items())}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def forecast_items(db: Session, restaurant_id: str, now: datetime | None = None) -> dict:
    """Write the next 14 days of item forecasts and one `forecast_runs` row. The same inputs write nothing new."""
    now = now or datetime.now(timezone.utc)
    world = menu_costs.load(db, restaurant_id, now)
    today = world.today
    sales = Sales(db, restaurant_id, today)
    if sales.weeks < MIN_WEEKS:
        return {"state": "setup", "weeks": sales.weeks, "written": 0}
    drivers = drivers_for(db, restaurant_id, today - timedelta(days=HISTORY_DAYS), today + timedelta(days=HORIZON_DAYS))
    stage, scores = forecasting.choose_stage(sales.total, drivers, today)
    digest = _hash(sales, drivers, stage)
    # Only the newest run counts: inputs that change and change back (a closure added, then removed) must be worked out again.
    done = latest_run(db, restaurant_id)
    if done is not None and done.inputs_hash == digest:
        return {"state": "live", "run_id": done.id, "written": 0, "stage": stage}
    run = ForecastRun(restaurant_id=restaurant_id, kind=KIND, model="items", model_version=stage, inputs_hash=digest, started_at=now, status="running")
    db.add(run)
    db.flush()
    model = forecasting.STAGES[stage]()

    small = {pos for pos in sales.items if sales.average(pos) < SMALL_ITEM_UNITS}
    by_category: dict[str, History] = {}
    for pos, days in sales.items.items():
        held = by_category.setdefault(sales.categories.get(pos, "Other"), {})
        for day, value in days.items():
            held[day] = held.get(day, 0.0) + value
    category_recent = {c: sum(v for d, v in days.items() if d >= today - timedelta(days=28)) for c, days in by_category.items()}

    held_rows = {(r.pos_item_id, r.business_date): r for r in db.query(ItemForecast).filter(ItemForecast.restaurant_id == restaurant_id, ItemForecast.business_date >= today)}
    totals: dict[date, list[float]] = {}
    written = 0
    for offset in range(HORIZON_DAYS + 1):
        day = today + timedelta(days=offset)
        category_made = {c: forecasting.forecast_day(model, days, day, drivers) for c, days in by_category.items()}
        for pos in sorted(sales.items):
            category = sales.categories.get(pos, "Other")
            if pos in small and category_recent.get(category, 0) > 0 and category_made.get(category) is not None:
                made, method = category_made[category], "category_share"
                quantiles = made.quantiles.scaled(sales.recent(pos) / category_recent[category])
            else:
                made, method = forecasting.forecast_day(model, sales.items[pos], day, drivers), "item"
                if made is None:
                    continue
                quantiles = made.quantiles
            values = {"pos_item_name": sales.names.get(pos, pos)[:200], "p10": round(quantiles.p10, 4), "p50": round(quantiles.p50, 4), "p90": round(quantiles.p90, 4),
                      "method": method, "stage": made.stage, "drivers_applied": made.drivers_applied, "run_id": run.id}
            row = held_rows.get((pos, day))
            if row is None:
                db.add(ItemForecast(restaurant_id=restaurant_id, pos_item_id=pos, business_date=day, **values))
            else:
                for name, value in values.items():
                    setattr(row, name, value)
            written += 1
            sums = totals.setdefault(day, [0.0, 0.0, 0.0])
            for i, value in enumerate((quantiles.p10, quantiles.p50, quantiles.p90)):
                sums[i] += value
    # The day's total of items, for the demand chart: the sum of the item forecasts, with the drivers the total would get.
    held_totals = {r.business_date: r for r in db.query(ForecastInterval).filter(
        ForecastInterval.restaurant_id == restaurant_id, ForecastInterval.grain == "day", ForecastInterval.metric == "items", ForecastInterval.channel == "all",
        ForecastInterval.kind == KIND, ForecastInterval.business_date >= today)}
    for day, (low, mid, high) in totals.items():
        total_made = forecasting.forecast_day(model, sales.total, day, drivers)
        values = {"p10": round(low, 4), "p50": round(mid, 4), "p90": round(high, 4), "drivers_applied": total_made.drivers_applied if total_made else [], "run_id": run.id}
        row = held_totals.get(day)
        if row is None:
            db.add(ForecastInterval(restaurant_id=restaurant_id, business_date=day, interval_start=day_bounds(day, world.conf.timezone, world.conf.business_day_start)[0],
                                    metric="items", channel="all", kind=KIND, grain="day", **values))
        else:
            for name, value in values.items():
                setattr(row, name, value)
    run.metrics = {"stage": stage, "scores": scores, "weeks_of_data": sales.weeks, **accuracy(sales, drivers, model, today)}
    run.status, run.finished_at = "done", datetime.now(timezone.utc)
    db.flush()
    events.emit(db, restaurant_id, "forecast.computed", "forecast_runs", run.id, {"kind": KIND, "model": "items", "stage": stage, "written": written})
    return {"state": "live", "run_id": run.id, "written": written, "stage": stage}


def accuracy(sales: Sales, drivers: Drivers, model, today: date) -> dict:
    """Average miss of the item forecasts, replayed week by week over the last 12 weeks: per week, for the last 28
    days and the 28 before, and the share of daily totals that fell inside the Likely range over 8 weeks."""
    start = today - timedelta(days=84)
    per_week: dict[date, forecasting.Score] = {}
    for days in sales.items.values():
        _, rows = forecasting.backtest(model, days, drivers, start, today - timedelta(days=1))
        for day, actual, made in rows:
            per_week.setdefault(start + timedelta(days=((day - start).days // 7) * 7), forecasting.Score()).add(actual, made)

    def over(first: date, last: date) -> float | None:
        score = forecasting.Score()
        for week, held in per_week.items():
            if first <= week <= last:
                score.abs_error += held.abs_error
                score.actual += held.actual
        return None if score.wape is None else round(score.wape, 6)

    total_score, total_rows = forecasting.backtest(model, sales.total, drivers, today - timedelta(days=56), today - timedelta(days=1))
    recent = [{"date": d.isoformat(), "actual": round(a, 2), "p10": round(q.p10, 2), "p50": round(q.p50, 2), "p90": round(q.p90, 2)} for d, a, q in total_rows[-14:]]
    return {"weekly": [{"week": w.isoformat(), "wape": None if s.wape is None else round(s.wape, 6)} for w, s in sorted(per_week.items())],
            "wape_28": over(today - timedelta(days=28), today), "wape_prior_28": over(today - timedelta(days=56), today - timedelta(days=29)),
            "coverage_56": None if total_score.coverage is None else round(total_score.coverage, 4), "recent_totals": recent}


def latest_run(db: Session, restaurant_id: str) -> ForecastRun | None:
    return db.query(ForecastRun).filter(ForecastRun.restaurant_id == restaurant_id, ForecastRun.kind == KIND, ForecastRun.model == "items",
                                        ForecastRun.status == "done").order_by(ForecastRun.started_at.desc(), ForecastRun.id).first()


def ingredient_demand(db: Session, world, start: date, end: date) -> dict[str, dict[date, tuple[Decimal, Decimal, Decimal]]]:
    """item id -> day -> (p10, p50, p90) of ingredient demand in base units: the sum over dishes of the item forecast
    times what a portion uses. Summing percentiles assumes dishes rise and fall together within a day, which is
    deliberately cautious."""
    from app.margin_labor import costing

    use: dict[str, dict[str, Decimal]] = {}
    for recipe in sorted(world.recipes.values(), key=lambda r: (r.name, r.id)):
        row = world.rows[recipe.id]
        if recipe.confirmed and recipe.kind == "menu_item" and row.pos_item_id and row.pos_item_id not in use:
            use[row.pos_item_id] = costing.usage(recipe, world.recipes, world.items)
    out: dict[str, dict[date, tuple[Decimal, Decimal, Decimal]]] = {}
    for f in db.query(ItemForecast).filter(ItemForecast.restaurant_id == world.restaurant_id, ItemForecast.business_date >= start, ItemForecast.business_date <= end):
        for item_id, per_portion in use.get(f.pos_item_id, {}).items():
            low, mid, high = out.setdefault(item_id, {}).get(f.business_date, (Decimal(0), Decimal(0), Decimal(0)))
            out[item_id][f.business_date] = (low + Decimal(str(f.p10)) * per_portion, mid + Decimal(str(f.p50)) * per_portion, high + Decimal(str(f.p90)) * per_portion)
    return out
