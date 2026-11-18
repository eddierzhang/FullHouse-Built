"""Schedule generation (spec sections 8.4, 8.5 and 12): the two solver stages, staffing to a range, and the
validator that is separate from the solver. No database."""

import random
import time
from datetime import date, timedelta

from app.margin_labor import scheduling
from app.margin_labor.scheduling import Person, Rules, Shift

MONDAY = date(2026, 11, 23)
RULES = Rules(weekly_overtime_hours=40, daily_overtime_hours=8, min_rest_hours=10, min_shift_hours=4, max_shift_hours=8, max_hours_per_day=10, max_consecutive_days=6,
              break_after_hours=6, break_minutes=30, min_paid_hours=3, advance_notice_days=7, allow_on_call=False)


def _need(*spans, slots=48):
    """People per half hour: (first hour, last hour, people)."""
    out = [0] * slots
    for first, last, people in spans:
        for t in range(int(first * 2), int(last * 2)):
            out[t] = people
    return out


def test_stage_one_covers_the_people_needed_with_as_few_shifts_as_fit():
    need = _need((11, 15, 2), (17, 21, 3))
    shifts = scheduling.choose_shifts(need, min_hours=4, max_hours=8)
    coverage = [sum(1 for s, e in shifts if s <= t * 30 < e) for t in range(48)]
    assert all(coverage[t] >= need[t] for t in range(48))  # being under costs ten times being over
    assert all(240 <= e - s <= 480 and s % 30 == 0 and e % 30 == 0 for s, e in shifts)
    assert len(shifts) == 5 and sum(coverage) == sum(need)  # two for lunch, three for dinner, nobody standing around
    assert scheduling.choose_shifts([0] * 48) == []
    # A two-hour need is shorter than the shortest shift: one four-hour shift covers it, over by two hours.
    short = scheduling.choose_shifts(_need((12, 14, 1)), min_hours=4, max_hours=8)
    assert len(short) == 1 and short[0][1] - short[0][0] == 240 and short[0][0] <= 720 and short[0][1] >= 840
    # A locked shift already covers lunch: nothing more is added there.
    assert scheduling.choose_shifts(_need((11, 15, 1)), fixed=_need((11, 15, 1))) == []
    # With no rule set, shifts run 4 to 8 hours.
    assert all(240 <= e - s <= 480 for s, e in scheduling.choose_shifts(need))


def test_stage_two_respects_roles_availability_time_off_rest_and_limits():
    dinner = [Shift(f"d{n}", "server", MONDAY + timedelta(days=n), 17 * 60, 22 * 60) for n in range(7)]
    lunch = Shift("l1", "server", MONDAY + timedelta(days=1), 7 * 60, 12 * 60)  # nine hours after Monday's dinner ends
    cook = Shift("c0", "line_cook", MONDAY, 16 * 60, 22 * 60)
    ana = Person("ana", frozenset({"server"}), max_weekly_hours=40, desired_weekly_hours=30)
    ben = Person("ben", frozenset({"server"}), availability={d: [(16 * 60, 23 * 60)] for d in (0, 1, 2)}, time_off=((MONDAY + timedelta(days=2), 0, 1440),))
    cal = Person("cal", frozenset({"line_cook"}))
    out = {s.id: s for s in scheduling.assign(dinner + [lunch, cook], [ana, ben, cal], RULES)}
    assert out["c0"].person == "cal"  # the only cook
    assert all(s.person in ("ana", "ben", None) for s in out.values() if s.role == "server")
    assert out["d2"].person != "ben" and all(out[f"d{n}"].person != "ben" for n in range(3, 7))  # time off Wednesday; not available Thursday on
    assert out["l1"].person != out["d0"].person or out["d0"].person is None  # ten hours of rest between Monday's close and Tuesday's open
    assert sum(1 for n in range(7) if out[f"d{n}"].person == "ana") <= 6  # at most six days in a row
    assert scheduling.validate(list(out.values()), [ana, ben, cal], RULES) == []
    # A shift nobody can take stays open rather than breaking a rule.
    alone = scheduling.assign([Shift("x", "bartender", MONDAY, 600, 900)], [ana], RULES)
    assert alone[0].person is None


