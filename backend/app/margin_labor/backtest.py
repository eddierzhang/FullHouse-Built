"""The forecast backtest command (spec section 12).

    python -m app.margin_labor.backtest                 the three synthetic fixtures
    python -m app.margin_labor.backtest --restaurant ID a restaurant's own daily item totals

It replays history week by week, forecasting each week from what was known before it, and reports average miss
(WAPE), pinball loss and range coverage per stage. A model change must not ship unless these are equal or better
on the fixtures.
"""

from __future__ import annotations

import argparse
import random
from datetime import date, timedelta

from app.margin_labor import forecasting, synthetic
from app.margin_labor.forecasting import Driver, Drivers, History

FIXTURE_END = date(2026, 11, 16)
FIXTURE_DAYS = 365
BACKTEST_WEEKS = 26


def fixture(profile: str, seed: int = 1) -> tuple[History, Drivers]:
    """A year of daily order counts for one synthetic restaurant, with a game day every other Saturday that lifts it by half."""
    rng = random.Random(f"{seed}:{profile}")
    history: History = {}
    drivers: Drivers = {}
    for back in range(FIXTURE_DAYS, 0, -1):
        day = FIXTURE_END - timedelta(days=back)
        orders = synthetic._orders_for(day, profile, rng)
        if orders == 0 and day.weekday() in synthetic.PROFILES[profile][3]:
            continue  # closed that weekday: no row, as a POS would have none
        if day.weekday() == 5 and day.isocalendar()[1] % 2 == 0:
            orders = round(orders * 1.5)
            drivers[day] = [Driver("local_event", "large")]
        history[day] = float(orders)
    return history, drivers


def report(history: History, drivers: Drivers, end: date, weeks: int = BACKTEST_WEEKS) -> dict:
    start = end - timedelta(days=7 * weeks)
    out = {}
    for name, model in forecasting.STAGES.items():
        score, _ = forecasting.backtest(model(), history, drivers, start, end - timedelta(days=1))
        out[name] = score.as_dict()
    out["chosen"] = forecasting.choose_stage(history, drivers, end)[0]
    return out


def fixtures_report() -> dict[str, dict]:
    return {profile: report(*fixture(profile), FIXTURE_END) for profile in ("steady", "seasonal", "sparse")}


def _print(name: str, result: dict) -> None:
    print(f"\n{name}  (stage in use: {result['chosen']})")
    print(f"  {'stage':<10} {'days':>5} {'average miss':>13} {'pinball':>9} {'in range':>9}")
    for stage in forecasting.STAGES:
        r = result[stage]
        pct = lambda v: "     n/a" if v is None else f"{v * 100:7.1f}%"  # noqa: E731
        print(f"  {stage:<10} {r['days']:>5} {pct(r['wape']):>13} {('n/a' if r['pinball'] is None else format(r['pinball'], '.2f')):>9} {pct(r['coverage']):>9}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay forecast history week by week and score each stage.")
    parser.add_argument("--restaurant", help="a restaurant id: backtest its own daily item totals")
    args = parser.parse_args(argv)
    if args.restaurant:
        from app.db.session import SessionLocal
        from app.margin_labor import demand, menu_costs

        db = SessionLocal()
        try:
            today = menu_costs.load(db, args.restaurant).today
            sales = demand.Sales(db, args.restaurant, today)
            drivers = demand.drivers_for(db, args.restaurant, today - timedelta(days=demand.HISTORY_DAYS), today)
            _print(f"restaurant {args.restaurant}, {sales.weeks} weeks of sales", report(sales.total, drivers, today, min(BACKTEST_WEEKS, max(1, sales.weeks - 4))))
        finally:
            db.close()
        return 0
    for profile, result in fixtures_report().items():
        _print(f"{profile} fixture, {BACKTEST_WEEKS} weeks", result)
    print("\nRange target: 80% of days inside the Likely range. Stage 3 (the quantile model) is not built.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
