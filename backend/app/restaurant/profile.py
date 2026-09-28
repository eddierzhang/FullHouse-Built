"""The restaurant profile, and everywhere the rest of the app reads it.

Setup fills it in one step at a time; the Profile page edits the same
fields afterwards. The rest of the app consults it here rather than
reading the row itself, so a deployment that never ran setup (tests, an
old database) keeps its previous behaviour: UTC, no scheduling rules, no
restricted roles.
"""

import uuid
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.models import User
from app.restaurant.models import Restaurant, Shift
from app.restaurant.profile_schemas import (
    DAY_NAMES,
    SECTIONS,
    WEEKDAYS,
    DayHours,
    Editor,
    InventoryIn,
    RestaurantDetailsIn,
    RestaurantOut,
    RestaurantView,
    SchedulingIn,
)


class SetupIncomplete(ValueError):
    pass


class EditConflict(ValueError):
    """Someone else saved the profile after this editor loaded it."""


def get_restaurant(db: Session) -> Restaurant | None:
    return db.query(Restaurant).order_by(Restaurant.created_at).first()


def restaurant_for(db: Session, user: User) -> Restaurant | None:
    """The restaurant `user` belongs to -- never another one.

    An account not linked to one yet (an owner before the first setup step,
    or one from before restaurants existed) gets the site's restaurant,
    which saving links them to.
    """
    if user.restaurant_id:
        return db.get(Restaurant, user.restaurant_id)
    return get_restaurant(db)


def view(db: Session, restaurant: Restaurant, viewer: User) -> RestaurantView:
    """The restaurant as `viewer` may see it: who last edited it is for owners and admins."""
    editor = None
    if viewer.is_manager and restaurant.updated_by:
        account = db.get(User, restaurant.updated_by)
        if account is not None:
            editor = Editor(id=account.id, name=account.name, email=account.email)
    return RestaurantView(
        **RestaurantOut.model_validate(restaurant).model_dump(), updated_by=editor, can_edit=viewer.is_manager
    )


def _assign(restaurant: Restaurant, values: dict) -> bool:
    """Set each field that differs. True when anything did."""
    changed = False
    for field, value in values.items():
        if getattr(restaurant, field) != value:
            setattr(restaurant, field, value)
            changed = True
    return changed


def _stamp(restaurant: Restaurant, user: User) -> None:
    restaurant.updated_at = datetime.now(timezone.utc)
    restaurant.updated_by = user.id


def _configured(db: Session) -> Restaurant | None:
    """The restaurant, only once setup is finished -- a draft changes nothing yet."""
    restaurant = get_restaurant(db)
    return restaurant if restaurant is not None and restaurant.setup_completed_at else None


# --- saving -------------------------------------------------------------------


def save_section(db: Session, user: User, section: str, data: BaseModel) -> Restaurant:
    """Save one step. Creates the restaurant on the first save and links the owner to it."""
    if section not in SECTIONS:
        raise ValueError(f"Unknown section {section!r}")
    restaurant = restaurant_for(db, user)
    created = restaurant is None
    if created:
        # Id assigned up front, not at flush, so the row is written once with
        # this step's values and the history shows one insert, not insert+update.
        restaurant = Restaurant(id=uuid.uuid4().hex, completed_steps=[])
        db.add(restaurant)

    values = data.model_dump()
    if section == "details":
        changed = _assign(restaurant, values)
    elif section == "hours":
        changed = _assign(restaurant, {"timezone": values["timezone"], "opening_hours": values["opening_hours"]})
    elif section == "inventory":
        changed = _assign(restaurant, {"inventory_settings": values})
    elif section == "staff":
        changed = _assign(restaurant, {"staff_roles": values["staff_roles"]})
    else:
        changed = _assign(restaurant, {"scheduling_rules": values})
    if changed or created:
        _stamp(restaurant, user)

    if section not in restaurant.completed_steps:
        # A new list, not an in-place append: the JSON column only notices reassignment.
        restaurant.completed_steps = [s for s in SECTIONS if s in {*restaurant.completed_steps, section}]
    if created:
        db.flush()  # after the values are set, so the history shows a single insert
        # Sample stock seeded before setup becomes this restaurant's.
        from app.restaurant.inventory import claim_unassigned

        claim_unassigned(db, restaurant)
    if user.restaurant_id != restaurant.id:
        db.flush()  # the restaurant row must exist before the user can point at it
        user.restaurant_id = restaurant.id
    db.commit()
    db.refresh(restaurant)

    if section in ("hours", "inventory") and restaurant.setup_completed_at:
        # Agent schedules run on the restaurant's clock, and the daily stock
        # check is a job built from the inventory settings.
        _resync_scheduler()
    return restaurant


