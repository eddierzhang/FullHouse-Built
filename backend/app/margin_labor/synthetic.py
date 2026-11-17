"""Synthetic history for tests, backtests and load checks: order-level sales and punches for a restaurant.

Three shapes, as the spec asks: `steady` (the same week again and again, with noise), `seasonal` (a strong
yearly swing and a weekend peak) and `sparse` (few orders, closed two days a week). The same seed gives the same
rows. Rows are written with bulk inserts, a restaurant-month at a time, so a large history is a set of small,
repeatable chunks.
"""

from __future__ import annotations

import math
import random
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.margin_labor import settings as ml_settings
from app.margin_labor.business import business_date, zone
from app.margin_labor.models import LaborPunch, SalesOrder, SalesOrderLine

MENU = [("BURGER", "Burger", "12.00", 30), ("FRIES", "Fries", "5.00", 24), ("SALAD", "House Salad", "9.00", 14),
        ("PASTA", "Rigatoni", "16.00", 12), ("WINGS", "Wings", "11.00", 10), ("SODA", "Soda", "3.00", 26), ("CAKE", "Chocolate Cake", "8.00", 6)]
PROFILES = {
    #: orders on a plain weekday, how much the weekend adds, the size of the yearly swing, weekdays closed
    "steady": (110, 0.35, 0.0, ()),
    "seasonal": (120, 0.60, 0.45, ()),
    "sparse": (18, 0.25, 0.10, (0, 1)),
}
CHANNEL_MIX = [("dine_in", None, 62), ("takeout", None, 20), ("delivery_platform", "DoorDash", 12), ("delivery_platform", "Uber Eats", 6)]
#: Lunch and dinner: (hour of the peak, spread in hours, share of the day)
DAYPARTS = [(12.5, 1.1, 0.38), (19.0, 1.5, 0.62)]
ROLES = [("server", 4), ("line_cook", 3), ("host", 1), ("dish", 1), ("bartender", 1)]


def _orders_for(day: date, profile: str, rng: random.Random) -> int:
    base, weekend, swing, closed = PROFILES[profile]
    if day.weekday() in closed:
        return 0
    level = base * (1 + weekend * (day.weekday() >= 4)) * (1 + swing * math.sin(2 * math.pi * day.timetuple().tm_yday / 365.0))
    return max(0, round(rng.gauss(level, level * 0.10)))


def generate(db: Session, restaurant_id: str, days: int, profile: str = "steady", seed: int = 1, today: date | None = None,
             punches: bool = True) -> dict:
    """Write `days` of history ending yesterday. Existing synthetic rows for the restaurant are not removed:
    external ids are derived from the seed and the day, so a second run with the same arguments collides on
    the unique keys instead of doubling. Callers generate into an empty restaurant."""
    today = today or date.today()
    conf = ml_settings.for_restaurant(db, restaurant_id)
    tz = zone(conf.timezone)
    rng = random.Random(f"{seed}:{profile}:{restaurant_id}")
    names, weights = [m for m in MENU], [m[3] for m in MENU]
    made_orders = made_lines = made_punches = 0
    now = datetime.now(timezone.utc)
    order_rows, line_rows, punch_rows = [], [], []

    def flush() -> None:
        nonlocal order_rows, line_rows, punch_rows
        if order_rows:
            db.execute(SalesOrder.__table__.insert(), order_rows)
        if line_rows:
            db.execute(SalesOrderLine.__table__.insert(), line_rows)
        if punch_rows:
            db.execute(LaborPunch.__table__.insert(), punch_rows)
        order_rows, line_rows, punch_rows = [], [], []

    for back in range(days, 0, -1):
        day = today - timedelta(days=back)
        count = _orders_for(day, profile, rng)
        for index in range(count):
            peak, spread, _share = rng.choices(DAYPARTS, [d[2] for d in DAYPARTS])[0]
            hour = min(23.5, max(10.0, rng.gauss(peak, spread)))
            opened = datetime.combine(day, time(int(hour), int(hour % 1 * 60)), tzinfo=tz).astimezone(timezone.utc)
            channel, platform, _w = rng.choices(CHANNEL_MIX, [c[2] for c in CHANNEL_MIX])[0]
            order_id = uuid.uuid4().hex
            net = Decimal(0)
            picked = rng.sample(names, k=rng.choice((1, 2, 2, 3))) if rng.random() < 0.5 else rng.choices(names, weights, k=rng.choice((1, 2, 3)))
            for line_no, (item_id, item_name, price, _weight) in enumerate(dict.fromkeys(picked), start=1):
                qty = Decimal(rng.choice((1, 1, 1, 2)))
                line_net = (Decimal(price) * qty).quantize(Decimal("0.0001"))
                net += line_net
                line_rows.append(dict(id=uuid.uuid4().hex, restaurant_id=restaurant_id, order_id=order_id, line_no=line_no,
                                      business_date=business_date(opened, conf.timezone, conf.business_day_start), pos_item_id=item_id,
                                      pos_item_name=item_name, qty=qty, net=line_net, modifiers=[], voided=False, voided_after_fire=False,
                                      fired_at=None, created_at=now, updated_at=now))
                made_lines += 1
            order_rows.append(dict(id=order_id, restaurant_id=restaurant_id, connection_key="synthetic", external_id=f"{seed}-{day.isoformat()}-{index}",
                                   opened_at=opened, closed_at=opened + timedelta(minutes=rng.randint(20, 75)),
                                   business_date=business_date(opened, conf.timezone, conf.business_day_start), channel=channel, platform=platform,
                                   covers=rng.choice((1, 2, 2, 3, 4)) if channel == "dine_in" else None, gross=net, discounts=Decimal(0), net=net,
                                   tax=(net * Decimal("0.08")).quantize(Decimal("0.0001")), tips=Decimal(0), status="closed", created_at=now, updated_at=now))
            made_orders += 1
        if punches and count:
            for role, heads in ROLES:
                for head in range(max(1, round(heads * count / PROFILES[profile][0]))):
                    start = datetime.combine(day, time(10 + (head % 2) * 6, 0), tzinfo=tz).astimezone(timezone.utc)
                    punch_rows.append(dict(id=uuid.uuid4().hex, restaurant_id=restaurant_id, employee_id=None, employee_name=f"{role.title()} {head + 1}",
                                           role_key=role, clock_in=start, clock_out=start + timedelta(hours=rng.choice((6, 7, 8))),
                                           business_date=day, source="csv", external_id=f"{seed}-{day.isoformat()}-{role}-{head}", created_at=now, updated_at=now))
                    made_punches += 1
        if day.day == 1 or len(order_rows) > 4000:  # a restaurant-month, or less, a chunk
            flush()
    flush()
    return {"orders": made_orders, "lines": made_lines, "punches": made_punches, "days": days, "profile": profile}
