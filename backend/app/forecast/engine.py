"""Baseline demand forecasting, and how it did.

For each POS item with at least four weeks of history: the predicted quantity for a day is the
weighted mean of the same weekday over the last six weeks (weights 6 for last week down to 1)
times a trend factor (the last two weeks' daily mean over the prior four weeks'), with a band of
one median absolute deviation either side. Items with less history are skipped. When actuals
arrive, the error is written and the digest shows last week's accuracy as one line. No weather,
holidays or reservations: those are a later model behind a flag.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.forecast.models import Forecast, ForecastError
from app.sales.models import SalesItem

MODEL = "weekday-weighted-v1"
WEEKS = 6
MIN_WEEKS = 4
HORIZON_DAYS = 7


def _history(db: Session, restaurant_id: str, today: date) -> dict[str, tuple[str, dict[date, float]]]:
    since = today - timedelta(days=7 * WEEKS + 1)
    out: dict[str, tuple[str, dict[date, float]]] = {}
    rows = (db.query(SalesItem.pos_item_id, SalesItem.pos_item_name, SalesItem.date, func.sum(SalesItem.qty))
            .filter(SalesItem.restaurant_id == restaurant_id, SalesItem.date > since, SalesItem.date <= today)
            .group_by(SalesItem.pos_item_id, SalesItem.pos_item_name, SalesItem.date).all())
    for pos_id, name, day, qty in rows:
        out.setdefault(pos_id, (name, {}))[1][day] = float(qty or 0)
    return out


def predict(history: dict[date, float], target: date, today: date) -> tuple[float, float, float] | None:
    """(pred, lo, hi) for `target` from `history` up to `today`; None with fewer than four same-weekday weeks."""
    same = []
    for back in range(1, WEEKS + 1):
        day = target - timedelta(days=7 * back)
        if day > today:
            continue
        if day in history:
            same.append((back, history[day]))
    if len(same) < MIN_WEEKS:
        return None
    weighted = sum((WEEKS + 1 - back) * qty for back, qty in same) / sum(WEEKS + 1 - back for back, _ in same)
    # Yesterday back two weeks, against the four weeks before that; today's sales aren't in yet.
    recent = [history.get(today - timedelta(days=i), 0.0) for i in range(1, 15)]
    prior = [history.get(today - timedelta(days=i), 0.0) for i in range(15, 43)]
    recent_mean = sum(recent) / 14
    prior_mean = sum(prior) / 28
    trend = (recent_mean / prior_mean) if prior_mean > 0 else 1.0
    trend = max(0.5, min(1.5, trend))
    pred = round(weighted * trend, 2)
    values = [qty for _, qty in same]
    median = statistics.median(values)
    mad = statistics.median(abs(v - median) for v in values) if len(values) > 1 else 0.0
    return pred, round(max(pred - mad, 0), 2), round(pred + mad, 2)


def run(db: Session, restaurant_id: str, today: date | None = None, horizon: int = HORIZON_DAYS) -> dict:
    """Forecast the next `horizon` days for every item with enough history, and backfill errors for days with actuals."""
    today = today or date.today()
    history = _history(db, restaurant_id, today)
    written = 0
    for pos_id, (name, days) in history.items():
        for ahead in range(1, horizon + 1):
            target = today + timedelta(days=ahead)
            out = predict(days, target, today)
            if out is None:
                continue
            pred, lo, hi = out
            row = db.query(Forecast).filter(Forecast.restaurant_id == restaurant_id, Forecast.pos_item_id == pos_id, Forecast.date == target).first()
            if row is None:
                row = Forecast(restaurant_id=restaurant_id, pos_item_id=pos_id, pos_item_name=name, date=target, qty_pred=pred, qty_lo=lo, qty_hi=hi)
                db.add(row)
            else:
                row.qty_pred, row.qty_lo, row.qty_hi, row.pos_item_name, row.model = pred, lo, hi, name, MODEL
            written += 1
    errors = backfill_errors(db, restaurant_id, today)
    db.flush()
    from app.platform import events

    events.emit(db, restaurant_id, "forecast.computed", "forecasts", today.isoformat(), {"written": written, "errors": errors})
    return {"forecasts": written, "errors": errors}


def backfill_errors(db: Session, restaurant_id: str, today: date) -> int:
    """For each past forecast day that now has an actual, the absolute percentage error."""
    actuals: dict[tuple[str, date], float] = {}
    since = today - timedelta(days=28)
    for pos_id, day, qty in (db.query(SalesItem.pos_item_id, SalesItem.date, func.sum(SalesItem.qty))
                             .filter(SalesItem.restaurant_id == restaurant_id, SalesItem.date > since, SalesItem.date <= today)
                             .group_by(SalesItem.pos_item_id, SalesItem.date)):
        actuals[(pos_id, day)] = float(qty or 0)
    written = 0
    for forecast in db.query(Forecast).filter(Forecast.restaurant_id == restaurant_id, Forecast.date > since, Forecast.date <= today):
        actual = actuals.get((forecast.pos_item_id, forecast.date))
        if actual is None:
            continue
        row = db.query(ForecastError).filter(ForecastError.restaurant_id == restaurant_id, ForecastError.pos_item_id == forecast.pos_item_id,
                                             ForecastError.date == forecast.date).first()
        if row is None:
            row = ForecastError(restaurant_id=restaurant_id, pos_item_id=forecast.pos_item_id, date=forecast.date, qty_pred=forecast.qty_pred, qty_actual=actual)
            db.add(row)
        row.qty_pred, row.qty_actual = forecast.qty_pred, actual
        row.abs_pct_err = round(abs(forecast.qty_pred - actual) / actual * 100, 2) if actual > 0 else None
        written += 1
    db.flush()
    return written


def accuracy_last_week(db: Session, restaurant_id: str | None, today: date | None = None) -> float | None:
    """100 minus last week's mean absolute percentage error, or None with no errors yet."""
    from app.prices.engine import week_start

    if restaurant_id is None:
        return None
    today = today or date.today()
    start = week_start(today) - timedelta(days=7)
    end = start + timedelta(days=6)
    errors = [e.abs_pct_err for e in db.query(ForecastError).filter(ForecastError.restaurant_id == restaurant_id, ForecastError.date >= start,
                                                                     ForecastError.date <= end) if e.abs_pct_err is not None]
    if not errors:
        return None
    return round(max(0.0, 100 - sum(errors) / len(errors)), 1)
