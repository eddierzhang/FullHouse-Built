"""Module D, the weekly schedule (spec sections 8.4 and 8.5): a draft generated from the people needed, edited and
locked by a manager, regenerated around the locks, and published by a person.

The draft is one `schedule_draft` proposal a week. Publishing sets its status and offers exports; FullHouse does
not notify staff and writes to no scheduler or payroll system. Hourly rates are read here for one thing, the cost
of the schedule, and only totals leave this module for anyone without wage access.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from statistics import median

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.agents.models import AgentAction
from app.margin_labor import labor, scheduling, words
from app.margin_labor.models import (EmployeeAvailability, EmployeeProfile, EmployeeTimeOff, EmployeeWage, ForecastInterval, LaborRequirement, LaborRules,
                                     Schedule, ScheduleShift)
from app.margin_labor.scheduling import Person, Rules, Shift
from app.platform import events
from app.restaurant.models import Staff

logger = logging.getLogger(__name__)

ACTION = "schedule_draft"
SLOTS = 60  # half hours from local midnight: a business day can run past midnight
RULE_FIELDS = scheduling.RULE_NAMES


def rules_for(db: Session, restaurant_id: str) -> Rules:
    """The confirmed rules. Until the owner confirms them, every rule is unset and none is enforced."""
    row = db.query(LaborRules).filter(LaborRules.restaurant_id == restaurant_id).first()
    if row is None or row.confirmed_at is None:
        return Rules()
    return Rules(**{name: getattr(row, name) for name in RULE_FIELDS}, minor_rules=dict(row.minor_rules or {}))


def _minutes(text: str) -> int:
    return int(text[:2]) * 60 + int(text[3:5])


class Team:
    """The people who can be scheduled, with what the solver may know about them. Rates are held apart."""

    def __init__(self, db: Session, service: labor.Service, week_of: date):
        rid = service.restaurant_id
        self.staff = {s.id: s for s in db.query(Staff).filter(Staff.restaurant_id == rid)}
        profiles = {p.staff_id: p for p in db.query(EmployeeProfile).filter(EmployeeProfile.restaurant_id == rid)}
        availability: dict[str, dict[int, list]] = {}
        for row in db.query(EmployeeAvailability).filter(EmployeeAvailability.restaurant_id == rid):
            availability.setdefault(row.staff_id, {}).setdefault(row.weekday, []).append((_minutes(row.start_time), _minutes(row.end_time)))
        off: dict[str, list] = {}
        for row in db.query(EmployeeTimeOff).filter(EmployeeTimeOff.restaurant_id == rid, EmployeeTimeOff.end_at >= service.slot_start(week_of, 16) - timedelta(days=1)):
            start, end = self._aware(row.start_at).astimezone(service.tz), self._aware(row.end_at).astimezone(service.tz)
            day = start.date()
            while day <= end.date():
                first = start.hour * 60 + start.minute if day == start.date() else 0
                last = end.hour * 60 + end.minute if day == end.date() else 1440
                if last > first:
                    off.setdefault(row.staff_id, []).append((day, first, last))
                day += timedelta(days=1)
        last_week: dict[str, set] = {}
        before = db.query(Schedule).filter(Schedule.restaurant_id == rid, Schedule.week_of == week_of - timedelta(days=7)).first()
        if before is not None:
            for shift in db.query(ScheduleShift).filter(ScheduleShift.schedule_id == before.id, ScheduleShift.staff_id.isnot(None)):
                start, end = local_minutes(service, shift)
                last_week.setdefault(shift.staff_id, set()).add((shift.business_date.weekday(), start, end, shift.role_key))
        self.people: list[Person] = []
        self.names: dict[str, str] = {}
        for staff in sorted(self.staff.values(), key=lambda s: (s.name, s.id)):
            profile = profiles.get(staff.id)
            if profile is not None and not profile.active:
                continue
            roles = frozenset(profile.role_keys) if profile is not None and profile.role_keys else frozenset({labor.slug(staff.role)})
            self.names[staff.id] = staff.name
            self.people.append(Person(staff.id, roles, profile.max_weekly_hours if profile else None, profile.desired_weekly_hours if profile else None,
                                      bool(profile.is_minor) if profile else False, availability.get(staff.id, {}), tuple(off.get(staff.id, ())),
                                      frozenset(last_week.get(staff.id, ()))))
        self.roles_of = {p.id: p.roles for p in self.people}

    @staticmethod
    def _aware(moment: datetime) -> datetime:
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def local_minutes(service: labor.Service, shift: ScheduleShift) -> tuple[int, int]:
    """A stored shift's start and end as minutes from local midnight of its business date."""
    midnight = datetime.combine(shift.business_date, time(0, 0), tzinfo=service.tz)
    aware = lambda m: m if m.tzinfo else m.replace(tzinfo=timezone.utc)  # noqa: E731
    return (round((aware(shift.start_at) - midnight).total_seconds() / 60), round((aware(shift.end_at) - midnight).total_seconds() / 60))