def complete_setup(db: Session, user: User) -> Restaurant:
    restaurant = restaurant_for(db, user)
    missing = [s for s in SECTIONS if restaurant is None or s not in restaurant.completed_steps]
    if missing:
        raise SetupIncomplete(f"Finish these steps first: {', '.join(missing)}.")
    if restaurant.setup_completed_at is None:
        restaurant.setup_completed_at = datetime.now(timezone.utc)
        _stamp(restaurant, user)
        user.restaurant_id = restaurant.id
        _schedule_agents(db, restaurant)
        db.commit()
        db.refresh(restaurant)
        _resync_scheduler()
    return restaurant


def save_restaurant_details(db: Session, user: User, body: RestaurantDetailsIn) -> Restaurant:
    """The Restaurant Details tab: details, timezone and hours in one transaction.

    Only for a restaurant that exists -- setup is what creates one. Saving
    nothing new leaves `updated_at` alone, so it always marks a real change.
    """
    restaurant = restaurant_for(db, user)
    if restaurant is None:
        raise LookupError("Set up the restaurant first.")
    if body.expected_updated_at is not None and body.expected_updated_at != restaurant.updated_at:
        raise EditConflict(
            "Someone else saved these details after you opened them. Reload to see their changes, then try again."
        )

    values = body.model_dump(exclude={"expected_updated_at"})
    hours_before = (restaurant.timezone, restaurant.opening_hours)
    if _assign(restaurant, values):
        _stamp(restaurant, user)
        for section in ("details", "hours"):
            if section not in restaurant.completed_steps:
                restaurant.completed_steps = [s for s in SECTIONS if s in {*restaurant.completed_steps, section}]
    db.commit()
    db.refresh(restaurant)

    if restaurant.setup_completed_at and (restaurant.timezone, restaurant.opening_hours) != hours_before:
        _resync_scheduler()  # agent schedules run on the restaurant's clock
    return restaurant


def _schedule_agents(db: Session, restaurant: Restaurant) -> None:
    """Give the agent a routine that fits the restaurant's week.

    The Assistant Manager reviews the day an hour before the usual opening time, on open
    days. (The daily stock check isn't set here: the scheduler reads it from
    the inventory settings each time it syncs, so edits apply straight away.)
    Day names, not numbers, in the cron: the scheduler numbers weekdays from
    Monday, unlike crontab, and names mean the same to both.
    """
    from app.agents.definitions import AGENT_KEY
    from app.agents.models import AgentDefinition

    agent = db.query(AgentDefinition).filter(AgentDefinition.key == AGENT_KEY).first()
    hours = restaurant.opening_hours or {}
    open_days = [day for day in WEEKDAYS if not hours.get(day, {}).get("closed", True)]

    if agent is not None and open_days:
        usual_open = Counter(hours[day]["open"] for day in open_days).most_common(1)[0][0]
        opens = time.fromisoformat(usual_open)
        review = (datetime.combine(date.min, opens) - timedelta(hours=1)).time() if opens.hour >= 1 else opens
        agent.schedule_cron = f"{review.minute} {review.hour} * * {','.join(open_days)}"


def _resync_scheduler() -> None:
    from app.agents import scheduler

    scheduler.sync_agent_schedules()


# --- reading, for the rest of the app ------------------------------------------


def restaurant_timezone(db: Session) -> ZoneInfo | timezone:
    restaurant = _configured(db)
    return ZoneInfo(restaurant.timezone) if restaurant else timezone.utc


def staff_roles(db: Session) -> list[str] | None:
    restaurant = _configured(db)
    return list(restaurant.staff_roles) if restaurant and restaurant.staff_roles else None


def role_problem(db: Session, role: str) -> str | None:
    """Why `role` can't be used, when the owner has set the list of roles."""
    roles = staff_roles(db)
    if roles is None or role.strip().lower() in {r.lower() for r in roles}:
        return None
    return f"{role!r} isn't one of this restaurant's roles: {', '.join(roles)}. Add it on the Profile page, under Operating rules."


def inventory_settings(db: Session) -> InventoryIn | None:
    restaurant = _configured(db)
    return InventoryIn(**restaurant.inventory_settings) if restaurant and restaurant.inventory_settings else None


def scheduling_rules(db: Session) -> SchedulingIn | None:
    restaurant = _configured(db)
    return SchedulingIn(**restaurant.scheduling_rules) if restaurant and restaurant.scheduling_rules else None


def _hours(start: time, end: time) -> float:
    return (datetime.combine(date.min, end) - datetime.combine(date.min, start)).total_seconds() / 3600


def _fmt_hours(value: float) -> str:
    return f"{value:g}"


