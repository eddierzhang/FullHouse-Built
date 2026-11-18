"""Module D golden and property tests for forecasts by interval and people needed (spec sections 8.2, 8.3 and 12)."""

import math
import random

from app.margin_labor import labor_math
from app.margin_labor.forecasting import Quantiles


def test_golden_labor_requirement_24_covers_at_16_an_hour_needs_3_and_31_needs_4():
    assert labor_math.need(24, 16) == 3  # capacity per head in 30 minutes is 8
    assert labor_math.need(31, 16) == 4
    assert labor_math.need(24.0000000001, 16) == 3 and labor_math.need(24.1, 16) == 4
    assert labor_math.need(0, 16) == 0 and labor_math.need(0, 16, min_on_floor=2) == 2 and labor_math.need(40, 16, min_on_floor=2) == 5
    assert labor_math.need(10, 0, min_on_floor=1) == 1  # no standard yet: the floor alone


def test_property_need_is_never_below_the_floor_and_never_falls_when_units_rise():
    rng = random.Random(3)
    for _ in range(500):
        standard, floor = rng.uniform(1, 60), rng.randint(0, 4)
        units = sorted(rng.uniform(0, 400) for _ in range(6))
        needs = [labor_math.need(u, standard, floor) for u in units]
        assert needs == sorted(needs) and all(n >= floor for n in needs)


def test_a_standard_is_the_70th_percentile_of_the_busier_half_and_needs_enough_data():
    # 120 intervals with two people on (one labor hour): half slow at 6 units, half busy at 14 to 18.
    slow = [(6.0, 1.0)] * 60
    busy = [(14.0 + (n % 5), 1.0) for n in range(60)]
    standard, sample = labor_math.learn_standard(slow + busy, punch_weeks=8)
    assert sample == 120 and standard == 17.0  # the busier half is 14, 15, 16, 17, 18 twelve times over: its 70th percentile
    assert labor_math.learn_standard(slow + busy, punch_weeks=3) == (None, 120)  # under 4 weeks of punches
    assert labor_math.learn_standard((slow + busy)[:99], punch_weeks=8) == (None, 99)  # under 100 qualifying intervals
    # An interval with nobody on, or with no units, teaches nothing.
    assert labor_math.learn_standard(slow + busy + [(0.0, 1.0), (5.0, 0.0)] * 30, punch_weeks=8)[1] == 120
    assert labor_math.learn_standard([(8.0, 1.0)] * 120, punch_weeks=8)[0] == 8.0  # every interval alike: that rate
    assert labor_math.rates([(8.0, 2.0), (0.0, 1.0), (3.0, 0.0)]) == [4.0]


def _day(lunch=10.0, dinner=20.0):
    day = [0.0] * labor_math.PER_DAY
    for slot in range(46, 56):   # 11:30 to 14:00
        day[slot] = lunch
    for slot in range(68, 84):   # 17:00 to 21:00
        day[slot] = dinner
    return day


def test_property_interval_shares_sum_to_one_and_stay_inside_opening_hours():
    rng = random.Random(9)
    for _ in range(40):
        days = [[v * rng.uniform(0.6, 1.4) for v in _day(rng.uniform(5, 15), rng.uniform(10, 30))] for _ in range(8)]
        share = labor_math.shares(days)
        assert abs(sum(share) - 1.0) < 0.001 and all(s >= 0 for s in share)
        # The moving average spills one interval past each end of a service, and no further.
        assert sum(share[:45]) == 0 and sum(share[85:]) == 0 and share[60] == 0 and share[45] > 0
    assert labor_math.shares([]) == [0.0] * labor_math.PER_DAY and sum(labor_math.shares([[0.0] * 96])) == 0


def test_the_split_spreads_a_day_by_share_and_widens_the_range_by_daypart():
    days = [_day() for _ in range(8)]
    share = labor_math.shares(days)
    total = sum(_day())
    daily = Quantiles(total * 0.9, total, total * 1.1)
    even = labor_math.split(daily, share, labor_math.range_ratios(days, share))
    assert math.isclose(sum(q.p50 for q in even), total) and math.isclose(even[70].p50, 20.0, rel_tol=0.02)
    assert labor_math.daypart(46) == "lunch" and labor_math.daypart(70) == "dinner" and labor_math.daypart(92) == "late" and labor_math.daypart(8) == "morning"
    # Intervals that swing more than the day does get a wider range than the day's own.
    rng = random.Random(4)
    noisy = [[v * rng.uniform(0.5, 1.5) for v in _day()] for _ in range(8)]
    noisy_share = labor_math.shares(noisy)
    ratios = labor_math.range_ratios(noisy, noisy_share)
    assert ratios["dinner"][0] < 1 < ratios["dinner"][1]
    wide = labor_math.split(daily, noisy_share, ratios)
    assert (wide[70].p90 - wide[70].p10) / wide[70].p50 > (daily.p90 - daily.p10) / daily.p50
    assert all(q.p10 <= q.p50 <= q.p90 for q in wide)


def test_range_calibration_scales_to_80_percent_only_outside_70_to_90():
    q = Quantiles(90, 100, 110)
    inside = [(100.0, q)] * 8 + [(130.0, q)] * 2          # 80% inside: left alone
    assert labor_math.coverage(inside) == 0.8 and labor_math.calibration_factor(inside) == 1.0
    narrow = [(100.0 + 4 * n, q) for n in range(10)]      # actuals 100..136: only 100, 104, 108 inside
    assert labor_math.coverage(narrow) == 0.3
    factor = labor_math.calibration_factor(narrow)
    assert factor == 2.8  # the eighth of ten is 128: 28 over, against a half-width of 10
    widened = [(a, q.widened(factor)) for a, _q in narrow]
    assert labor_math.coverage(widened) == 0.8
    wide = [(100.0 + n, Quantiles(50, 100, 150)) for n in range(10)]  # all inside a range five times too wide
    assert labor_math.coverage(wide) == 1.0 and labor_math.calibration_factor(wide) == 0.14
    assert labor_math.coverage([(a, Quantiles(50, 100, 150).widened(0.14)) for a, _q in wide]) == 0.8
    assert labor_math.calibration_factor([]) == 1.0 and labor_math.calibration_factor([(120.0, Quantiles(100, 100, 100))] * 10) == 1.0  # no width to scale