def to_moment(service: labor.Service, day: date, minutes: int) -> datetime:
    return (datetime.combine(day, time(0, 0), tzinfo=service.tz) + timedelta(minutes=minutes)).astimezone(timezone.utc)


def as_spec(service: labor.Service, shift: ScheduleShift) -> Shift:
    start, end = local_minutes(service, shift)
    return Shift(shift.id, shift.role_key, shift.business_date, start, end, shift.staff_id, shift.locked, shift.kind, shift.note)


def needs_for(db: Session, service: labor.Service, days: list[date]) -> dict[tuple[str, date], tuple[list[int], list[int]]]:
    """(role, business date) -> people per half hour from local midnight, at expected volume and if busy."""
    out: dict[tuple[str, date], tuple[list[int], list[int]]] = {}
    for row in db.query(LaborRequirement).filter(LaborRequirement.restaurant_id == service.restaurant_id, LaborRequirement.kind == labor.KIND,
                                                 LaborRequirement.business_date.in_(days)):
        midnight = datetime.combine(row.business_date, time(0, 0), tzinfo=service.tz)
        start = row.interval_start if row.interval_start.tzinfo else row.interval_start.replace(tzinfo=timezone.utc)
        half = round((start - midnight).total_seconds() / 1800)
        if 0 <= half < SLOTS:
            p50, p90 = out.setdefault((row.role_key, row.business_date), ([0] * SLOTS, [0] * SLOTS))
            p50[half], p90[half] = row.need_p50, row.need_p90
    return out


def week_days(week_of: date) -> list[date]:
    return [week_of + timedelta(days=n) for n in range(7)]


def generate(db: Session, restaurant_id: str, week_of: date, now: datetime | None = None, seconds: float = scheduling.TOTAL_SECONDS) -> Schedule:
    """Generate, or regenerate, the week's draft. Locked shifts stay exactly as they are and the rest is scheduled
    around them. One draft, and one `schedule_draft` proposal, per week."""
    week_of -= timedelta(days=week_of.weekday())
    labor.forecast_labor(db, restaurant_id, now)
    db.commit()
    # Read everything the solver needs, then let go of the database: the solve takes up to 30 seconds, and nothing
    # else should wait on it.
    service = labor.Service(db, restaurant_id, now)
    days = week_days(week_of)
    schedule = db.query(Schedule).filter(Schedule.restaurant_id == restaurant_id, Schedule.week_of == week_of).first()
    held = db.query(ScheduleShift).filter(ScheduleShift.schedule_id == schedule.id).all() if schedule is not None else []
    locked = [as_spec(service, s) for s in held if s.locked]
    team = Team(db, service, week_of)
    rules = rules_for(db, restaurant_id)
    needs = needs_for(db, service, days)
    db.commit()
    import uuid

    draft = scheduling.generate(needs, team.people, rules, locked, seconds, new_id=lambda: uuid.uuid4().hex)

    schedule = db.query(Schedule).filter(Schedule.restaurant_id == restaurant_id, Schedule.week_of == week_of).first()
    if schedule is None:
        schedule = Schedule(restaurant_id=restaurant_id, week_of=week_of, version=0)
        db.add(schedule)
        db.flush()
    kept = {s.id for s in locked}
    for shift in db.query(ScheduleShift).filter(ScheduleShift.schedule_id == schedule.id).all():
        if shift.id not in kept:
            db.delete(shift)  # everything that was not locked when the solve began is replaced
    db.flush()
    for spec in draft.shifts:
        if spec.id in kept:
            continue
        db.add(ScheduleShift(id=spec.id, restaurant_id=restaurant_id, schedule_id=schedule.id, staff_id=spec.person, role_key=spec.role, business_date=spec.day,
                             start_at=to_moment(service, spec.day, spec.start), end_at=to_moment(service, spec.day, spec.end), kind=spec.kind,
                             break_minutes=scheduling.break_minutes(spec, rules), locked=False, note=spec.note))
    schedule.status, schedule.version, schedule.generated_at = "draft", schedule.version + 1, datetime.now(timezone.utc)
    schedule.published_at = schedule.published_by = None
    schedule.notes = {"unset_rules": draft.unset_rules, "seconds": draft.seconds}
    db.flush()
    refresh_totals(db, service, schedule)
    _proposal(db, service, schedule)
    events.emit(db, restaurant_id, "schedule.generated", "schedules", schedule.id, {"week_of": week_of.isoformat(), "version": schedule.version, "shifts": len(draft.shifts)})
    return schedule