def test_locked_shifts_stay_and_the_rest_is_scheduled_around_them():
    ana = Person("ana", frozenset({"server"}))
    ben = Person("ben", frozenset({"server"}))
    locked = Shift("keep", "server", MONDAY, 17 * 60, 22 * 60, person="ben", locked=True)
    need = {("server", MONDAY): (_need((17, 22, 2)), _need((17, 22, 2)))}
    draft = scheduling.generate(need, [ana, ben], RULES, locked=[locked])
    kept = next(s for s in draft.shifts if s.id == "keep")
    assert kept == locked and len(draft.shifts) == 2  # one more shift, not two: the locked one counts toward the need
    other = next(s for s in draft.shifts if s.id != "keep")
    assert other.person == "ana" and (other.start, other.end) == (17 * 60, 22 * 60)
    assert draft.unset_rules == [] and scheduling.validate(draft.shifts, [ana, ben], RULES) == []


def test_soft_weights_prefer_desired_hours_last_weeks_shift_and_no_overtime():
    shifts = [Shift(f"s{n}", "server", MONDAY + timedelta(days=n), 600, 1080) for n in range(5)]  # five eight-hour days
    same = frozenset((n, 600, 1080, "server") for n in range(5))
    regular = Person("regular", frozenset({"server"}), desired_weekly_hours=40, last_week=same)
    casual = Person("casual", frozenset({"server"}), desired_weekly_hours=0)
    out = scheduling.assign(shifts, [casual, regular], RULES)
    assert [s.person for s in out] == ["regular"] * 5
    # A sixth day would be overtime for the regular: it goes to the other person.
    more = scheduling.assign(shifts + [Shift("s5", "server", MONDAY + timedelta(days=5), 600, 1080)], [casual, regular], RULES)
    assert sorted(s.person for s in more).count("regular") == 5 and sorted(s.person for s in more).count("casual") == 1


def test_staffing_to_a_range_adds_flex_only_when_on_call_is_allowed():
    ids = iter(range(100))
    base = [Shift("a", "server", MONDAY, 17 * 60, 22 * 60), Shift("b", "server", MONDAY, 11 * 60, 15 * 60)]
    busy = _need((11, 15, 1), (17, 22, 1), (19, 21, 2))  # one more needed 7 to 9 if busy
    noted = scheduling.staff_to_range(base, "server", MONDAY, busy, RULES, lambda: f"n{next(ids)}")
    assert len(noted) == 2 and next(s for s in noted if s.id == "a").note == "May extend if busy" and next(s for s in noted if s.id == "b").note == ""
    on_call = scheduling.staff_to_range(base, "server", MONDAY, busy, Rules(**{**RULES.__dict__, "allow_on_call": True}), lambda: f"n{next(ids)}")
    flex = [s for s in on_call if s.kind == "flex"]
    assert len(flex) == 1 and flex[0].start <= 19 * 60 and flex[0].end >= 21 * 60 and flex[0].minutes == 240 and flex[0].note == "On call for a busy service"
    assert scheduling.staff_to_range(on_call, "server", MONDAY, busy, Rules(**{**RULES.__dict__, "allow_on_call": True}), lambda: "z") == on_call  # one flex shift a day
    assert scheduling.staff_to_range(base, "server", MONDAY, _need((17, 22, 1), (11, 15, 1)), RULES, lambda: "z") == base  # no gap: nothing added


def test_the_validator_names_each_hard_constraint_that_is_broken():
    minor = Person("kid", frozenset({"host"}), is_minor=True)
    ana = Person("ana", frozenset({"server"}), max_weekly_hours=10, availability={0: [(600, 900)], 1: [(0, 1440)]}, time_off=((MONDAY + timedelta(days=1), 0, 1440),))
    rules = Rules(**{**RULES.__dict__, "minor_rules": {"max_hours_per_day": 6, "latest_end": "21:00"}, "max_consecutive_days": 1})
    broken = [
        Shift("role", "bartender", MONDAY, 600, 840, person="ana"),
        Shift("avail", "server", MONDAY, 900, 1200, person="ana"),
        Shift("off", "server", MONDAY + timedelta(days=1), 600, 900, person="ana"),
        Shift("late", "host", MONDAY, 17 * 60, 22 * 60, person="kid"),
        Shift("long", "host", MONDAY + timedelta(days=2), 600, 600 + 7 * 60, person="kid"),
        Shift("rest", "host", MONDAY + timedelta(days=3), 0, 300, person="kid"),
        Shift("ghost", "host", MONDAY, 600, 900, person="nobody"),
    ]
    found = {(v.shift_id, v.rule) for v in scheduling.validate(broken, [minor, ana], rules)}
    assert ("role", "does not hold the role") in found and ("avail", "is outside their availability") in found and ("off", "has time off") in found
    assert ("late", "ends after the latest time for a minor") in found and ("long", "is longer than a minor may work in a day") in found
    assert ("ghost", "is not on the staff list") in found and any(rule == "is over their weekly hours limit" for _s, rule in found)
    assert any(rule == "leaves too little rest between shifts" for _s, rule in found) and any(rule == "works too many days in a row" for _s, rule in found)
    two = [Shift("one", "server", MONDAY, 600, 900, person="b"), Shift("two", "server", MONDAY, 840, 1100, person="b")]
    assert [v.rule for v in scheduling.validate(two, [Person("b", frozenset({"server"}))], Rules())] == ["overlaps another shift"]
    # An unset rule is skipped: with no rules at all only roles, availability, time off and overlaps are checked.
    assert Rules().unset() == list(scheduling.RULE_NAMES)
    assert scheduling.validate([Shift("r", "host", MONDAY + timedelta(days=3), 0, 300, person="kid"), Shift("l", "host", MONDAY + timedelta(days=2), 600, 1020, person="kid")], [minor], Rules()) == []


