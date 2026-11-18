"""The forecast core and Module C's arithmetic (spec sections 7, 8.2 and 12): golden and property tests, no database."""

import math
import random
from datetime import date, timedelta
from decimal import Decimal as D

from app.margin_labor import forecasting, ordering_math
from app.margin_labor.forecasting import Baseline, Driver, Quantiles, WithDrivers

START = date(2026, 6, 1)  # a Monday


def _history(days, level=lambda d: 100.0):
    return {START + timedelta(days=n): level(START + timedelta(days=n)) for n in range(days)}


def test_golden_order_quantity_period_demand_16422_need_17243_order_4_cases():
    days = [(D(3000), D(4000), D(5000)), (D(3800), D(5000), D(6200)), (D(4500), D(6000), D(7500))]  # Tue, Wed, Thu: delivery Tuesday, next Friday
    assert [ordering_math.sigma_day(lo, hi).quantize(D("0.1")) for lo, _m, hi in days] == [D("780.3"), D("936.4"), D("1170.5")]
    out = ordering_math.order_quantity(days, shelf_life_days=4, loss=D("0.05"), est_on_hand_at_delivery=D(3000), pack_size=D("4535.92"))
    whole = lambda v: v.quantize(D(1))  # noqa: E731
    assert (out.sum_p50, out.root_sum_variance.quantize(D("0.1")), out.z) == (D("15000.0000"), D("1689.9"), D("0.8416"))
    assert (whole(out.period_demand), whole(out.need), whole(out.raw_qty)) == (D(16422), D(17243), D(14243))
    assert (out.raw_qty / D("4535.92")).quantize(D("0.01")) == D("3.14") and out.packs == 4 and out.order_qty == D("18143.6800")


def test_order_quantity_shelf_life_service_level_and_nothing_to_order():
    days = [(D(3000), D(4000), D(5000))] * 5
    short = ordering_math.order_quantity(days, shelf_life_days=2, loss=D(0), est_on_hand_at_delivery=D(0), pack_size=D(1000))
    assert short.days_used == 2 and short.sum_p50 == D("8000.0000")  # only what can be used before it spoils
    assert ordering_math.order_quantity(days, 10, D(0), D(0), D(1000)).z == D("1.6449") and ordering_math.order_quantity(days, None, D(0), D(0), D(1000)).z == D("1.6449")
    assert ordering_math.order_quantity(days, 5, D(0), D(0), D(1000)).z == D("0.8416")
    plenty = ordering_math.order_quantity(days, 4, D(0), est_on_hand_at_delivery=D(50000), pack_size=D(1000))
    assert plenty.packs == 0 and plenty.order_qty == D("0.0000") and plenty.raw_qty < 0
    assert ordering_math.order_quantity(days[:1], 4, D(0), D(3999), D(1000)).packs == 1  # a gram short of the buffer still rounds up to a pack


def test_loss_factor_par_and_the_running_estimate():
    assert ordering_math.loss_factor(D(13000), D(82000), live=True) == D("0.150000")  # 15.9%, limited to 15%
    assert ordering_math.loss_factor(D(4100), D(82000), live=True) == D("0.050000")
    assert ordering_math.loss_factor(D(-500), D(82000), live=True) == D(0) and ordering_math.loss_factor(D(4100), D(82000), live=False) == D(0)
    assert ordering_math.par_today(D(3000), D(4000), D(5000)) == D("4526.3363")  # 4,000 + 0.6745 x 780.3
    assert ordering_math.next_estimate(D(5000), D(18000), D(4000), D("0.05")) == D("18800.00")
    assert ordering_math.next_estimate(D(1000), D(0), D(4000), D(0)) == D(0)  # never below nothing
    assert ordering_math.at_delivery(D(9000), D(6000)) == D(3000) and ordering_math.at_delivery(D(2000), D(6000)) == D(0)


