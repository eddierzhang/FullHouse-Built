"""The intraday reforecast (spec section 8.6): every 15 minutes during opening hours, compare the day so far with
the forecast and adjust the rest of the day.

    elapsed_share = share of the day's forecast volume expected by now
    pace          = actual volume so far / forecast p50 volume so far
    weight        = min(1, elapsed_share / 0.40)
    multiplier    = weight * pace + (1 - weight), limited to 0.50 to 1.75
    revised(t)    = forecast(t) * multiplier, for each remaining interval and each of p10, p50, p90

No adjustment is made while elapsed_share is under 0.15.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_ELAPSED = 0.15
FULL_WEIGHT_AT = 0.40
LIMITS = (0.50, 1.75)
SUGGESTION_MINUTES = 20
COOL_OFF_MINUTES = 60
MAX_DAYPART_MISS = 0.25


@dataclass(frozen=True)
class Pace:
    elapsed_share: float
    pace: float | None
    weight: float
    multiplier: float
    #: False while too little of the day has passed to say anything.
    adjusted: bool


def reforecast(forecast_so_far: float, forecast_day: float, actual_so_far: float) -> Pace:
    elapsed = forecast_so_far / forecast_day if forecast_day > 0 else 0.0
    if elapsed < MIN_ELAPSED or forecast_so_far <= 0:
        return Pace(elapsed, None if forecast_so_far <= 0 else actual_so_far / forecast_so_far, 0.0, 1.0, False)
    pace = actual_so_far / forecast_so_far
    weight = min(1.0, elapsed / FULL_WEIGHT_AT)
    multiplier = max(LIMITS[0], min(LIMITS[1], weight * pace + (1 - weight)))
    return Pace(elapsed, pace, weight, multiplier, True)
