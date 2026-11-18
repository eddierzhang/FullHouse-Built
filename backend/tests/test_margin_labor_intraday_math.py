"""The intraday golden test (spec sections 8.6 and 12)."""

import math

from app.margin_labor import intraday_math


def test_golden_intraday_multiplier_1_1875_and_140_remaining_becomes_166_25():
    out = intraday_math.reforecast(forecast_so_far=60, forecast_day=200, actual_so_far=75)
    assert math.isclose(out.elapsed_share, 0.30) and math.isclose(out.pace, 1.25) and math.isclose(out.weight, 0.75)
    assert math.isclose(out.multiplier, 1.1875) and out.adjusted
    assert math.isclose(140 * out.multiplier, 166.25)


def test_no_adjustment_under_15_percent_of_the_day_and_the_multiplier_is_limited():
    early = intraday_math.reforecast(28, 200, 60)  # 14% of the day, running at twice the forecast
    assert early.adjusted is False and early.multiplier == 1.0 and math.isclose(early.pace, 60 / 28)
    assert intraday_math.reforecast(30, 200, 60).adjusted is True  # 15%: now it counts, at part weight
    assert math.isclose(intraday_math.reforecast(30, 200, 60).multiplier, 0.375 * 2 + 0.625)
    assert intraday_math.reforecast(100, 200, 400).multiplier == 1.75 and intraday_math.reforecast(100, 200, 10).multiplier == 0.50
    assert intraday_math.reforecast(100, 200, 100).multiplier == 1.0  # on forecast
    assert intraday_math.reforecast(0, 0, 0) == intraday_math.Pace(0.0, None, 0.0, 1.0, False)