def test_baseline_is_the_weekday_median_times_the_trend_with_a_range_from_twelve_weeks():
    # Twelve weeks; Fridays run 200, other days 100; the last two weeks are 10% busier.
    last = START + timedelta(days=83)
    history = _history(84, lambda d: (200.0 if d.weekday() == 4 else 100.0) * (1.1 if d > last - timedelta(days=14) else 1.0))
    friday = START + timedelta(days=88)
    made = Baseline().forecast(history, friday, {})
    # Six Fridays back: 220, 220, 200, 200, 200, 200 -> median 200; trend 1.1.
    assert made.stage == "baseline" and round(made.quantiles.p50, 2) == 220.0
    # The 10th and 90th percentiles of twelve Fridays (ten at 200, two at 220) are 200 and 218, times the trend.
    assert round(made.quantiles.p10, 2) == 220.0 and round(made.quantiles.p90, 2) == 239.8
    assert Baseline().forecast(_history(21), START + timedelta(days=21), {}) is None  # under four weeks: no forecast
    assert Baseline().forecast(_history(28), START + timedelta(days=28), {}).quantiles == Quantiles(100.0, 100.0, 100.0)
    # A forecast two weeks out uses the same weekdays, as known today.
    assert round(Baseline().forecast(history, friday + timedelta(days=14), {}).quantiles.p50, 2) == 220.0


def test_the_trend_is_limited_and_holidays_and_closures_are_left_out_of_training():
    surge = _history(56, lambda d: 300.0 if d >= START + timedelta(days=42) else 100.0)
    assert round(Baseline().forecast(surge, START + timedelta(days=56), {}).quantiles.p50, 2) == 125.0  # median of six Mondays is 100; a 3x jump is held to 1.25
    slump = _history(56, lambda d: 10.0 if d >= START + timedelta(days=42) else 100.0)
    assert round(Baseline().forecast(slump, START + timedelta(days=56), {}).quantiles.p50, 2) == 80.0
    history = _history(56)
    odd = START + timedelta(days=49)  # a Monday the restaurant was closed
    history[odd] = 0.0
    with_closure = Baseline().forecast(history, START + timedelta(days=56), {odd: [Driver("closure", "Burst pipe")]})
    assert with_closure.quantiles == Quantiles(100.0, 100.0, 100.0)
    assert Baseline().forecast(history, START + timedelta(days=56), {}).quantiles.p10 < 100.0  # counted as normal, it drags the range down


def test_drivers_learn_a_multiplier_from_six_days_and_use_the_owners_until_then():
    days = 16 * 7
    game = {START + timedelta(days=5 + 7 * k): [Driver("local_event", "large")] for k in range(2, 16, 2)}  # every other Saturday from week 3: seven games
    history = _history(days, lambda d: 150.0 if d in game else 100.0)
    target = START + timedelta(days=days + 5)
    drivers = {**game, target: [Driver("local_event", "large")]}
    made = WithDrivers().forecast(history, target, drivers)
    # Game days are not left out of the baseline (only closures and holidays are), so a Saturday's baseline is already
    # 125, half way; the multiplier learned against it is 150 / 125, and the two together land on 150.
    assert made.stage == "drivers" and made.drivers_applied == [{"kind": "local_event", "name": "large", "multiplier": 1.2, "source": "learned", "days": 6}]
    assert round(made.quantiles.p50, 1) == 150.0
    # Five past days is not enough: the owner's expected effect is used, and said to be theirs.
    few = {d: v for d, v in list(game.items())[:5]}
    owner = WithDrivers().forecast(history, target, {**few, target: [Driver("local_event", "large", expected=1.3)]})
    assert owner.drivers_applied[0]["source"] == "owner" and owner.drivers_applied[0]["multiplier"] == 1.3 and owner.drivers_applied[0]["days"] == 4  # the first had no baseline yet
    assert WithDrivers().forecast(history, target, {**few, target: [Driver("local_event", "large")]}).drivers_applied == []  # nothing known: no adjustment
    # A multiplier is limited to 0.60 to 1.60, a holiday with no history widens the range, and a closure is zero.
    wild = WithDrivers().forecast(_history(days, lambda d: 500.0 if d in game else 100.0), target, drivers)
    assert wild.drivers_applied[0]["multiplier"] == 1.6
    noisy = _history(days, lambda d: 100.0 + (d.toordinal() % 5) * 10)
    plain = WithDrivers().forecast(noisy, target, {}).quantiles
    holiday = WithDrivers().forecast(noisy, target, {target: [Driver("holiday", "Thanksgiving")]})
    assert holiday.drivers_applied[0]["source"] == "no_history" and holiday.quantiles.p50 == plain.p50
    assert math.isclose(holiday.quantiles.p90 - holiday.quantiles.p50, (plain.p90 - plain.p50) * 1.5) and holiday.quantiles.p10 < plain.p10
    closed = WithDrivers().forecast(history, target, {target: [Driver("closure", "Private event")]})
    assert closed.quantiles == Quantiles(0.0, 0.0, 0.0) and closed.drivers_applied[0]["source"] == "closure"
    assert WithDrivers().forecast(_history(49), START + timedelta(days=49), {START + timedelta(days=49): [Driver("local_event", "large", expected=1.3)]}).drivers_applied == []  # under 8 weeks: baseline alone


