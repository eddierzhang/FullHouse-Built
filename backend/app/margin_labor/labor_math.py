"""Module D's arithmetic for forecasts by interval and people needed (spec sections 8.2 and 8.3). Pure functions.

Interval split:
    share(t)        = mean share of the day's volume in interval t, same weekday, last 8 weeks, smoothed with a
                      3-interval moving average and rescaled to sum to 1
    interval_p50(t) = daily_p50 * share(t)
    interval_p10(t) = daily_p10 * share(t) * r10(daypart)        (p90 likewise, with r90)
r10 and r90 are the 10th and 90th percentiles of actual interval volume / (actual daily volume * share), per
daypart: one interval is noisier than a whole day, so its range is wider.

Range calibration: over the last 8 weeks, the share of actual daily totals inside p10 to p90 should be 80%. Below
70% or above 90%, both half-widths are scaled by the smallest factor that brings it to 80%.

A standard is how many units of work one person in a role handles an hour:
    rate                 = units / labor hours, per 30-minute interval with someone on and at least one unit
    units_per_labor_hour = 70th percentile of rate over the intervals whose units are above the role's median
    need(role, t)        = max(min_on_floor, ceil(units(t) / (units_per_labor_hour * 0.5)))
"""

from __future__ import annotations

import math
from statistics import median

from app.margin_labor.forecasting import Quantiles, percentile

CALC_VERSION = "d1"
INTERVAL_MINUTES = 15
PER_DAY = 24 * 60 // INTERVAL_MINUTES
SHARE_WEEKS = 8
COVERAGE_TARGET = 0.80
COVERAGE_BAND = (0.70, 0.90)
STANDARD_PERCENTILE = 0.70
MIN_QUALIFYING = 100
MIN_PUNCH_WEEKS = 4
#: (name, first local hour, last local hour exclusive)
DAYPARTS = (("morning", 0, 11), ("lunch", 11, 15), ("afternoon", 15, 17), ("dinner", 17, 22), ("late", 22, 24))
DEFAULT_R = (1.0, 1.0)


def daypart(slot: int) -> str:
    """The daypart of a 15-minute slot of the local day (slot 0 is 00:00)."""
    hour = slot * INTERVAL_MINUTES // 60
    return next(name for name, start, end in DAYPARTS if start <= hour < end)


def need(units: float, units_per_labor_hour: float, min_on_floor: int = 0) -> int:
    """People needed for a 30-minute interval: each handles half an hour of the standard."""
    if units_per_labor_hour <= 0:
        return int(min_on_floor)
    # A hair under a whole person, from float noise, is that person: 24 / 8 is 3, not 4.
    return max(int(min_on_floor), math.ceil(max(0.0, units) / (units_per_labor_hour * 0.5) - 1e-9))


def learn_standard(observations: list[tuple[float, float]], punch_weeks: int) -> tuple[float | None, int]:
    """(units per labor hour, how many intervals it rests on) from (units, labor hours) per 30-minute interval.
    None without 4 weeks of punches and 100 qualifying intervals: the owner then enters it."""
    qualifying = [(units, hours) for units, hours in observations if hours > 0 and units >= 1]
    if punch_weeks < MIN_PUNCH_WEEKS or len(qualifying) < MIN_QUALIFYING:
        return None, len(qualifying)
    middle = median(units for units, _h in qualifying)
    busy = [units / hours for units, hours in qualifying if units > middle] or [units / hours for units, hours in qualifying]
    return round(percentile(busy, STANDARD_PERCENTILE), 4), len(qualifying)


def rates(observations: list[tuple[float, float]]) -> list[float]:
    """Observed units per labor hour, for the histogram behind a standard."""
    return [units / hours for units, hours in observations if hours > 0 and units >= 1]


def shares(days: list[list[float]]) -> list[float]:
    """The share of a day's volume in each interval: the mean over the days given, smoothed over 3 intervals, summing to 1."""
    slots = len(days[0]) if days else PER_DAY
    mean = [0.0] * slots
    used = 0
    for day in days:
        total = sum(day)
        if total <= 0:
            continue
        used += 1
        for i, value in enumerate(day):
            mean[i] += value / total
    if not used:
        return [0.0] * slots
    mean = [v / used for v in mean]
    smooth = [(mean[max(0, i - 1)] + mean[i] + mean[min(slots - 1, i + 1)]) / 3 if 0 < i < slots - 1 else mean[i] for i in range(slots)]
    total = sum(smooth)
    return [s / total for s in smooth] if total > 0 else smooth


def range_ratios(days: list[list[float]], share: list[float]) -> dict[str, tuple[float, float]]:
    """r10 and r90 per daypart: how far one interval strays from its share of the day."""
    by_part: dict[str, list[float]] = {}
    for day in days:
        total = sum(day)
        if total <= 0:
            continue
        for i, value in enumerate(day):
            if share[i] > 0:
                by_part.setdefault(daypart(i), []).append(value / (total * share[i]))
    return {part: (percentile(values, 0.1), percentile(values, 0.9)) for part, values in by_part.items() if len(values) >= 8}


def split(daily: Quantiles, share: list[float], ratios: dict[str, tuple[float, float]]) -> list[Quantiles]:
    """A day's forecast as one forecast per interval."""
    out = []
    for i, s in enumerate(share):
        r10, r90 = ratios.get(daypart(i), DEFAULT_R)
        mid = max(0.0, daily.p50 * s)
        # Expected stays the day's share; the range is held around it rather than re-sorted, so the intervals still sum to the day.
        out.append(Quantiles(min(mid, max(0.0, daily.p10 * s * r10)), mid, max(mid, daily.p90 * s * r90)))
    return out


def coverage(rows: list[tuple[float, Quantiles]]) -> float | None:
    return sum(1 for actual, q in rows if q.p10 <= actual <= q.p90) / len(rows) if rows else None


def calibration_factor(rows: list[tuple[float, Quantiles]]) -> float:
    """What to scale both half-widths by. 1 while 70% to 90% of the last 8 weeks' days fell inside the range;
    otherwise the smallest factor that puts 80% of them inside."""
    share = coverage(rows)
    if share is None or COVERAGE_BAND[0] <= share <= COVERAGE_BAND[1]:
        return 1.0
    needed = []
    for actual, q in rows:
        width = (q.p90 - q.p50) if actual >= q.p50 else (q.p50 - q.p10)
        gap = abs(actual - q.p50)
        needed.append(0.0 if gap == 0 else (gap / width if width > 0 else math.inf))
    needed.sort()
    factor = needed[max(0, math.ceil(COVERAGE_TARGET * len(needed)) - 1)]
    return 1.0 if not math.isfinite(factor) or factor <= 0 else round(factor, 4)