def _proposal(db: Session, service: labor.Service, schedule: Schedule) -> None:
    """One open `schedule_draft` proposal per week being scheduled; a regenerate updates it."""
    from app.agents.crud import get_definition_by_key
    from app.agents.definitions import AGENT_KEY
    from app.restaurant.po_draft import _system_run

    if get_definition_by_key(db, AGENT_KEY) is None:
        return  # a site with no agent set up yet: the draft is still on the Labor page, with no proposal beside it

    note = (f"Draft schedule for the week of {words.day(schedule.week_of)}, version {schedule.version}: {words.qty(schedule.hours_total)} hours. "
            "Review it on the Labor page. Publishing notifies nobody: you tell the team.")
    payload = {"module": "labor", "schedule_id": schedule.id, "week_of": schedule.week_of.isoformat(), "version": schedule.version,
               "hours_total": str(schedule.hours_total), "item_name": f"Week of {words.day(schedule.week_of)}", "note": note}
    action = db.get(AgentAction, schedule.proposal_id) if schedule.proposal_id else None
    if action is not None and action.status == "pending":
        action.payload = payload
        flag_modified(action, "payload")
        return
    run = _system_run(db, service.restaurant_id, f"Schedule drafted for the week of {schedule.week_of.isoformat()}")
    action = AgentAction(restaurant_id=service.restaurant_id, run_id=run.id, agent_definition_id=run.agent_definition_id, action_type=ACTION, payload=payload)
    db.add(action)
    db.flush()
    schedule.proposal_id = action.id


# --- hours, cost and what is still wrong -----------------------------------------------------------------------


def rates(db: Session, restaurant_id: str, team: Team) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    """(rate by person, median rate by role). Read only to cost a schedule."""
    by_person = {w.staff_id: w.hourly_rate for w in db.query(EmployeeWage).filter(EmployeeWage.restaurant_id == restaurant_id)}
    by_role: dict[str, list[Decimal]] = {}
    for person_id, rate in by_person.items():
        for role in team.roles_of.get(person_id, ()):
            by_role.setdefault(role, []).append(rate)
    return by_person, {role: Decimal(median(values)) for role, values in by_role.items()}


