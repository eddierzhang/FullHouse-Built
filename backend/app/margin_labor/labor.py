"""Module D, labor forecasting (spec sections 8.2 and 8.3): demand in 15-minute intervals with a range, and from
it the people needed in each role every half hour.

`forecast_labor` is the job. It reads timestamped orders, forecasts each series a day at a time with the forecast
core, calibrates the range against the last 8 weeks, splits the day by the weekday's own shape, and turns each
role's units into people with the role's standard. Standards are learned from punches or set by the owner, whose
value always wins. Nothing here writes a schedule or a punch, and no wage is read.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.margin_labor import demand, forecasting, labor_math
from app.margin_labor import settings as ml_settings
from app.margin_labor.business import business_date, zone
from app.margin_labor.forecasting import Quantiles
from app.margin_labor.labor_math import INTERVAL_MINUTES, PER_DAY
from app.margin_labor.models import (ForecastInterval, ForecastRun, LaborPunch, LaborRequirement, LaborRole, LaborStandard, PosItem, SalesOrder,
                                     SalesOrderLine)
from app.platform import events
from app.restaurant.models import Restaurant

KIND = "weekly"
MODEL = "labor"
HORIZON_DAYS = 13
HISTORY_DAYS = 120
LEARN_DAYS = 56
METRICS = ("orders", "covers", "net")
COUNTED = ("closed", "open")
WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DINE_IN = {"channels": ["dine_in"]}
OFF_PREMISE = {"channels": ["takeout", "delivery_own", "delivery_platform"]}
BAR_CATEGORIES = ["Bar", "Drinks", "Beverages", "Beer", "Wine", "Cocktails", "Liquor"]
#: The default driver for a role, by a word in its key (spec 8.3). Anything else is driven by orders.
DEFAULT_DRIVERS = (("server", "covers", DINE_IN), ("host", "covers", DINE_IN), ("dish", "covers", DINE_IN), ("bartender", "items", {"categories": BAR_CATEGORIES}),
                   ("bar", "items", {"categories": BAR_CATEGORIES}), ("cook", "items", {}), ("chef", "items", {}), ("expo", "orders", OFF_PREMISE),
                   ("packer", "orders", OFF_PREMISE), ("prep", "none", {}))


def default_driver(role_key: str, has_covers: bool) -> tuple[str, dict]:
    for word, metric, filt in DEFAULT_DRIVERS:
        if word in role_key:
            # Covers are the POS guest counts and are never estimated: without them, a cover-driven role follows orders.
            return ("orders", filt) if metric == "covers" and not has_covers else (metric, filt)
    return "orders", {}


class Service:
    """Timestamped orders by business date and 15-minute slot of the local clock, loaded once."""

    def __init__(self, db: Session, restaurant_id: str, now: datetime | None = None, days: int = HISTORY_DAYS):
        self.db, self.restaurant_id = db, restaurant_id
        self.conf = ml_settings.for_restaurant(db, restaurant_id)
        self.tz = zone(self.conf.timezone)
        self.now = now or datetime.now(timezone.utc)
        self.today = business_date(self.now, self.conf.timezone, self.conf.business_day_start)
        self.restaurant = db.get(Restaurant, restaurant_id)
        first = self.today - timedelta(days=days)
        self.orders = db.query(SalesOrder.id, SalesOrder.opened_at, SalesOrder.business_date, SalesOrder.channel, SalesOrder.covers, SalesOrder.net).filter(
            SalesOrder.restaurant_id == restaurant_id, SalesOrder.business_date >= first, SalesOrder.business_date <= self.today, SalesOrder.status.in_(COUNTED)).all()
        self.slot_of = {o.id: self.slot(o.opened_at) for o in self.orders}
        self.open_days = sorted({o.business_date for o in self.orders if o.business_date < self.today})
        dine_in = [o for o in self.orders if o.channel == "dine_in"]
        self.has_covers = bool(dine_in) and sum(1 for o in dine_in if o.covers) >= len(dine_in) / 2
        self._lines = None
        self._series: dict[str, dict[date, list[float]]] = {}

    @property
    def weeks(self) -> int:
        return len(self.open_days) // 7

    @property
    def primary(self) -> str:
        """What the week is talked about in: covers where the POS gives guest counts, else orders."""
        return "covers" if self.has_covers else "orders"

    def slot(self, moment: datetime) -> int:
        local = (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(self.tz)
        return (local.hour * 60 + local.minute) // INTERVAL_MINUTES

    def slot_start(self, day: date, slot: int) -> datetime:
        """The moment (UTC) a slot of a business date starts. Slots before the day's start belong to the next calendar day."""
        hours, _, minutes = self.conf.business_day_start.partition(":")
        first_slot = (int(hours) * 60 + int(minutes or 0)) // INTERVAL_MINUTES
        calendar = day + timedelta(days=1) if slot < first_slot else day
        return datetime.combine(calendar, time(slot * INTERVAL_MINUTES // 60, slot * INTERVAL_MINUTES % 60), tzinfo=self.tz).astimezone(timezone.utc)

    def lines(self) -> list:
        if self._lines is None:
            categories = dict(self.db.query(PosItem.pos_item_id, PosItem.category).filter(PosItem.restaurant_id == self.restaurant_id))
            ids = {o.id: o for o in self.orders}
            self._lines = [(ids[order_id], qty, categories.get(pos) or "Other") for order_id, pos, qty in self.db.query(
                SalesOrderLine.order_id, SalesOrderLine.pos_item_id, SalesOrderLine.qty).filter(
                SalesOrderLine.restaurant_id == self.restaurant_id, SalesOrderLine.business_date >= self.today - timedelta(days=HISTORY_DAYS),
                SalesOrderLine.voided.is_(False)) if order_id in ids]
        return self._lines

    def series(self, metric: str, filt: dict | None = None) -> dict[date, list[float]]:
        """business date -> the metric in each 15-minute slot, for the sales the filter admits."""
        filt = filt or {}
        key = json.dumps([metric, filt], sort_keys=True)
        if key in self._series:
            return self._series[key]
        channels, categories = set(filt.get("channels") or ()), {c.lower() for c in filt.get("categories") or ()}
        out: dict[date, list[float]] = {d: [0.0] * PER_DAY for d in {o.business_date for o in self.orders}}
        if metric == "items":
            for order, qty, category in self.lines():
                if (not channels or order.channel in channels) and (not categories or category.lower() in categories):
                    out[order.business_date][self.slot_of[order.id]] += float(qty)
        else:
            for order in self.orders:
                if channels and order.channel not in channels:
                    continue
                value = 1.0 if metric == "orders" else float(order.covers or 0) if metric == "covers" else float(order.net or 0)
                out[order.business_date][self.slot_of[order.id]] += value
        self._series[key] = out
        return out

    def daily(self, slots: dict[date, list[float]]) -> forecasting.History:
        return {d: sum(slots[d]) for d in self.open_days if d in slots}

    def hours(self, day: date) -> tuple[int, int] | None:
        """(opening slot, closing slot) of the local clock for a business date; None when closed."""
        held = (self.restaurant.opening_hours or {}).get(WEEKDAY_KEYS[day.weekday()]) if self.restaurant else None
        if not held or held.get("closed") or not held.get("open") or not held.get("close"):
            return None
        to_slot = lambda text: (int(text[:2]) * 60 + int(text[3:5])) // INTERVAL_MINUTES  # noqa: E731
        return to_slot(held["open"]), to_slot(held["close"])


# --- roles and standards ----------------------------------------------------------------------------------


def slug(name: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")[:40]


def ensure_roles(db: Session, restaurant_id: str) -> list[LaborRole]:
    """The restaurant's roles: those saved, plus any a punch or the staff list names that is not there yet."""
    held = {r.key: r for r in db.query(LaborRole).filter(LaborRole.restaurant_id == restaurant_id)}
    restaurant = db.get(Restaurant, restaurant_id)
    named = {slug(n): n for n in (restaurant.staff_roles or [])} if restaurant else {}
    for (key,) in db.query(LaborPunch.role_key).filter(LaborPunch.restaurant_id == restaurant_id).distinct():
        named.setdefault(key, key.replace("_", " ").capitalize())
    for key, name in named.items():
        if key and key not in held:
            held[key] = LaborRole(restaurant_id=restaurant_id, key=key, name=name[:80], is_tipped=any(w in key for w in ("server", "bartender", "bar")))
            db.add(held[key])
    db.flush()
    return sorted(held.values(), key=lambda r: r.name)


def standards(db: Session, restaurant_id: str, day: date) -> dict[str, LaborStandard]:
    """The standard in effect for each role on `day`: the owner's when there is one, else the learned one."""
    out: dict[str, LaborStandard] = {}
    for row in db.query(LaborStandard).filter(LaborStandard.restaurant_id == restaurant_id, LaborStandard.effective_from <= day).order_by(LaborStandard.effective_from, LaborStandard.created_at):
        current = out.get(row.role_key)
        if current is None or row.source == "manual" or current.source == "learned":
            out[row.role_key] = row
    return out


def labor_hours(db: Session, service: Service, role_key: str, since: date) -> dict[tuple[date, int], float]:
    """(business date, 30-minute slot) -> hours clocked in the role in that half hour."""
    out: dict[tuple[date, int], float] = {}
    for punch in db.query(LaborPunch).filter(LaborPunch.restaurant_id == service.restaurant_id, LaborPunch.role_key == role_key, LaborPunch.business_date >= since,
                                             LaborPunch.clock_out.isnot(None)):
        start = punch.clock_in if punch.clock_in.tzinfo else punch.clock_in.replace(tzinfo=timezone.utc)
        end = punch.clock_out if punch.clock_out.tzinfo else punch.clock_out.replace(tzinfo=timezone.utc)
        moment = start.astimezone(service.tz).replace(minute=start.astimezone(service.tz).minute // 30 * 30, second=0, microsecond=0)
        while moment < end:
            overlap = (min(end, moment + timedelta(minutes=30)) - max(start, moment)).total_seconds() / 3600
            if overlap > 0:
                key = (punch.business_date, (moment.hour * 60 + moment.minute) // 30)
                out[key] = out.get(key, 0.0) + overlap
            moment += timedelta(minutes=30)
    return out


def observations(db: Session, service: Service, role_key: str, metric: str, filt: dict) -> tuple[list[tuple[float, float]], int]:
    """((units, labor hours) per 30-minute interval of the last 8 weeks, weeks of punches)."""
    since = service.today - timedelta(days=LEARN_DAYS)
    hours = labor_hours(db, service, role_key, since)
    slots = service.series(metric, filt) if metric != "none" else {}
    out = []
    for (day, half), worked in sorted(hours.items()):
        day_slots = slots.get(day)
        if day_slots is None or day >= service.today:
            continue
        out.append((day_slots[half * 2] + day_slots[half * 2 + 1], worked))
    return out, len({day for day, _half in hours}) // 7


def learn(db: Session, service: Service) -> int:
    """Write this week's learned standard for each role with a driver. Learned rows only: an owner's value is never touched."""
    written = 0
    held = standards(db, service.restaurant_id, service.today)
    for role in ensure_roles(db, service.restaurant_id):
        current = held.get(role.key)
        metric, filt = (current.driver_metric, current.driver_filter or {}) if current is not None else default_driver(role.key, service.has_covers)
        if metric == "none":
            continue
        value, sample = labor_math.learn_standard(*observations(db, service, role.key, metric, filt))
        if value is None:
            continue
        latest = db.query(LaborStandard).filter(LaborStandard.restaurant_id == service.restaurant_id, LaborStandard.role_key == role.key,
                                                LaborStandard.source == "learned").order_by(LaborStandard.effective_from.desc()).first()
        exact = Decimal(str(value)).quantize(Decimal("0.0001"))
        if latest is not None and latest.units_per_labor_hour == exact and latest.sample_size == sample:
            continue
        if latest is not None and latest.effective_from == service.today:
            latest.units_per_labor_hour, latest.sample_size = exact, sample
        else:
            db.add(LaborStandard(restaurant_id=service.restaurant_id, role_key=role.key, driver_metric=metric, driver_filter=filt, units_per_labor_hour=exact,
                                 min_on_floor=latest.min_on_floor if latest else 1, source="learned", sample_size=sample, effective_from=service.today))
        written += 1
    db.flush()
    return written


# --- the forecast and the requirements ----------------------------------------------------------------------


def state_for(weeks: int, has_standard: bool) -> str:
    """Setup under 4 weeks of timestamped orders or with no standard set; learning to 8 weeks; then live."""
    return "setup" if weeks < 4 or not has_standard else "learning" if weeks < 8 else "live"


class SeriesForecast:
    """One series forecast for the days ahead: by day, and by 15-minute interval."""

    def __init__(self, service: Service, slots: dict[date, list[float]], drivers, model, days: list[date]):
        self.history = service.daily(slots)
        _, rows = forecasting.backtest(model, self.history, drivers, service.today - timedelta(days=56), service.today - timedelta(days=1))
        self.backtest = [(actual, made) for _day, actual, made in rows]
        self.factor = labor_math.calibration_factor(self.backtest)
        self.coverage = labor_math.coverage(self.backtest)
        self.by_day: dict[date, forecasting.Forecast] = {}
        self.by_slot: dict[date, list[Quantiles]] = {}
        for day in days:
            made = forecasting.forecast_day(model, self.history, day, drivers)
            if made is None:
                continue
            made.quantiles = made.quantiles.widened(self.factor) if self.factor != 1.0 else made.quantiles
            same = [slots[d] for d in (day - timedelta(days=7 * k) for k in range(1, labor_math.SHARE_WEEKS + 6)) if d in slots and d < service.today][:labor_math.SHARE_WEEKS]
            share = labor_math.shares(same)
            self.by_day[day] = made
            self.by_slot[day] = labor_math.split(made.quantiles, share, labor_math.range_ratios(same, share))


def _hash(service: Service, held: dict[str, LaborStandard], drivers) -> str:
    body = {"today": service.today.isoformat(), "orders": sorted((o.id, str(o.opened_at), o.channel, o.covers, str(o.net)) for o in service.orders),
            "standards": sorted((k, s.driver_metric, json.dumps(s.driver_filter, sort_keys=True), str(s.units_per_labor_hour), s.min_on_floor, s.open_lead_minutes,
                                 s.close_lag_minutes) for k, s in held.items()),
            "hours": service.restaurant.opening_hours if service.restaurant else None,
            "drivers": sorted((d.isoformat(), sorted((x.kind, x.name, x.expected) for x in v)) for d, v in drivers.items()), "v": labor_math.CALC_VERSION}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def requirements_for(service: Service, standard: LaborStandard, slots: list[Quantiles] | None, day: date) -> list[dict]:
    """People needed each half hour of one day for one role: from its units and standard, and at least the floor
    from `open_lead_minutes` before opening to `close_lag_minutes` after closing."""
    hours = service.hours(day)
    floor_from = floor_to = None
    if hours is not None:
        floor_from = (hours[0] * INTERVAL_MINUTES - standard.open_lead_minutes) // 30
        floor_to = math.ceil((hours[1] * INTERVAL_MINUTES + standard.close_lag_minutes) / 30)
    rate = float(standard.units_per_labor_hour or 0)
    out = []
    for half in range(PER_DAY // 2):
        p50 = (slots[half * 2].p50 + slots[half * 2 + 1].p50) if slots else 0.0
        p90 = (slots[half * 2].p90 + slots[half * 2 + 1].p90) if slots else 0.0
        floor = standard.min_on_floor if floor_from is not None and floor_from <= half < floor_to else 0
        need_p50 = labor_math.need(p50, rate, floor) if rate > 0 else floor
        need_p90 = max(need_p50, labor_math.need(p90, rate, floor) if rate > 0 else floor)
        if need_p50 or need_p90:
            out.append({"half": half, "units_p50": round(p50, 4), "units_p90": round(p90, 4), "need_p50": need_p50, "need_p90": need_p90})
    return out


def forecast_labor(db: Session, restaurant_id: str, now: datetime | None = None) -> dict:
    """Forecast the next two weeks by interval and write the people needed per role. The same inputs write nothing new."""
    service = Service(db, restaurant_id, now)
    learn(db, service)
    held = standards(db, restaurant_id, service.today)
    state = state_for(service.weeks, bool(held))
    if service.weeks < 4:
        return {"state": "setup", "weeks": service.weeks, "written": 0}
    days = [service.today + timedelta(days=n) for n in range(HORIZON_DAYS + 1)]
    drivers = demand.drivers_for(db, restaurant_id, service.today - timedelta(days=HISTORY_DAYS), days[-1])
    digest = _hash(service, held, drivers)
    # Only the newest run counts: inputs that change and change back (a standard set, then dropped) must be worked out again.
    done = latest_run(db, restaurant_id)
    if done is not None and done.inputs_hash == digest:
        return {"state": state, "run_id": done.id, "written": 0}
    primary_history = service.daily(service.series(service.primary))
    stage, scores = forecasting.choose_stage(primary_history, drivers, service.today)
    model = forecasting.STAGES[stage]()
    run = ForecastRun(restaurant_id=restaurant_id, kind=KIND, model=MODEL, model_version=stage, inputs_hash=digest, started_at=service.now, status="running")
    db.add(run)
    db.flush()

    db.query(ForecastInterval).filter(ForecastInterval.restaurant_id == restaurant_id, ForecastInterval.kind == KIND, ForecastInterval.channel == "all",
                                      ForecastInterval.metric.in_(METRICS), ForecastInterval.business_date >= service.today).delete(synchronize_session=False)
    made: dict[str, SeriesForecast] = {}
    written = 0
    for metric in METRICS:
        if metric == "covers" and not service.has_covers:
            continue
        made[metric] = SeriesForecast(service, service.series(metric), drivers, model, days)
        for day, forecast in made[metric].by_day.items():
            q = forecast.quantiles
            db.add(ForecastInterval(restaurant_id=restaurant_id, business_date=day, interval_start=service.slot_start(day, 16), metric=metric, channel="all", kind=KIND,
                                    grain="day", p10=round(q.p10, 4), p50=round(q.p50, 4), p90=round(q.p90, 4), drivers_applied=forecast.drivers_applied, run_id=run.id))
            for slot, sq in enumerate(made[metric].by_slot[day]):
                if sq.p90 > 0:
                    db.add(ForecastInterval(restaurant_id=restaurant_id, business_date=day, interval_start=service.slot_start(day, slot), metric=metric, channel="all",
                                            kind=KIND, grain="interval", p10=round(sq.p10, 4), p50=round(sq.p50, 4), p90=round(sq.p90, 4), drivers_applied=[], run_id=run.id))
                    written += 1

    db.query(LaborRequirement).filter(LaborRequirement.restaurant_id == restaurant_id, LaborRequirement.kind == KIND,
                                      LaborRequirement.business_date >= service.today).delete(synchronize_session=False)
    needs = 0
    for role_key, standard in sorted(held.items()):
        role_made = SeriesForecast(service, service.series(standard.driver_metric, standard.driver_filter or {}), drivers, model, days) if standard.driver_metric != "none" else None
        for day in days:
            for row in requirements_for(service, standard, role_made.by_slot.get(day) if role_made else None, day):
                db.add(LaborRequirement(restaurant_id=restaurant_id, business_date=day, interval_start=service.slot_start(day, row["half"] * 2), role_key=role_key,
                                        units_p50=row["units_p50"], units_p90=row["units_p90"], need_p50=row["need_p50"], need_p90=row["need_p90"], kind=KIND, run_id=run.id))
                needs += 1
    primary = made[service.primary]
    run.metrics = {"stage": stage, "scores": scores, "weeks_of_data": service.weeks, "primary": service.primary, "calibration_factor": primary.factor,
                   "coverage_56": None if primary.coverage is None else round(primary.coverage, 4), "dayparts": daypart_accuracy(service, drivers, model)}
    run.status, run.finished_at = "done", datetime.now(timezone.utc)
    db.flush()
    events.emit(db, restaurant_id, "forecast.computed", "forecast_runs", run.id, {"kind": KIND, "model": MODEL, "stage": stage, "intervals": written})
    events.emit(db, restaurant_id, "requirements.computed", "labor_requirements", run.id, {"rows": needs, "roles": sorted(held)})
    return {"state": state, "run_id": run.id, "written": written, "requirements": needs, "stage": stage}


def daypart_accuracy(service: Service, drivers, model, days: int = 84) -> list[dict]:
    """Average miss by daypart over the last 12 weeks: each week forecast from what was known before it, split by the
    shape of the 8 weeks before."""
    slots = service.series(service.primary)
    history = service.daily(slots)
    scores: dict[str, list[float]] = {}
    start = service.today - timedelta(days=days)
    week = start
    while week < service.today:
        known = {d: v for d, v in history.items() if d < week}
        for n in range(7):
            day = week + timedelta(days=n)
            if day >= service.today or day not in history or not known:
                continue
            made = model.forecast(known, day, drivers)
            if made is None:
                continue
            same = [slots[d] for d in (day - timedelta(days=7 * k) for k in range(1, 14)) if d in slots and d < week][:labor_math.SHARE_WEEKS]
            share = labor_math.shares(same)
            for name, first, last in labor_math.DAYPARTS:
                span = range(first * 4, last * 4)
                actual = sum(slots[day][i] for i in span)
                expected = made.quantiles.p50 * sum(share[i] for i in span)
                held = scores.setdefault(name, [0.0, 0.0])
                held[0] += abs(actual - expected)
                held[1] += actual
        week += timedelta(days=7)
    return [{"daypart": name, "wape": round(scores[name][0] / scores[name][1], 6)} for name, _f, _l in labor_math.DAYPARTS if name in scores and scores[name][1] > 0]


def latest_run(db: Session, restaurant_id: str) -> ForecastRun | None:
    return db.query(ForecastRun).filter(ForecastRun.restaurant_id == restaurant_id, ForecastRun.kind == KIND, ForecastRun.model == MODEL,
                                        ForecastRun.status == "done").order_by(ForecastRun.started_at.desc(), ForecastRun.id).first()


def preview(db: Session, restaurant_id: str, role_key: str, units_per_labor_hour: Decimal, min_on_floor: int, now: datetime | None = None) -> dict:
    """What a changed standard would do to the role's hours over the next 7 days, before it is saved. Nothing is written."""
    service = Service(db, restaurant_id, now)
    held = standards(db, restaurant_id, service.today)
    current = held.get(role_key)
    metric, filt = (current.driver_metric, current.driver_filter or {}) if current is not None else default_driver(role_key, service.has_covers)
    days = [service.today + timedelta(days=n) for n in range(1, 8)]
    drivers = demand.drivers_for(db, restaurant_id, service.today - timedelta(days=HISTORY_DAYS), days[-1])
    run = latest_run(db, restaurant_id)
    model = forecasting.STAGES[(run.model_version if run else "baseline")]()
    made = SeriesForecast(service, service.series(metric, filt), drivers, model, days) if metric != "none" else None
    changed = LaborStandard(role_key=role_key, driver_metric=metric, driver_filter=filt, units_per_labor_hour=units_per_labor_hour, min_on_floor=min_on_floor,
                            open_lead_minutes=current.open_lead_minutes if current else 0, close_lag_minutes=current.close_lag_minutes if current else 0)
    out = []
    for day in days:
        slots = made.by_slot.get(day) if made else None
        before = sum(r["need_p50"] for r in requirements_for(service, current, slots, day)) * 0.5 if current is not None else 0.0
        after = sum(r["need_p50"] for r in requirements_for(service, changed, slots, day)) * 0.5
        out.append({"date": day, "before": before, "after": after})
    top = max(out, key=lambda d: (abs(d["after"] - d["before"]), d["after"]))
    return {"role_key": role_key, "days": out, "week_before": sum(d["before"] for d in out), "week_after": sum(d["after"] for d in out), "top": top}
