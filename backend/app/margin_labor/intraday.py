"""Module D, intraday (spec section 8.6): during service, compare the day so far with the forecast, revise the
rest of the day and the people needed, and suggest a cut, an extension or a call-in to the manager.

A suggestion is not a proposal. It goes to the manager only, on the Today screen and as a notification; it expires
after 20 minutes; and accepting it records the manager's decision. The manager tells the employee. Nothing here
messages staff or writes to a time clock.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.margin_labor import demand, forecasting, intraday_math, labor, labor_math, schedule as schedules, scheduling, words
from app.margin_labor.intraday_math import COOL_OFF_MINUTES, MAX_DAYPART_MISS, SUGGESTION_MINUTES
from app.margin_labor.labor_math import INTERVAL_MINUTES, PER_DAY
from app.margin_labor.models import ForecastInterval, LaborAdjustment, LaborPunch, LaborRequirement, LaborRole, Schedule, ScheduleShift
from app.platform import events

KIND = "intraday"
NOTICE = "labor_suggestion"


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class Day:
    """Today so far, against what was forecast: loaded once for the reforecast and the page."""

    def __init__(self, db: Session, restaurant_id: str, now: datetime | None = None):
        self.db = db
        self.service = labor.Service(db, restaurant_id, now)
        s = self.service
        self.now, self.day = s.now, s.today
        hours, _, minutes = s.conf.business_day_start.partition(":")
        self.first_slot = (int(hours) * 60 + int(minutes or 0)) // INTERVAL_MINUTES
        self.now_ord = self.ord(s.slot(self.now))
        self.forecast: dict[int, forecasting.Quantiles] = {}
        for row in db.query(ForecastInterval).filter(ForecastInterval.restaurant_id == restaurant_id, ForecastInterval.business_date == self.day, ForecastInterval.kind == labor.KIND,
                                                     ForecastInterval.grain == "interval", ForecastInterval.metric == s.primary, ForecastInterval.channel == "all"):
            self.forecast[s.slot(row.interval_start)] = forecasting.Quantiles(row.p10, row.p50, row.p90)
        self.actual = s.series(s.primary).get(self.day, [0.0] * PER_DAY)
        self.net = s.series("net").get(self.day, [0.0] * PER_DAY)
        done = [slot for slot in range(PER_DAY) if self.ord(slot) < self.now_ord]
        self.forecast_so_far = sum(self.forecast[slot].p50 for slot in done if slot in self.forecast)
        self.forecast_day = sum(q.p50 for q in self.forecast.values())
        self.actual_so_far = sum(self.actual[slot] for slot in done)
        self.net_so_far = sum(self.net[slot] for slot in done)
        self.pace = intraday_math.reforecast(self.forecast_so_far, self.forecast_day, self.actual_so_far)
        self.held = labor.standards(db, restaurant_id, self.day)
        self.state = labor.state_for(s.weeks, bool(self.held))
        week_of = self.day - timedelta(days=self.day.weekday())
        self.schedule = db.query(Schedule).filter(Schedule.restaurant_id == restaurant_id, Schedule.week_of == week_of).first()
        self.shifts = []
        if self.schedule is not None:
            self.shifts = [schedules.as_spec(s, row) for row in db.query(ScheduleShift).filter(ScheduleShift.schedule_id == self.schedule.id, ScheduleShift.business_date == self.day)]
        self.punched: dict[str, int] | None = None
        punches = db.query(LaborPunch).filter(LaborPunch.restaurant_id == restaurant_id, LaborPunch.business_date == self.day).all()
        if punches:
            self.punched = {}
            for p in punches:
                if _aware(p.clock_in) <= self.now and (p.clock_out is None or _aware(p.clock_out) > self.now):
                    self.punched[p.role_key] = self.punched.get(p.role_key, 0) + 1

    def ord(self, slot: int) -> int:
        """A local-clock slot's place in the business day, which starts at `business_day_start`."""
        return (slot - self.first_slot) % PER_DAY

    def minutes(self, moment: datetime) -> int:
        """Minutes from local midnight of the business date."""
        midnight = datetime.combine(self.day, datetime.min.time(), tzinfo=self.service.tz)
        return round((_aware(moment) - midnight).total_seconds() / 60)

    @property
    def now_minutes(self) -> int:
        return self.minutes(self.now)

    def scheduled(self, role: str, minute: int) -> list:
        return [s for s in self.shifts if s.role == role and s.person and s.start <= minute < s.end]

    def heads_now(self, role: str) -> int:
        """People on now: from today's punches when there are any, else from the schedule."""
        return self.punched.get(role, 0) if self.punched is not None else len(self.scheduled(role, self.now_minutes))