def test_pinball_average_miss_and_range_coverage():
    assert math.isclose(forecasting.pinball(0.9, 110, 100), 9.0) and math.isclose(forecasting.pinball(0.9, 90, 100), 1.0) and forecasting.pinball(0.5, 100, 100) == 0
    score = forecasting.Score()
    for actual, made in ((100, Quantiles(80, 90, 120)), (150, Quantiles(80, 100, 120)), (50, Quantiles(40, 50, 60))):
        score.add(actual, made)
    assert math.isclose(score.wape, 60 / 300) and math.isclose(score.coverage, 2 / 3) and score.days == 3
    assert forecasting.Score().wape is None and forecasting.Score().as_dict() == {"days": 0, "wape": None, "pinball": None, "coverage": None}


def test_a_backtest_replays_week_by_week_and_a_later_stage_must_beat_the_baseline_on_both_measures():
    days = 20 * 7
    game = {START + timedelta(days=5 + 7 * k): [Driver("local_event", "large")] for k in range(1, 20, 2)}
    history = _history(days, lambda d: 160.0 if d in game else 100.0)
    today = START + timedelta(days=days)
    base, rows = forecasting.backtest(Baseline(), history, game, today - timedelta(days=56), today - timedelta(days=1))
    learned, _ = forecasting.backtest(WithDrivers(), history, game, today - timedelta(days=56), today - timedelta(days=1))
    assert base.days == learned.days == 56 and len(rows) == 56 and rows[0][0] == today - timedelta(days=56)
    assert learned.wape < base.wape and learned.mean_pinball < base.mean_pinball
    assert forecasting.choose_stage(history, game, today)[0] == "drivers"
    stage, scores = forecasting.choose_stage(_history(days), {}, today)  # no drivers: the same forecast, so no promotion
    assert stage == "baseline" and scores["baseline"] == scores["drivers"] and scores["baseline"]["wape"] == 0.0
    # Each week is forecast only from what was known before it: a week of history removed changes nothing earlier.
    shorter = {d: v for d, v in history.items() if d < today - timedelta(days=7)}
    again, rows_again = forecasting.backtest(Baseline(), shorter, game, today - timedelta(days=56), today - timedelta(days=8))
    assert rows_again == rows[:49]


def test_property_every_forecast_is_ordered_and_never_negative():
    rng = random.Random(11)
    for _ in range(200):
        days = rng.randint(28, 120)
        history = _history(days, lambda d: max(0.0, rng.gauss(80, 60)))
        marked = {d: [Driver("local_event", rng.choice(("small", "large")), expected=rng.choice((None, 0.2, 1.4, 3.0)))] for d in history if rng.random() < 0.15}
        target = START + timedelta(days=days + rng.randint(0, 13))
        marked.setdefault(target, [Driver("holiday", "Odd day")] if rng.random() < 0.3 else [])
        for model in (Baseline(), WithDrivers()):
            made = model.forecast(history, target, marked)
            if made is not None:
                q = made.quantiles
                assert 0 <= q.p10 <= q.p50 <= q.p90


def test_the_built_in_calendar_names_us_holidays_and_the_days_beside_them():
    held = forecasting.holiday_drivers("United States", date(2026, 11, 1), date(2026, 12, 31))
    assert [d.name for d in held[date(2026, 11, 26)]] == ["Thanksgiving"] and [d.name for d in held[date(2026, 11, 25)]] == ["Day before Thanksgiving"]
    assert [d.name for d in held[date(2026, 12, 25)]] == ["Christmas Day"] and [d.name for d in held[date(2026, 12, 24)]] == ["Christmas Eve"]  # the day itself wins
    assert [d.name for d in held[date(2026, 12, 26)]] == ["Day after Christmas Day"] and date(2026, 11, 17) not in held
    assert forecasting.us_holidays(2026)[date(2026, 5, 25)] == "Memorial Day" and forecasting.us_holidays(2026)[date(2026, 9, 7)] == "Labor Day"
    assert forecasting.holiday_drivers("France", date(2026, 11, 1), date(2026, 12, 31)) == {}  # no built-in calendar: the owner adds them
