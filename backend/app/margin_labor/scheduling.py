"""Schedule generation (spec sections 8.4 and 8.5): a draft week from the people needed, in two stages with a
constraint solver (OR-Tools CP-SAT), and a validator that is separate from the solver.

Stage 1, shifts. Per role and business date, how many of each candidate shift to use. Candidates start on the
30-minute grid and run from `min_shift_hours` to `max_shift_hours` in 30-minute steps.
    minimize  sum over t of (10 * under(t) + 1 * over(t)) + 0.5 * number_of_shifts
Stage 2, people. Assign employees to the chosen shifts. Hard constraints: the employee holds the role, the shift is
inside their availability, no time off, no overlap, the daily and weekly limits, rest between shifts, days in a row,
minor rules, and locked shifts stay as they are. Soft: an unassigned shift (50, it becomes an open shift), hours
over the overtime thresholds (20 an hour), distance from desired weekly hours (2 an hour), and a shift that differs
from the same person's last week (1).

A hard constraint whose rule is unset is skipped, and the draft lists the unset rules. Labor rules are
configuration the owner confirms; this is not legal advice. Pay rate, age and every other personal attribute are
never a selection criterion: no rate is passed into this module at all.

Times are minutes from local midnight of the shift's business date, on a 30-minute grid.
"""

from __future__ import annotations

import time as clock
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from ortools.sat.python import cp_model

HALF = 30
DEFAULT_MIN_SHIFT_HOURS = 4.0
DEFAULT_MAX_SHIFT_HOURS = 8.0
TOTAL_SECONDS = 30.0
WEIGHTS = {"unassigned": 50, "overtime_hour": 20, "desired_hour": 2, "differs": 1, "under": 10, "over": 1, "shift": 0.5}
RULE_NAMES = ("weekly_overtime_hours", "daily_overtime_hours", "min_rest_hours", "min_shift_hours", "max_shift_hours", "max_hours_per_day", "max_consecutive_days",
              "break_after_hours", "break_minutes", "min_paid_hours", "advance_notice_days", "allow_on_call")


@dataclass(frozen=True)
class Rules:
    """The restaurant's labor rules. Each is None until the owner sets it, and an unset rule is not enforced."""

    weekly_overtime_hours: float | None = None
    daily_overtime_hours: float | None = None
    min_rest_hours: float | None = None
    min_shift_hours: float | None = None
    max_shift_hours: float | None = None
    max_hours_per_day: float | None = None
    max_consecutive_days: int | None = None
    break_after_hours: float | None = None
    break_minutes: int | None = None
    min_paid_hours: float | None = None
    advance_notice_days: int | None = None
    allow_on_call: bool | None = None
    #: {"max_hours_per_day": 8, "latest_end": "22:00"}
    minor_rules: dict = field(default_factory=dict)
    max_flex_shifts_per_day: int = 1

    def unset(self) -> list[str]:
        return [name for name in RULE_NAMES if getattr(self, name) is None]


@dataclass(frozen=True)
class Person:
    id: str
    roles: frozenset
    max_weekly_hours: float | None = None
    desired_weekly_hours: float | None = None
    is_minor: bool = False
    #: weekday (Monday = 0) -> [(start minute, end minute)]. Empty: available at any time.
    availability: dict = field(default_factory=dict)
    #: [(business date, start minute, end minute)] they are off.
    time_off: tuple = ()
    #: {(weekday, start minute, end minute, role)} they worked last week.
    last_week: frozenset = frozenset()


@dataclass(frozen=True)
class Shift:
    id: str
    role: str
    day: date
    start: int
    end: int
    person: str | None = None
    locked: bool = False
    kind: str = "regular"
    note: str = ""

    @property
    def minutes(self) -> int:
        return self.end - self.start

    def absolute(self) -> tuple[int, int]:
        """Start and end in minutes from an arbitrary shared origin, so shifts on different days compare."""
        base = self.day.toordinal() * 1440
        return base + self.start, base + self.end


# --- stage 1: shifts ------------------------------------------------------------------------------------