def reforecast(db: Session, restaurant_id: str, now: datetime | None = None) -> Day:
    """Revise the rest of today's forecast and the people needed, then make at most one suggestion per role.
    Running it again for the same moment leaves the same rows."""
    day = Day(db, restaurant_id, now)
    s = day.service
    db.query(ForecastInterval).filter(ForecastInterval.restaurant_id == restaurant_id, ForecastInterval.business_date == day.day, ForecastInterval.kind == KIND).delete(synchronize_session=False)
    db.query(LaborRequirement).filter(LaborRequirement.restaurant_id == restaurant_id, LaborRequirement.business_date == day.day, LaborRequirement.kind == KIND).delete(synchronize_session=False)
    day.revised = {}
    day.needs = {}
    if not day.forecast:
        db.flush()
        return day
    m = day.pace.multiplier
    for slot, q in sorted(day.forecast.items()):
        if day.ord(slot) < day.now_ord:
            continue
        revised = q.scaled(m)
        day.revised[slot] = revised
        db.add(ForecastInterval(restaurant_id=restaurant_id, business_date=day.day, interval_start=s.slot_start(day.day, slot), metric=s.primary, channel="all", kind=KIND,
                                grain="interval", p10=round(revised.p10, 4), p50=round(revised.p50, 4), p90=round(revised.p90, 4),
                                drivers_applied=[{"kind": "pace", "name": "Today so far", "multiplier": round(m, 4), "source": "intraday", "days": 0}], run_id=None))
    run = labor.latest_run(db, restaurant_id)
    model = forecasting.STAGES[run.model_version if run else "baseline"]()
    drivers = demand.drivers_for(db, restaurant_id, day.day - timedelta(days=labor.HISTORY_DAYS), day.day)
    for role_key, standard in sorted(day.held.items()):
        made = labor.SeriesForecast(s, s.series(standard.driver_metric, standard.driver_filter or {}), drivers, model, [day.day]) if standard.driver_metric != "none" else None
        slots = made.by_slot.get(day.day) if made else None
        scaled = [q.scaled(m) for q in slots] if slots else None
        day.needs[role_key] = {}
        for row in labor.requirements_for(s, standard, scaled, day.day):
            if day.ord(row["half"] * 2 + 1) < day.now_ord:
                continue  # that half hour is over
            minute = (row["half"] * 30) + (1440 if row["half"] * 2 < day.first_slot else 0)
            day.needs[role_key][minute] = (row["need_p50"], row["need_p90"])
            db.add(LaborRequirement(restaurant_id=restaurant_id, business_date=day.day, interval_start=s.slot_start(day.day, row["half"] * 2), role_key=role_key,
                                    units_p50=row["units_p50"], units_p90=row["units_p90"], need_p50=row["need_p50"], need_p90=row["need_p90"], kind=KIND, run_id=None))
    db.flush()
    day.suggestions_on, day.suggestions_why = _allowed(db, day, model, drivers)
    _expire(db, day)
    if day.suggestions_on:
        for role_key in sorted(day.held):
            _suggest(db, day, role_key)
    db.flush()
    return day


def _allowed(db: Session, day: Day, model, drivers) -> tuple[bool, str]:
    """Suggestions are made only with 8 weeks of data, once 15% of the day has passed, and while the average miss for
    this part of the day was 25% or lower over the last 4 weeks."""
    if day.state != "live":
        return False, "learning"
    if not day.pace.adjusted:
        return False, "early"
    part = labor_math.daypart(day.service.slot(day.now))
    miss = {d["daypart"]: d["wape"] for d in labor.daypart_accuracy(day.service, drivers, model, days=28)}.get(part)
    if miss is not None and miss > MAX_DAYPART_MISS:
        return False, "accuracy"
    return True, "on"


def _expire(db: Session, day: Day) -> None:
    for row in db.query(LaborAdjustment).filter(LaborAdjustment.restaurant_id == day.service.restaurant_id, LaborAdjustment.status == "proposed"):
        if _aware(row.expires_at) <= day.now:
            row.status = "expired"


