"""The forecast core (spec section 8.2): a daily total with a range, from one of three stages behind one
`Forecaster` interface. Pure functions of a daily history and the known outside conditions, so a backtest is the
same code run at earlier dates. Forecast values are floats, as the spec allows.

Stage 1, baseline (needs 4 weeks). For a weekday:
    base  = median of the same weekday over the last 6 normal weeks
    trend = mean daily total, last 2 weeks / mean daily total, the 4 weeks before; limited to 0.80 to 1.25
    p50   = base * trend
    p10   = 10th percentile of the same weekday over the last 12 normal weeks * trend     (p90 likewise)
A normal week leaves out closures and holidays. With fewer than 12 weeks, the weeks there are.

Stage 2, driver multipliers (needs 8 weeks):
    multiplier(driver) = median(actual / baseline_p50) on past days with that driver, limited to 0.60 to 1.60
    forecast           = baseline * product of the multipliers that apply to the day
A multiplier is learned from 6 past days; before that an owner's own expected effect is used, a holiday with no
history widens the range by 1.5, and a closure makes the forecast 0.

Stage 3 (a quantile model, 52 weeks) is not built: the spec builds it only after stages 1 and 2 are live.

A later stage replaces an earlier one only when it has lower average miss and lower pinball loss on a rolling
backtest of the last 8 weeks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Protocol

TREND_LIMITS = (0.80, 1.25)
MULTIPLIER_LIMITS = (0.60, 1.60)
BASE_WEEKS, RANGE_WEEKS = 6, 12
MIN_DRIVER_DAYS = 6
NO_HISTORY_WIDEN = 1.5
ABNORMAL_KINDS = ("closure", "holiday")
QUANTILES = (0.1, 0.5, 0.9)


@dataclass(frozen=True)
class Quantiles:
    p10: float
    p50: float
    p90: float

    @staticmethod
    def ordered(a: float, b: float, c: float) -> "Quantiles":
        low, mid, high = sorted((max(0.0, a), max(0.0, b), max(0.0, c)))
        return Quantiles(low, mid, high)

    def scaled(self, factor: float) -> "Quantiles":
        return Quantiles.ordered(self.p10 * factor, self.p50 * factor, self.p90 * factor)

    def widened(self, factor: float) -> "Quantiles":
        """Both half-widths scaled by `factor`, the middle kept."""
        return Quantiles.ordered(self.p50 - (self.p50 - self.p10) * factor, self.p50, self.p50 + (self.p90 - self.p50) * factor)


@dataclass(frozen=True)
class Driver:
    """Something outside the restaurant on one day: a holiday, a local event, a promotion, rain, a closure."""

    kind: str
    name: str = ""
    #: The effect the owner expects, used until six past days have taught one.
    expected: float | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.name)


@dataclass
class Forecast:
    quantiles: Quantiles
    stage: str
    #: [{"kind", "name", "multiplier", "source": learned|owner|no_history|closure, "days": n}]
    drivers_applied: list[dict] = field(default_factory=list)


History = dict[date, float]
Drivers = dict[date, list[Driver]]


class Forecaster(Protocol):
    name: str
    min_weeks: int

    def forecast(self, history: History, target: date, drivers: Drivers) -> Forecast | None: ...


def percentile(values: list[float], q: float) -> float:
    """The q-th quantile by linear interpolation between the ranked values."""
    ranked = sorted(values)
    if len(ranked) == 1:
        return ranked[0]
    position = q * (len(ranked) - 1)
    low = int(position)
    high = min(low + 1, len(ranked) - 1)
    return ranked[low] + (ranked[high] - ranked[low]) * (position - low)


def clamp(value: float, limits: tuple[float, float]) -> float:
    return max(limits[0], min(limits[1], value))


def abnormal(drivers: Drivers, day: date) -> bool:
    return any(d.kind in ABNORMAL_KINDS for d in drivers.get(day, ()))


def _before(history: History, target: date) -> date | None:
    """The last day of history before `target`: what was known when the forecast was made."""
    known = [d for d in history if d < target]
    return max(known) if known else None


class Baseline:
    """Stage 1."""

    name = "baseline"
    min_weeks = 4

    def forecast(self, history: History, target: date, drivers: Drivers) -> Forecast | None:
        last = _before(history, target)
        if last is None:
            return None
        same: list[float] = []
        day = target - timedelta(days=7)
        while day > last:
            day -= timedelta(days=7)
        earliest = min(history)
        while day >= earliest and len(same) < RANGE_WEEKS:
            if day in history and not abnormal(drivers, day):
                same.append(history[day])
            day -= timedelta(days=7)
        if len(same) < self.min_weeks:
            return None
        trend = self._trend(history, drivers, last)
        return Forecast(Quantiles.ordered(percentile(same, 0.1) * trend, median(same[:BASE_WEEKS]) * trend, percentile(same, 0.9) * trend), self.name)

    @staticmethod
    def _trend(history: History, drivers: Drivers, last: date) -> float:
        def mean(start: date, end: date) -> float | None:
            values = [history[d] for d in (start + timedelta(days=n) for n in range((end - start).days + 1)) if d in history and not abnormal(drivers, d)]
            return sum(values) / len(values) if values else None

        recent = mean(last - timedelta(days=13), last)
        earlier = mean(last - timedelta(days=41), last - timedelta(days=14))
        if recent is None or earlier is None or earlier <= 0:
            return 1.0
        return clamp(recent / earlier, TREND_LIMITS)


class WithDrivers:
    """Stage 2: the baseline, adjusted for the known outside conditions of the day."""

    name = "drivers"
    min_weeks = 8

    def __init__(self) -> None:
        self.baseline = Baseline()
        self._cache: dict[tuple[int, date], float | None] = {}

    def _baseline_p50(self, history: History, day: date, drivers: Drivers) -> float | None:
        key = (id(history), day)
        if key not in self._cache:
            made = self.baseline.forecast({d: v for d, v in history.items() if d < day}, day, drivers)
            self._cache[key] = made.quantiles.p50 if made is not None else None
        return self._cache[key]

    def multiplier(self, history: History, driver: Driver, target: date, drivers: Drivers) -> tuple[float | None, int]:
        """(the learned multiplier, how many past days taught it). None with fewer than six."""
        ratios = []
        for day, held in drivers.items():
            if day >= target or day not in history or not any(d.key == driver.key for d in held) or any(d.kind == "closure" for d in held):
                continue
            base = self._baseline_p50(history, day, drivers)
            if base is not None and base > 0:
                ratios.append(history[day] / base)
        if len(ratios) < MIN_DRIVER_DAYS:
            return None, len(ratios)
        return clamp(median(ratios), MULTIPLIER_LIMITS), len(ratios)

    def forecast(self, history: History, target: date, drivers: Drivers) -> Forecast | None:
        today = drivers.get(target, [])
        if any(d.kind == "closure" for d in today):
            closure = next(d for d in today if d.kind == "closure")
            return Forecast(Quantiles(0.0, 0.0, 0.0), self.name, [{"kind": "closure", "name": closure.name, "multiplier": 0.0, "source": "closure", "days": 0}])
        made = self.baseline.forecast(history, target, drivers)
        if made is None:
            return None
        last = _before(history, target)
        if last is None or (last - min(history)).days < self.min_weeks * 7 - 1:
            return made  # under 8 weeks: the baseline alone
        quantiles, applied = made.quantiles, []
        for driver in today:
            learned, days = self.multiplier(history, driver, target, drivers)
            if learned is not None:
                quantiles = quantiles.scaled(learned)
                applied.append({"kind": driver.kind, "name": driver.name, "multiplier": round(learned, 4), "source": "learned", "days": days})
            elif driver.expected is not None:
                effect = clamp(driver.expected, MULTIPLIER_LIMITS)
                quantiles = quantiles.scaled(effect)
                applied.append({"kind": driver.kind, "name": driver.name, "multiplier": round(effect, 4), "source": "owner", "days": days})
            elif driver.kind == "holiday":
                quantiles = quantiles.widened(NO_HISTORY_WIDEN)
                applied.append({"kind": driver.kind, "name": driver.name, "multiplier": 1.0, "source": "no_history", "days": days})
        return Forecast(quantiles, self.name, applied)


STAGES: dict[str, type] = {"baseline": Baseline, "drivers": WithDrivers}


def forecast_day(model: Forecaster, history: History, target: date, drivers: Drivers) -> Forecast | None:
    """`model`'s forecast for a day, with the one rule that holds at every stage: a closure is a forecast of zero."""
    closure = next((d for d in drivers.get(target, ()) if d.kind == "closure"), None)
    if closure is not None:
        return Forecast(Quantiles(0.0, 0.0, 0.0), model.name, [{"kind": "closure", "name": closure.name, "multiplier": 0.0, "source": "closure", "days": 0}])
    return model.forecast(history, target, drivers)