def summary(db: Session, service: labor.Service, schedule: Schedule) -> dict:
    """Everything the page shows about a schedule, computed from its shifts. Cost fields are for people with wage access."""
    team = Team(db, service, schedule.week_of)
    rules = rules_for(db, service.restaurant_id)
    shifts = db.query(ScheduleShift).filter(ScheduleShift.schedule_id == schedule.id).all()
    specs = [as_spec(service, s) for s in shifts]
    by_person, by_role = rates(db, service.restaurant_id, team)
    paid = {s.id: scheduling.paid_minutes(s, rules) for s in specs}
    hours = sum((Decimal(paid[s.id]) for s in specs if s.person), Decimal(0)) / 60
    cost, uncosted = Decimal(0), 0
    cost_by_day: dict[date, Decimal] = {}
    for s in specs:
        if not s.person:
            continue
        rate = by_person.get(s.person, by_role.get(s.role))  # the role's median when the person's rate is unknown
        if rate is None:
            uncosted += 1
        else:
            cost += Decimal(paid[s.id]) / 60 * rate
            cost_by_day[s.day] = cost_by_day.get(s.day, Decimal(0)) + Decimal(paid[s.id]) / 60 * rate
    days = week_days(schedule.week_of)
    net_rows = {r.business_date: r for r in db.query(ForecastInterval).filter(
        ForecastInterval.restaurant_id == service.restaurant_id, ForecastInterval.kind == labor.KIND, ForecastInterval.grain == "day", ForecastInterval.metric == "net",
        ForecastInterval.channel == "all", ForecastInterval.business_date.in_(days))}
    net = sum(r.p50 for r in net_rows.values())
    by_day = [{"date": d, "cost": str(cost_by_day.get(d, Decimal(0)).quantize(Decimal("0.01"))), "net_low": net_rows[d].p10 if d in net_rows else None,
               "net_expected": net_rows[d].p50 if d in net_rows else None, "net_high": net_rows[d].p90 if d in net_rows else None} for d in days]
    needs = needs_for(db, service, days)
    under = 0
    for (role, day), (p50, _p90) in needs.items():
        assigned = scheduling.coverage_of([s for s in specs if s.person], role, day, SLOTS)
        under += sum(1 for t in range(SLOTS) if assigned[t] < p50[t])
    per_person: dict[str, int] = {}
    for s in specs:
        if s.person:
            per_person[s.person] = per_person.get(s.person, 0) + paid[s.id]
    overtime = sorted(p for p, minutes in per_person.items() if rules.weekly_overtime_hours is not None and minutes > rules.weekly_overtime_hours * 60)
    violations = scheduling.validate(specs, team.people, rules)
    return {"hours_total": hours.quantize(Decimal("0.01")), "cost_total": cost.quantize(Decimal("0.01")) if specs and uncosted == 0 and by_person else None,
            "uncosted_shifts": uncosted, "net_expected": Decimal(str(round(net, 2))), "open_shifts": sum(1 for s in specs if not s.person),
            "understaffed_intervals": under, "overtime": overtime, "hours_by_person": {p: round(m / 60, 2) for p, m in per_person.items()},
            "cost_by_day": by_day, "violations": [{"shift_id": v.shift_id, "person": team.names.get(v.person, "Someone"), "rule": v.rule} for v in violations],
            "unset_rules": rules.unset(), "names": team.names, "roles_of": {p: sorted(r) for p, r in team.roles_of.items()}, "specs": specs, "rules": rules}


def refresh_totals(db: Session, service: labor.Service, schedule: Schedule) -> dict:
    out = summary(db, service, schedule)
    schedule.hours_total = out["hours_total"]
    schedule.cost_total = out["cost_total"]
    schedule.labor_pct_p50 = (out["cost_total"] / out["net_expected"]).quantize(Decimal("0.000001")) if out["cost_total"] is not None and out["net_expected"] > 0 else None
    db.flush()
    return out


# --- edits --------------------------------------------------------------------------------------------------


class ScheduleError(ValueError):
    pass


def _draft(db: Session, restaurant_id: str, schedule_id: str) -> Schedule:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None or schedule.restaurant_id != restaurant_id:
        raise LookupError("No such schedule.")
    return schedule


def _check_times(start: int, end: int) -> None:
    if start % 30 or end % 30 or not (0 <= start < end <= SLOTS * 30) or end - start > 16 * 60:
        raise ScheduleError("A shift starts and ends on the half hour, and ends after it starts.")


def save_shift(db: Session, restaurant_id: str, schedule_id: str, shift_id: str | None, *, day: date | None = None, start: int | None = None, end: int | None = None,
               role_key: str | None = None, staff_id: str | None = "keep", locked: bool | None = None, note: str | None = None, now: datetime | None = None) -> tuple[Schedule, ScheduleShift]:
    """Add a shift, or change one. An edited schedule goes back to draft: what was published is no longer what is here."""
    schedule = _draft(db, restaurant_id, schedule_id)
    service = labor.Service(db, restaurant_id, now)
    rules = rules_for(db, restaurant_id)
    if staff_id not in (None, "keep"):  # checked before anything is written
        staff = db.get(Staff, staff_id)
        if staff is None or staff.restaurant_id != restaurant_id:
            raise ScheduleError("That person is not on this restaurant's staff list.")
    if shift_id is None:
        if day is None or start is None or end is None or not role_key:
            raise ScheduleError("A new shift needs a day, a role, a start and an end.")
        if not (schedule.week_of <= day < schedule.week_of + timedelta(days=7)):
            raise ScheduleError("The day is outside this schedule's week.")
        _check_times(start, end)
        shift = ScheduleShift(restaurant_id=restaurant_id, schedule_id=schedule.id, role_key=role_key, business_date=day, start_at=to_moment(service, day, start),
                              end_at=to_moment(service, day, end), staff_id=None if staff_id == "keep" else staff_id, locked=bool(locked), note=(note or "")[:200])
        db.add(shift)
    else:
        shift = db.get(ScheduleShift, shift_id)
        if shift is None or shift.schedule_id != schedule.id:
            raise LookupError("No such shift.")
        old_start, old_end = local_minutes(service, shift)
        new_start, new_end = old_start if start is None else start, old_end if end is None else end
        _check_times(new_start, new_end)
        shift.start_at, shift.end_at = to_moment(service, shift.business_date, new_start), to_moment(service, shift.business_date, new_end)
        if role_key:
            shift.role_key = role_key
        if staff_id != "keep":
            shift.staff_id = staff_id
        if locked is not None:
            shift.locked = locked
        if note is not None:
            shift.note = note[:200]
    db.flush()
    shift.break_minutes = scheduling.break_minutes(as_spec(service, shift), rules)
    schedule.status, schedule.published_at, schedule.published_by = "draft", None, None
    db.flush()
    refresh_totals(db, service, schedule)
    return schedule, shift


