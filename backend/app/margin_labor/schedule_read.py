"""The schedule's part of the Labor week page: the grid's rows, the totals, and what still needs a decision.
Cost appears only for people with wage access (owners and managers), and only as a total."""

from __future__ import annotations

from sqlalchemy.orm import Session

from decimal import Decimal

from app.margin_labor import labor, scheduling
from app.margin_labor import schedule as schedules
from app.margin_labor.models import Schedule


def out(db: Session, restaurant_id: str, schedule: Schedule, user) -> dict:
    service = labor.Service(db, restaurant_id)
    s = schedules.summary(db, service, schedule)
    wage_access = bool(getattr(user, "is_manager", False))
    shifts = [{"id": spec.id, "staff_id": spec.person, "role_key": spec.role, "date": spec.day, "start": spec.start, "end": spec.end, "kind": spec.kind, "locked": spec.locked,
               "note": spec.note, "paid_minutes": scheduling.paid_minutes(spec, s["rules"])} for spec in s["specs"]]
    shifts.sort(key=lambda x: (x["date"], x["start"], x["role_key"], x["id"]))
    body = {"id": schedule.id, "week_of": schedule.week_of, "status": schedule.status, "version": schedule.version, "generated_at": schedule.generated_at,
            "published_at": schedule.published_at, "proposal_id": schedule.proposal_id, "hours_total": str(s["hours_total"]), "open_shifts": s["open_shifts"],
            "understaffed_intervals": s["understaffed_intervals"], "overtime": s["overtime"], "hours_by_person": s["hours_by_person"], "violations": s["violations"],
            "unset_rules": s["unset_rules"], "people": [{"id": p, "name": n, "roles": s["roles_of"].get(p, [])} for p, n in sorted(s["names"].items(), key=lambda kv: kv[1])],
            "shifts": shifts, "wage_access": wage_access}
    if wage_access:
        cost = s["cost_total"]
        body.update(cost_total=None if cost is None else str(cost), net_expected=str(s["net_expected"]), uncosted_shifts=s["uncosted_shifts"], cost_by_day=s["cost_by_day"],
                    labor_pct=None if cost is None or s["net_expected"] <= 0 else str((cost / s["net_expected"]).quantize(Decimal("0.000001"))))
    return body


def attach(db: Session, restaurant_id: str, week: dict, user) -> dict:
    """Add the week's schedule to the week page, and the scheduled heads to each half hour of the day view."""
    schedule = db.query(Schedule).filter(Schedule.restaurant_id == restaurant_id, Schedule.week_of == week["week_of"]).first()
    week["schedule"] = None
    week["wage_access"] = bool(getattr(user, "is_manager", False))
    if schedule is None or week["state"] == "setup":
        return week
    body = out(db, restaurant_id, schedule, user)
    week["schedule"] = body
    service = labor.Service(db, restaurant_id)
    for shift in body["shifts"]:
        if shift["staff_id"] is None:
            continue
        steps = week["intervals"].get(shift["date"].isoformat(), {}).get(shift["role_key"], [])
        for step in steps:
            local = step["start"].astimezone(service.tz)
            minutes = (local.date() - shift["date"]).days * 1440 + local.hour * 60 + local.minute
            if shift["start"] <= minutes < shift["end"]:
                step["scheduled"] += 1
    return week