def choose_shifts(need: list[int], fixed: list[int] | None = None, min_hours: float | None = None, max_hours: float | None = None,
                  seconds: float = 1.0) -> list[tuple[int, int]]:
    """The shifts for one role on one day, as (start minute, end minute). `need` is people per half hour from local
    midnight; `fixed` is coverage already there from locked shifts."""
    fixed = fixed or [0] * len(need)
    slots = len(need)
    low = max(1, round((min_hours or DEFAULT_MIN_SHIFT_HOURS) * 2))
    high = max(low, round((max_hours or DEFAULT_MAX_SHIFT_HOURS) * 2))
    wanted = [t for t in range(slots) if need[t] > fixed[t]]
    if not wanted:
        return []
    first, last = wanted[0], wanted[-1]
    model = cp_model.CpModel()
    cap = max(need)
    count: dict[tuple[int, int], cp_model.IntVar] = {}
    for start in range(max(0, first - low + 1), last + 1):
        for length in range(low, high + 1):
            if start + length <= slots:
                count[(start, length)] = model.NewIntVar(0, cap, f"n_{start}_{length}")
    cost = []
    for t in range(slots):
        covering = [v for (s, length), v in count.items() if s <= t < s + length]
        if not covering and need[t] <= fixed[t]:
            continue
        coverage = sum(covering) + fixed[t]
        under = model.NewIntVar(0, max(0, need[t]), f"u_{t}")
        over = model.NewIntVar(0, cap * len(covering) + fixed[t], f"o_{t}")
        model.Add(under >= need[t] - coverage)
        model.Add(over >= coverage - need[t])
        cost += [2 * WEIGHTS["under"] * under, 2 * WEIGHTS["over"] * over]
    model.Minimize(sum(cost) + sum(count.values()))  # every weight doubled, so half a point a shift is a whole number
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 1
    if solver.Solve(model) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return []
    out = []
    for (start, length), var in sorted(count.items()):
        out += [(start * HALF, (start + length) * HALF)] * int(solver.Value(var))
    return out