def _next_halves(day: Day, count: int) -> list[int]:
    """The start minutes of the next `count` half hours after the one now is in."""
    first = (day.now_minutes // 30 + 1) * 30
    return [first + 30 * n for n in range(count)]


def _suggest(db: Session, day: Day, role_key: str) -> LaborAdjustment | None:
    rid = day.service.restaurant_id
    recent = db.query(LaborAdjustment).filter(LaborAdjustment.restaurant_id == rid, LaborAdjustment.business_date == day.day, LaborAdjustment.role_key == role_key).all()
    if any(r.status == "proposed" for r in recent):
        return None  # at most one open suggestion per role
    if any(r.status == "dismissed" and r.decided_at is not None and day.now - _aware(r.decided_at) < timedelta(minutes=COOL_OFF_MINUTES) for r in recent):
        return None  # a dismissal is respected for an hour
    standard, needs = day.held[role_key], day.needs.get(role_key, {})
    rules = schedules.rules_for(db, rid)
    team = schedules.Team(db, day.service, day.day - timedelta(days=day.day.weekday()))
    by_person, by_role = schedules.rates(db, rid, team)
    week_minutes: dict[str, int] = {}
    if day.schedule is not None:
        for row in db.query(ScheduleShift).filter(ScheduleShift.schedule_id == day.schedule.id, ScheduleShift.staff_id.isnot(None)):
            spec = schedules.as_spec(day.service, row)
            week_minutes[spec.person] = week_minutes.get(spec.person, 0) + scheduling.paid_minutes(spec, rules)
    at = math.ceil((day.now_minutes + 30) / 15) * 15  # at least half an hour's notice, on the quarter hour
    made = None
    three = _next_halves(day, 3)
    heads = [len(day.scheduled(role_key, minute)) for minute in three]
    if all(h > 0 and h > needs.get(minute, (0, 0))[1] and h - 1 >= standard.min_on_floor for h, minute in zip(heads, three)):
        on = [s for s in day.shifts if s.role == role_key and s.person and s.start <= day.now_minutes and s.end > three[-1]]
        # First the person closest to overtime this week, then the person on shift longest today.
        for shift in sorted(on, key=lambda s: (-week_minutes.get(s.person, 0), s.start, team.names.get(s.person, ""))):
            if rules.min_paid_hours is not None and (at - shift.start) < rules.min_paid_hours * 60:
                continue  # it would pay less than the least paid hours: it saves nothing
            rate = by_person.get(shift.person, by_role.get(role_key))
            first = week_minutes.get(shift.person, 0) == max(week_minutes.get(s.person, 0) for s in on) and len({week_minutes.get(s.person, 0) for s in on}) > 1
            made = ("cut", shift, at, None if rate is None else (Decimal(shift.end - at) / 60 * rate).quantize(Decimal("0.01")),
                    {"why": "closest_to_overtime" if first else "longest_on_shift", "heads": heads, "need_if_busy": [needs.get(mn, (0, 0))[1] for mn in three], "shift_end": shift.end})
            break
    if made is None:
        two = _next_halves(day, 2)
        short = [needs.get(minute, (0, 0))[0] > len(day.scheduled(role_key, minute)) for minute in two]
        if all(short) and all(minute in needs for minute in two):
            until = two[-1] + 30
            ending = sorted((s for s in day.shifts if s.role == role_key and s.person and s.start <= day.now_minutes < s.end <= two[-1]), key=lambda s: (s.end, team.names.get(s.person, "")))
            for shift in ending:
                longer = until - shift.start
                if rules.max_hours_per_day is not None and longer > rules.max_hours_per_day * 60:
                    continue
                if rules.daily_overtime_hours is not None and longer > rules.daily_overtime_hours * 60:
                    continue
                if rules.weekly_overtime_hours is not None and week_minutes.get(shift.person, 0) + (until - shift.end) > rules.weekly_overtime_hours * 60:
                    continue
                made = ("extend", shift, until, None, {"why": "ends_soonest", "need": [needs[mn][0] for mn in two], "heads": [len(day.scheduled(role_key, mn)) for mn in two], "shift_end": shift.end})
                break
            if made is None:
                flex = next((s for s in day.shifts if s.role == role_key and s.kind == "flex" and not (s.start <= day.now_minutes < s.end)), None)
                if flex is not None:
                    made = ("call_in", flex, at, None, {"why": "flex", "need": [needs[mn][0] for mn in two], "heads": [len(day.scheduled(role_key, mn)) for mn in two]})
    if made is None:
        return None
    kind, shift, minute, savings, reason = made
    name = team.names.get(shift.person or "", "")
    parts = name.split()
    reason["person"] = f"{parts[0]} {parts[-1][0]}." if len(parts) > 1 else name
    row = LaborAdjustment(restaurant_id=rid, business_date=day.day, kind=kind, role_key=role_key, shift_id=shift.id, suggested_time=schedules.to_moment(day.service, day.day, minute),
                          savings_estimate=savings, reason=reason, expires_at=day.now + timedelta(minutes=SUGGESTION_MINUTES),
                          created_at=day.now)  # the moment it was made for, which is the moment a later look compares against
    db.add(row)
    db.flush()
    from app.notify import service as notify

    role = db.query(LaborRole).filter(LaborRole.restaurant_id == rid, LaborRole.key == role_key).first()
    notify.tell(db, rid, kind=NOTICE, title=sentence(kind, role.name if role else role_key, _aware(row.suggested_time).astimezone(day.service.tz)), dedupe_key=f"labor:{row.id}",
                body="Open Today to accept or dismiss it. It expires in 20 minutes.", link="#labor/today")
    events.emit(db, rid, "adjustment.proposed", "labor_adjustments", row.id, {"kind": kind, "role_key": role_key})
    return row


def sentence(kind: str, role_name: str, at: datetime) -> str:
    """Cut one server at 7:45 pm."""
    when = f"{at.hour % 12 or 12}:{at.minute:02d} {'am' if at.hour < 12 else 'pm'}"
    role = role_name.lower()
    return {"cut": f"Cut one {role} at {when}.", "extend": f"Keep one {role} until {when}.", "call_in": f"Call in one {role} for {when}."}[kind]


def decide(db: Session, restaurant_id: str, adjustment_id: str, decision: str, who: str, now: datetime | None = None) -> LaborAdjustment:
    """Record the manager's decision. Accepting a cut or an extension moves the shift's end on the schedule, so the
    page's people-on figures follow; nobody is messaged."""
    row = db.get(LaborAdjustment, adjustment_id)
    if row is None or row.restaurant_id != restaurant_id:
        raise LookupError("No such suggestion.")
    now = now or datetime.now(timezone.utc)
    if row.status != "proposed" or _aware(row.expires_at) <= now:
        if row.status == "proposed":
            row.status = "expired"
            db.flush()
        raise ValueError("This suggestion has expired or was already decided.")
    row.status, row.decided_at, row.decided_by = decision, now, who
    if decision == "accepted" and row.kind in ("cut", "extend") and row.shift_id:
        shift = db.get(ScheduleShift, row.shift_id)
        if shift is not None and shift.restaurant_id == restaurant_id:
            shift.end_at = row.suggested_time
    db.flush()
    events.emit(db, restaurant_id, "adjustment.decided", "labor_adjustments", row.id, {"kind": row.kind, "decision": decision})
    return row


# --- the Today page ------------------------------------------------------------------------------------


def today(db: Session, restaurant_id: str, user, now: datetime | None = None) -> dict:
    day = reforecast(db, restaurant_id, now)
    db.commit()
    s = day.service
    wage_access = bool(getattr(user, "is_manager", False))
    roles = {r.key: r.name for r in labor.ensure_roles(db, restaurant_id)}
    db.commit()
    hours = s.hours(day.day)
    open_now = hours is not None and hours[0] <= s.slot(day.now) < hours[1] and day.now_ord < PER_DAY
    out = {"state": day.state if day.forecast else "setup", "today": day.day, "now": day.now, "primary": s.primary, "weeks_of_data": s.weeks,
           "setup": [{"key": "orders", "done": s.weeks >= 4, "count": s.weeks, "of": 4}, {"key": "standards", "done": bool(day.held), "count": len(day.held)},
                     {"key": "forecast", "done": bool(day.forecast), "count": len(day.forecast)}],
           "stale": bool(day.forecast and open_now and day.forecast_so_far > 0 and day.actual_so_far == 0), "open_now": open_now, "wage_access": wage_access,
           "pace": None, "so_far": None, "day": None, "people": [], "chart": None, "coverage": {}, "suggestion": None, "suggestions_on": getattr(day, "suggestions_on", False),
           "suggestions_why": getattr(day, "suggestions_why", "learning")}
    if not day.forecast:
        return out
    p = day.pace
    out["pace"] = {"elapsed_share": round(p.elapsed_share, 4), "pace": None if p.pace is None else round(p.pace, 4), "multiplier": round(p.multiplier, 4), "adjusted": p.adjusted}
    out["so_far"] = {"actual": day.actual_so_far, "expected": round(day.forecast_so_far, 2)}
    remaining = sum(q.p50 for q in day.revised.values())
    out["day"] = {"expected": round(day.forecast_day, 2), "revised": round(day.actual_so_far + remaining, 2), "net_so_far": round(day.net_so_far, 2)}
    # Covers so far against the forecast, cumulatively, with the revised line for the rest of the day.
    slots = sorted(set(day.forecast) | {i for i, v in enumerate(day.actual) if v}, key=day.ord)
    points, low, mid, high, actual, revised = [], 0.0, 0.0, 0.0, 0.0, day.actual_so_far
    for slot in slots:
        q = day.forecast.get(slot)
        low, mid, high = low + (q.p10 if q else 0), mid + (q.p50 if q else 0), high + (q.p90 if q else 0)
        done = day.ord(slot) < day.now_ord
        if done:
            actual += day.actual[slot]
        else:
            revised += day.revised[slot].p50 if slot in day.revised else 0
        points.append({"start": s.slot_start(day.day, slot), "low": round(low, 2), "expected": round(mid, 2), "high": round(high, 2), "actual": round(actual, 2) if done else None,
                       "revised": None if done else round(revised, 2)})
    out["chart"] = {"points": points, "now_index": sum(1 for slot in slots if day.ord(slot) < day.now_ord)}
    half_now = day.now_minutes // 30 * 30
    for role_key in sorted(day.held):
        needs = day.needs.get(role_key, {})
        need_now = needs.get(half_now, (0, 0))
        out["people"].append({"role_key": role_key, "name": roles.get(role_key, role_key), "on": day.heads_now(role_key), "needed": need_now[0], "needed_if_busy": need_now[1]})
        steps = []
        for minute in sorted(needs):
            hour, minutes = (minute // 60) % 24, minute % 60
            steps.append({"label": f"{hour % 12 or 12}:{minutes:02d}", "need": needs[minute][0], "need_if_busy": needs[minute][1], "scheduled": len(day.scheduled(role_key, minute))})
        out["coverage"][role_key] = steps
    open_ = db.query(LaborAdjustment).filter(LaborAdjustment.restaurant_id == restaurant_id, LaborAdjustment.business_date == day.day, LaborAdjustment.status == "proposed",
                                              # read as of an earlier moment, the page shows only what had been suggested by then
                                              LaborAdjustment.created_at <= day.now).order_by(LaborAdjustment.created_at).all()
    if open_ and wage_access:  # suggestions go to the manager only
        row = open_[0]
        out["suggestion"] = {"id": row.id, "kind": row.kind, "role_key": row.role_key, "role": roles.get(row.role_key, row.role_key), "suggested_time": row.suggested_time,
                             "expires_at": row.expires_at, "savings_estimate": None if row.savings_estimate is None else str(row.savings_estimate), "reason": row.reason}
    if wage_access and day.schedule is not None:
        rules = schedules.rules_for(db, restaurant_id)
        team = schedules.Team(db, s, day.day - timedelta(days=day.day.weekday()))
        by_person, by_role = schedules.rates(db, restaurant_id, team)
        cost = Decimal(0)
        for shift in day.shifts:
            rate = by_person.get(shift.person or "", by_role.get(shift.role))
            worked = max(0, min(day.now_minutes, shift.end) - shift.start)
            if shift.person and rate is not None and worked:
                cost += Decimal(worked) / 60 * rate
        del rules
        out["labor_so_far"] = {"cost": str(cost.quantize(Decimal("0.01"))), "net": str(round(day.net_so_far, 2)),
                               "pct": None if day.net_so_far <= 0 else str((cost / Decimal(str(day.net_so_far))).quantize(Decimal("0.000001")))}
    return out