def test_paid_hours_leave_out_an_unpaid_break_once_a_shift_is_long_enough():
    assert scheduling.paid_minutes(Shift("a", "server", MONDAY, 600, 1080), RULES) == 450  # eight hours less a 30-minute break
    assert scheduling.paid_minutes(Shift("b", "server", MONDAY, 600, 900), RULES) == 300    # five hours: no break
    assert scheduling.paid_minutes(Shift("a", "server", MONDAY, 600, 1080), Rules()) == 480 and scheduling.break_minutes(Shift("a", "server", MONDAY, 600, 1080), RULES) == 30


def _random_week(rng, employees):
    roles = ["server", "line_cook", "host", "dish", "bartender"]
    people = []
    for n in range(employees):
        held = frozenset(rng.sample(roles, k=rng.choice((1, 1, 2))))
        availability = {} if rng.random() < 0.5 else {d: [(rng.choice((480, 600, 900)), rng.choice((1320, 1380, 1440)))] for d in rng.sample(range(7), k=rng.randint(3, 7))}
        off = tuple((MONDAY + timedelta(days=rng.randrange(7)), 0, 1440) for _ in range(rng.choice((0, 0, 1))))
        people.append(Person(f"p{n}", held, max_weekly_hours=rng.choice((None, 20, 32, 40)), desired_weekly_hours=rng.choice((None, 16, 30, 38)), is_minor=rng.random() < 0.1,
                             availability=availability, time_off=off))
    rules = Rules(weekly_overtime_hours=rng.choice((None, 40)), daily_overtime_hours=rng.choice((None, 8)), min_rest_hours=rng.choice((None, 8, 10, 11)), min_shift_hours=rng.choice((None, 3, 4)),
                  max_shift_hours=rng.choice((None, 8, 9)), max_hours_per_day=rng.choice((None, 9, 10, 12)), max_consecutive_days=rng.choice((None, 5, 6)),
                  allow_on_call=rng.choice((None, False, True)), minor_rules=rng.choice(({}, {"max_hours_per_day": 6, "latest_end": "22:00"})))
    needs = {}
    for role in roles:
        scale = max(1, employees // 12)
        for d in range(7):
            p50 = _need((11, 14.5, rng.randint(0, 2) * scale), (17, 22, rng.randint(1, 3) * scale))
            needs[(role, MONDAY + timedelta(days=d))] = (p50, [n + (1 if n and rng.random() < 0.3 else 0) for n in p50])
    return needs, people, rules


def test_acceptance_the_validator_finds_no_hard_constraint_violation_across_50_random_fixtures():
    rng = random.Random(2026)
    assigned = 0
    for _ in range(50):
        needs, people, rules = _random_week(rng, rng.randint(4, 14))
        draft = scheduling.generate(needs, people, rules, seconds=1.5)
        assert scheduling.validate(draft.shifts, people, rules) == []
        assert draft.unset_rules == rules.unset()
        assigned += sum(1 for s in draft.shifts if s.person)
    assert assigned > 500  # and it does schedule people: not fifty empty weeks


def test_acceptance_generation_finishes_within_30_seconds_for_40_employees():
    needs, people, rules = _random_week(random.Random(40), 40)
    started = time.monotonic()
    draft = scheduling.generate(needs, people, rules)
    took = time.monotonic() - started
    assert took < 30, f"generation took {took:.1f} s"
    assert scheduling.validate(draft.shifts, people, rules) == [] and sum(1 for s in draft.shifts if s.person) >= 40