# --- how good a forecast was ---------------------------------------------------------------------------


def pinball(q: float, actual: float, forecast: float) -> float:
    """q * (actual - forecast) if actual >= forecast, else (1 - q) * (forecast - actual)"""
    return q * (actual - forecast) if actual >= forecast else (1 - q) * (forecast - actual)


@dataclass
class Score:
    days: int = 0
    abs_error: float = 0.0
    actual: float = 0.0
    pinball: float = 0.0
    inside: int = 0

    def add(self, actual: float, made: Quantiles) -> None:
        self.days += 1
        self.abs_error += abs(actual - made.p50)
        self.actual += actual
        self.pinball += sum(pinball(q, actual, v) for q, v in zip(QUANTILES, (made.p10, made.p50, made.p90)))
        self.inside += 1 if made.p10 <= actual <= made.p90 else 0

    @property
    def wape(self) -> float | None:
        """Average miss: sum(|actual - p50|) / sum(actual)"""
        return self.abs_error / self.actual if self.actual > 0 else None

    @property
    def mean_pinball(self) -> float | None:
        return self.pinball / (self.days * len(QUANTILES)) if self.days else None

    @property
    def coverage(self) -> float | None:
        """The share of days whose actual fell inside the Likely range. The target is 80%."""
        return self.inside / self.days if self.days else None

    def as_dict(self) -> dict:
        r = lambda v: None if v is None else round(v, 4)  # noqa: E731
        return {"days": self.days, "wape": r(self.wape), "pinball": r(self.mean_pinball), "coverage": r(self.coverage)}