def coverage_of(shifts: list[Shift], role: str, day: date, slots: int = 60) -> list[int]:
    out = [0] * slots
    for shift in shifts:
        if shift.role == role and shift.day == day:
            for t in range(max(0, shift.start // HALF), min(slots, shift.end // HALF)):
                out[t] += 1
    return out


def staff_to_range(shifts: list[Shift], role: str, day: date, need_p90: list[int], rules: Rules, new_id) -> list[Shift]:
    """Cover the gap up to "people needed if busy" with flexibility, not padding: a flex shift over the peak gap when
    on-call is allowed, else a note on the regular shift that ends nearest the peak that it may extend."""
    coverage = coverage_of(shifts, role, day, len(need_p90))
    gaps = [max(0, need_p90[t] - coverage[t]) for t in range(len(need_p90))]
    if not any(gaps):
        return shifts
    peak = max(range(len(gaps)), key=lambda t: (gaps[t], -t))
    first = peak
    while first > 0 and gaps[first - 1] > 0:
        first -= 1
    last = peak
    while last + 1 < len(gaps) and gaps[last + 1] > 0:
        last += 1
    if rules.allow_on_call:
        if rules.max_flex_shifts_per_day <= 0 or any(s.kind == "flex" and s.role == role and s.day == day for s in shifts):
            return shifts
        low = max(1, round((rules.min_shift_hours or DEFAULT_MIN_SHIFT_HOURS) * 2))
        high = max(low, round((rules.max_shift_hours or DEFAULT_MAX_SHIFT_HOURS) * 2))
        length = min(high, max(low, last - first + 1))
        start = max(0, min(first, len(gaps) - length))
        return shifts + [Shift(new_id(), role, day, start * HALF, (start + length) * HALF, kind="flex", note="On call for a busy service")]
    own = [s for s in shifts if s.role == role and s.day == day and s.kind == "regular"]
    if not own:
        return shifts
    nearest = min(own, key=lambda s: (abs(s.end - peak * HALF), s.start, s.id))
    return [replace(s, note="May extend if busy") if s.id == nearest.id and not s.note else s for s in shifts]


# --- what makes a person unable to take a shift --------------------------------------------------------------


def _clock(text: str) -> int:
    return int(text[:2]) * 60 + int(text[3:5])


def eligible(person: Person, shift: Shift, rules: Rules) -> str | None:
    """None when the person may take the shift on its own merits; else the hard constraint in the way."""
    if shift.role not in person.roles:
        return "does not hold the role"
    windows = person.availability.get(shift.day.weekday()) if person.availability else None
    if person.availability and not any(start <= shift.start and shift.end <= end for start, end in (windows or ())):
        return "is outside their availability"
    for day, start, end in person.time_off:
        if day == shift.day and start < shift.end and shift.start < end:
            return "has time off"
    if person.is_minor and rules.minor_rules:
        latest = rules.minor_rules.get("latest_end")
        if latest and shift.end > _clock(latest):
            return "ends after the latest time for a minor"
        most = rules.minor_rules.get("max_hours_per_day")
        if most is not None and shift.minutes > float(most) * 60:
            return "is longer than a minor may work in a day"
    if rules.max_hours_per_day is not None and shift.minutes > rules.max_hours_per_day * 60:
        return "is longer than the daily limit"
    return None


def conflict(a: Shift, b: Shift, rules: Rules) -> str | None:
    """Why one person cannot work both shifts: they overlap, or leave too little rest between them."""
    a0, a1 = a.absolute()
    b0, b1 = b.absolute()
    if a0 < b1 and b0 < a1:
        return "overlaps another shift"
    if rules.min_rest_hours is not None:
        gap = (b0 - a1) if b0 >= a1 else (a0 - b1)
        if gap < rules.min_rest_hours * 60:
            return "leaves too little rest between shifts"
    return None


# --- stage 2: people ----------------------------------------------------------------------------------------


def assign(shifts: list[Shift], people: list[Person], rules: Rules, seconds: float = 15.0) -> list[Shift]:
    """Put people on the shifts. Locked shifts stay exactly as they are; a shift nobody can take stays open."""
    model = cp_model.CpModel()
    locked = [s for s in shifts if s.locked]
    free = [s for s in shifts if not s.locked]
    by_id = {p.id: p for p in people}
    x: dict[tuple[str, str], cp_model.IntVar] = {}
    for shift in free:
        for person in people:
            if eligible(person, shift, rules) is not None:
                continue
            # A locked shift is fixed: a free one that clashes with the person's locked shift is not offered to them.
            if any(l.person == person.id and conflict(l, shift, rules) for l in locked):
                continue
            x[(person.id, shift.id)] = model.NewBoolVar(f"x_{person.id}_{shift.id}")
    cost = []
    for shift in free:
        takers = [x[(p.id, shift.id)] for p in people if (p.id, shift.id) in x]
        if takers:
            model.Add(sum(takers) <= 1)
            open_ = model.NewBoolVar(f"open_{shift.id}")
            model.Add(sum(takers) + open_ == 1)
            cost.append(WEIGHTS["unassigned"] * open_)
    days = sorted({s.day for s in shifts})
    halves = lambda s: s.minutes // HALF  # noqa: E731
    for person in people:
        mine = [s for s in free if (person.id, s.id) in x]
        fixed = [s for s in locked if s.person == person.id]
        if not mine:
            continue
        for i, a in enumerate(mine):
            for b in mine[i + 1:]:
                if conflict(a, b, rules):
                    model.Add(x[(person.id, a.id)] + x[(person.id, b.id)] <= 1)
        day_limit = rules.max_hours_per_day
        if person.is_minor and rules.minor_rules.get("max_hours_per_day") is not None:
            day_limit = min(day_limit, float(rules.minor_rules["max_hours_per_day"])) if day_limit is not None else float(rules.minor_rules["max_hours_per_day"])
        worked = {}
        for day in days:
            todays = [s for s in mine if s.day == day]
            fixed_today = sum(halves(s) for s in fixed if s.day == day)
            if day_limit is not None and todays:
                model.Add(sum(halves(s) * x[(person.id, s.id)] for s in todays) + fixed_today <= int(day_limit * 2))
            if rules.daily_overtime_hours is not None and todays:
                over = model.NewIntVar(0, 48, f"dot_{person.id}_{day.toordinal()}")
                model.Add(over >= sum(halves(s) * x[(person.id, s.id)] for s in todays) + fixed_today - int(rules.daily_overtime_hours * 2))
                cost.append(WEIGHTS["overtime_hour"] // 2 * over)
            if rules.max_consecutive_days is not None:
                on = model.NewBoolVar(f"on_{person.id}_{day.toordinal()}")
                if fixed_today:
                    model.Add(on == 1)
                elif todays:
                    model.AddMaxEquality(on, [x[(person.id, s.id)] for s in todays])
                else:
                    model.Add(on == 0)
                worked[day] = on
        if rules.max_consecutive_days is not None:
            run = rules.max_consecutive_days + 1
            ordered = [days[0] + timedelta(days=n) for n in range((days[-1] - days[0]).days + 1)] if days else []
            for i in range(len(ordered) - run + 1):
                window = [worked[d] for d in ordered[i:i + run] if d in worked]
                if len(window) == run:
                    model.Add(sum(window) <= rules.max_consecutive_days)
        total = sum(halves(s) * x[(person.id, s.id)] for s in mine) + sum(halves(s) for s in fixed)
        if person.max_weekly_hours is not None:
            model.Add(total <= int(person.max_weekly_hours * 2))
        if rules.weekly_overtime_hours is not None:
            over = model.NewIntVar(0, 336, f"wot_{person.id}")
            model.Add(over >= total - int(rules.weekly_overtime_hours * 2))
            cost.append(WEIGHTS["overtime_hour"] // 2 * over)
        if person.desired_weekly_hours is not None:
            away = model.NewIntVar(0, 336, f"dev_{person.id}")
            model.Add(away >= total - int(person.desired_weekly_hours * 2))
            model.Add(away >= int(person.desired_weekly_hours * 2) - total)
            cost.append(WEIGHTS["desired_hour"] // 2 * away)
        for s in mine:
            if (s.day.weekday(), s.start, s.end, s.role) not in person.last_week:
                cost.append(WEIGHTS["differs"] * x[(person.id, s.id)])
    model.Minimize(sum(cost))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 1
    status = solver.Solve(model)
    out = []
    for shift in shifts:
        if shift.locked:
            out.append(shift)
            continue
        taker = None
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            taker = next((p.id for p in people if (p.id, shift.id) in x and solver.Value(x[(p.id, shift.id)])), None)
        out.append(replace(shift, person=taker))
    del by_id
    return out


@dataclass(frozen=True)
class Draft:
    shifts: list
    unset_rules: list
    seconds: float


def generate(needs: dict[tuple[str, date], tuple[list[int], list[int]]], people: list[Person], rules: Rules, locked: list[Shift] | None = None,
             seconds: float = TOTAL_SECONDS, new_id=None) -> Draft:
    """A draft week. `needs` maps (role, business date) to people per half hour at expected volume and if busy.
    Locked shifts are kept and scheduled around. Returns the best feasible result found inside the time limit."""
    started = clock.monotonic()
    counter = iter(range(1, 10**9))
    new_id = new_id or (lambda: f"s{next(counter)}")
    locked = [replace(s, locked=True) for s in (locked or [])]
    shifts: list[Shift] = list(locked)
    per_day = max(0.2, min(1.0, seconds * 0.4 / max(1, len(needs))))
    for (role, day), (need_p50, need_p90) in sorted(needs.items()):
        fixed = coverage_of(locked, role, day, len(need_p50))
        for start, end in choose_shifts(need_p50, fixed, rules.min_shift_hours, rules.max_shift_hours, per_day):
            shifts.append(Shift(new_id(), role, day, start, end))
        shifts = staff_to_range(shifts, role, day, need_p90, rules, new_id)
    # Stage 2 gets what is left of a little over half the limit: the whole run stays well inside it on a slow machine.
    left = max(2.0, seconds * 0.55 - (clock.monotonic() - started))
    return Draft(assign(shifts, people, rules, left), rules.unset(), round(clock.monotonic() - started, 2))


# --- the validator: separate from the solver ------------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    shift_id: str
    person: str
    rule: str


def validate(shifts: list[Shift], people: list[Person], rules: Rules) -> list[Violation]:
    """Every hard constraint a schedule breaks, checked directly from the shifts with none of the solver's own
    working. An unset rule is not checked. An empty list means none is broken."""
    by_id = {p.id: p for p in people}
    out: list[Violation] = []
    mine: dict[str, list[Shift]] = {}
    for shift in shifts:
        if shift.person is None:
            continue
        person = by_id.get(shift.person)
        if person is None:
            out.append(Violation(shift.id, shift.person, "is not on the staff list"))
            continue
        why = eligible(person, shift, rules)
        if why:
            out.append(Violation(shift.id, person.id, why))
        mine.setdefault(person.id, []).append(shift)
    for person_id, held in mine.items():
        person = by_id[person_id]
        held.sort(key=lambda s: s.absolute())
        for i, a in enumerate(held):
            for b in held[i + 1:]:
                why = conflict(a, b, rules)
                if why:
                    out.append(Violation(b.id, person_id, why))
        per_day: dict[date, int] = {}
        for s in held:
            per_day[s.day] = per_day.get(s.day, 0) + s.minutes
        day_limit = rules.max_hours_per_day
        if person.is_minor and rules.minor_rules.get("max_hours_per_day") is not None:
            minor = float(rules.minor_rules["max_hours_per_day"])
            day_limit = minor if day_limit is None else min(day_limit, minor)
        if day_limit is not None:
            for day, minutes in per_day.items():
                if minutes > day_limit * 60:
                    out.append(Violation(next(s.id for s in held if s.day == day), person_id, "is over the daily hours limit"))
        if person.max_weekly_hours is not None and sum(per_day.values()) > person.max_weekly_hours * 60:
            out.append(Violation(held[-1].id, person_id, "is over their weekly hours limit"))
        if rules.max_consecutive_days is not None:
            run, previous = 0, None
            for day in sorted(per_day):
                run = run + 1 if previous is not None and (day - previous).days == 1 else 1
                previous = day
                if run > rules.max_consecutive_days:
                    out.append(Violation(next(s.id for s in held if s.day == day), person_id, "works too many days in a row"))
    return out


# --- hours and cost -------------------------------------------------------------------------------------------


def paid_minutes(shift: Shift, rules: Rules) -> int:
    """paid_hours = shift length - unpaid break minutes / 60. A break applies from `break_after_hours`."""
    if rules.break_after_hours is not None and rules.break_minutes and shift.minutes >= rules.break_after_hours * 60:
        return max(0, shift.minutes - rules.break_minutes)
    return shift.minutes


def break_minutes(shift: Shift, rules: Rules) -> int:
    return shift.minutes - paid_minutes(shift, rules)