def delete_shift(db: Session, restaurant_id: str, schedule_id: str, shift_id: str, now: datetime | None = None) -> Schedule:
    schedule = _draft(db, restaurant_id, schedule_id)
    shift = db.get(ScheduleShift, shift_id)
    if shift is None or shift.schedule_id != schedule.id:
        raise LookupError("No such shift.")
    db.delete(shift)
    schedule.status, schedule.published_at, schedule.published_by = "draft", None, None
    db.flush()
    refresh_totals(db, labor.Service(db, restaurant_id, now), schedule)
    return schedule


# --- publish --------------------------------------------------------------------------------------------------


def publish(db: Session, restaurant_id: str, schedule_id: str, who: str, confirm: bool = False, now: datetime | None = None) -> dict:
    """Publish the schedule. Anything a manager should see first comes back as a warning that needs `confirm`:
    late notice, open shifts, a hard constraint a hand edit broke. Publishing notifies nobody."""
    schedule = _draft(db, restaurant_id, schedule_id)
    service = labor.Service(db, restaurant_id, now)
    out = refresh_totals(db, service, schedule)
    rules: Rules = out["rules"]
    warnings = []
    if rules.advance_notice_days is not None and (schedule.week_of - service.today).days < rules.advance_notice_days:
        warnings.append({"kind": "notice", "days": (schedule.week_of - service.today).days, "rule": rules.advance_notice_days})
    if out["open_shifts"]:
        warnings.append({"kind": "open_shifts", "count": out["open_shifts"]})
    for violation in out["violations"]:
        warnings.append({"kind": "rule", **violation})
    if warnings and not confirm:
        return {"published": False, "warnings": warnings}
    schedule.status, schedule.published_at, schedule.published_by = "published", datetime.now(timezone.utc), who
    action = db.get(AgentAction, schedule.proposal_id) if schedule.proposal_id else None
    if action is not None and action.status == "pending":
        action.status, action.decided_at = "approved", datetime.now(timezone.utc)
        action.applied_result = f"Published the schedule for the week of {words.day(schedule.week_of)}. Nobody was notified: tell the team, or export it."
    db.flush()
    events.emit(db, restaurant_id, "schedule.published", "schedules", schedule.id, {"week_of": schedule.week_of.isoformat(), "version": schedule.version, "warnings": len(warnings)})
    return {"published": True, "warnings": warnings}


def export_csv(db: Session, restaurant_id: str, schedule_id: str) -> str:
    """The schedule as CSV: who works when. No wage or cost is in it."""
    import csv
    import io

    schedule = _draft(db, restaurant_id, schedule_id)
    service = labor.Service(db, restaurant_id)
    names = {s.id: s.name for s in db.query(Staff).filter(Staff.restaurant_id == restaurant_id)}
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Date", "Day", "Person", "Role", "Start", "End", "Break minutes", "Kind", "Note"])
    label = lambda m: f"{(m // 60) % 24:02d}:{m % 60:02d}"  # noqa: E731
    for shift in sorted(db.query(ScheduleShift).filter(ScheduleShift.schedule_id == schedule.id), key=lambda s: (s.business_date, s.start_at, s.role_key, s.id)):
        start, end = local_minutes(service, shift)
        writer.writerow([shift.business_date.isoformat(), shift.business_date.strftime("%a"), names.get(shift.staff_id or "", "Open shift"), shift.role_key, label(start), label(end),
                         shift.break_minutes, shift.kind, shift.note])
    return out.getvalue()