def backtest(model: Forecaster, history: History, drivers: Drivers, start: date, end: date) -> tuple[Score, list[tuple[date, float, Quantiles]]]:
    """Replay history week by week: each week from `start` to `end` is forecast from what was known the day before it
    began, then scored against what happened."""
    score, rows = Score(), []
    week = start
    while week <= end:
        known = {d: v for d, v in history.items() if d < week}
        for n in range(7):
            day = week + timedelta(days=n)
            if day > end or day not in history or not known:
                continue
            made = model.forecast(known, day, drivers)
            if made is None:
                continue
            score.add(history[day], made.quantiles)
            rows.append((day, history[day], made.quantiles))
        week += timedelta(days=7)
    return score, rows


def choose_stage(history: History, drivers: Drivers, today: date) -> tuple[str, dict]:
    """The stage to use: drivers replaces the baseline only with lower average miss and lower pinball loss over the
    last 8 weeks. Returns the stage and both scores."""
    start = today - timedelta(days=56)
    base, _ = backtest(Baseline(), history, drivers, start, today - timedelta(days=1))
    with_drivers, _ = backtest(WithDrivers(), history, drivers, start, today - timedelta(days=1))
    better = (base.wape is not None and with_drivers.wape is not None and with_drivers.wape < base.wape
              and with_drivers.mean_pinball is not None and base.mean_pinball is not None and with_drivers.mean_pinball < base.mean_pinball)
    return ("drivers" if better else "baseline"), {"baseline": base.as_dict(), "drivers": with_drivers.as_dict()}


# --- the built-in holiday calendar ---------------------------------------------------------------------


def _nth(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th `weekday` (Monday = 0) of a month; n = -1 is the last."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def us_holidays(year: int) -> dict[date, str]:
    return {
        date(year, 1, 1): "New Year's Day", _nth(year, 1, 0, 3): "Martin Luther King Jr. Day", date(year, 2, 14): "Valentine's Day",
        _nth(year, 2, 0, 3): "Presidents' Day", _nth(year, 5, 6, 2): "Mother's Day", _nth(year, 5, 0, -1): "Memorial Day",
        _nth(year, 6, 6, 3): "Father's Day", date(year, 6, 19): "Juneteenth", date(year, 7, 4): "Independence Day", _nth(year, 9, 0, 1): "Labor Day",
        date(year, 10, 31): "Halloween", date(year, 11, 11): "Veterans Day", _nth(year, 11, 3, 4): "Thanksgiving", date(year, 12, 24): "Christmas Eve",
        date(year, 12, 25): "Christmas Day", date(year, 12, 31): "New Year's Eve",
    }


def holiday_drivers(country: str | None, start: date, end: date) -> Drivers:
    """Each named holiday, plus the day before and the day after, each learned on its own."""
    out: Drivers = {}
    if (country or "").strip().lower() not in ("united states", "usa", "us", "united states of america"):
        return out
    for year in range(start.year - 1, end.year + 2):
        for day, name in us_holidays(year).items():
            for offset, label in ((0, name), (-1, f"Day before {name}"), (1, f"Day after {name}")):
                when = day + timedelta(days=offset)
                if start <= when <= end and not any(d.kind == "holiday" and offset != 0 for d in out.get(when, [])):
                    held = out.setdefault(when, [])
                    if offset == 0:
                        held[:] = [d for d in held if d.kind != "holiday"]  # the day itself wins over being next to another
                        held.append(Driver("holiday", label))
                    elif not any(d.kind == "holiday" for d in held):
                        held.append(Driver("holiday", label))
    return out