def shift_problems(
    db: Session, staff_id: str, day: date, start: time, end: time, exclude_shift_id: int | None = None
) -> list[str]:
    """Every scheduling rule this shift would break. Empty when there are no rules."""
    rules = scheduling_rules(db)
    if rules is None or end <= start:
        return []  # the "ends before it starts" check lives with the callers
    problems = []

    length = _hours(start, end)
    if length > rules.max_shift_hours:
        problems.append(f"The shift is {_fmt_hours(length)} hours; the limit is {_fmt_hours(rules.max_shift_hours)}.")

    week_start = day - timedelta(days=day.weekday())
    query = db.query(Shift).filter(
        Shift.staff_id == staff_id, Shift.date >= week_start - timedelta(days=1), Shift.date <= week_start + timedelta(days=7)
    )
    others = [s for s in query if s.id != exclude_shift_id]

    weekly = length + sum(
        _hours(s.start_time, s.end_time) for s in others if week_start <= s.date < week_start + timedelta(days=7)
    )
    if weekly > rules.max_weekly_hours:
        problems.append(
            f"That brings their week to {_fmt_hours(weekly)} hours; the limit is {_fmt_hours(rules.max_weekly_hours)}."
        )

    begins, ends = datetime.combine(day, start), datetime.combine(day, end)
    for other in others:
        other_begins = datetime.combine(other.date, other.start_time)
        other_ends = datetime.combine(other.date, other.end_time)
        if begins < other_ends and other_begins < ends:
            problems.append(f"It overlaps their shift on {other.date} {other.start_time:%H:%M}-{other.end_time:%H:%M}.")
            continue
        gap = (begins - other_ends) if other_ends <= begins else (other_begins - ends)
        rest = gap.total_seconds() / 3600
        if rest < rules.min_rest_hours:
            problems.append(
                f"Only {_fmt_hours(round(rest, 1))} hours' rest next to their shift on {other.date}; "
                f"the minimum is {_fmt_hours(rules.min_rest_hours)}."
            )
    return problems


def is_open_now(restaurant: Restaurant, now: datetime | None = None) -> bool:
    tz = ZoneInfo(restaurant.timezone)
    local = (now or datetime.now(timezone.utc)).astimezone(tz)
    hours = restaurant.opening_hours or {}
    # Today's hours, and yesterday's in case they ran past midnight.
    for offset in (0, -1):
        day = local.date() + timedelta(days=offset)
        entry = hours.get(WEEKDAYS[day.weekday()])
        if not entry or entry.get("closed"):
            continue
        opens = datetime.combine(day, time.fromisoformat(entry["open"]), tz)
        closes = datetime.combine(day, time.fromisoformat(entry["close"]), tz)
        if closes <= opens:
            closes += timedelta(days=1)
        if opens <= local < closes:
            return True
    return False


def agent_context(db: Session, now: datetime | None = None) -> str | None:
    """What every agent is told about the restaurant, appended to its instructions."""
    restaurant = _configured(db)
    if restaurant is None:
        return None
    tz = ZoneInfo(restaurant.timezone)
    local = (now or datetime.now(timezone.utc)).astimezone(tz)

    address = ", ".join(p for p in (restaurant.street, restaurant.city, restaurant.region,
                                    restaurant.postal_code, restaurant.country) if p)
    lines = [
        "About the restaurant you work for (set by the owner -- treat it as fact):",
        f"- Name: {restaurant.name}",
        f"- Address: {address}",
        f"- Local time now: {local:%A %Y-%m-%d %H:%M} ({restaurant.timezone}); "
        f"{'open' if is_open_now(restaurant, now) else 'closed'} right now.",
    ]
    hours = restaurant.opening_hours or {}
    week = []
    for day in WEEKDAYS:
        entry = DayHours(**hours[day]) if day in hours else DayHours(closed=True)
        week.append(f"{DAY_NAMES[day]} {'closed' if entry.closed else f'{entry.open}-{entry.close}'}")
    lines.append(f"- Opening hours: {'; '.join(week)}.")
    if restaurant.staff_roles:
        lines.append(f"- Staff roles: {', '.join(restaurant.staff_roles)}. Only schedule people into these roles.")
    if restaurant.scheduling_rules:
        r = SchedulingIn(**restaurant.scheduling_rules)
        lines.append(
            f"- Scheduling rules: shifts at most {_fmt_hours(r.max_shift_hours)} hours; at least "
            f"{_fmt_hours(r.min_rest_hours)} hours' rest between one person's shifts; at most "
            f"{_fmt_hours(r.max_weekly_hours)} hours per person per week; at least "
            f"{r.min_staff_per_open_day} people scheduled on every open day. Proposals that break "
            f"these are refused."
        )
    if restaurant.inventory_settings:
        inv = InventoryIn(**restaurant.inventory_settings)
        lines.append(
            f"- Inventory: keep at least {inv.safety_days} day(s) of stock beyond each supplier's "
            f"lead time; an item with less is at risk."
        )
    return "\n".join(lines)
