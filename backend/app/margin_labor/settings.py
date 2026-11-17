"""A restaurant's settings for these modules, with the spec's starting defaults where none are saved.

Every threshold and weight in the spec is a setting, not a constant: `value(db, restaurant_id, "name")` reads
the owner's override, else the default below. None is an industry benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.margin_labor.models import RestaurantSettings
from app.restaurant.models import Restaurant

#: The spec's starting values, by name. Changed per restaurant through `RestaurantSettings.overrides`.
DEFAULTS: dict[str, object] = {
    "margin_alert_over_target": "0.02",       # a dish is flagged at target + 2 points
    "margin_alert_close_over_target": "0.01",  # and cleared below target + 1 point
    "margin_min_units_28d": 10,
    "reprice_cap_pct": "0.10",
    "usage_min_weekly_dollars": "25",
    "usage_min_pct_of_expected": "0.05",
    "usage_data_gap_pct": "0.85",
    "coverage_learning": "0.80",
    "coverage_live": "0.90",
}


@dataclass(frozen=True)
class Settings:
    timezone: str
    business_day_start: str
    target_food_cost_pct: Decimal
    target_labor_pct: Decimal | None
    price_ending: Decimal
    costing_price_policy: str
    overrides: dict


def for_restaurant(db: Session, restaurant_id: str) -> Settings:
    restaurant = db.get(Restaurant, restaurant_id)
    row = db.query(RestaurantSettings).filter(RestaurantSettings.restaurant_id == restaurant_id).first()
    tz = (restaurant.timezone if restaurant else None) or "UTC"
    if row is None:
        return Settings(tz, "04:00", Decimal("0.30"), None, Decimal("0.00"), "latest", {})
    return Settings(tz, row.business_day_start, row.target_food_cost_pct, row.target_labor_pct, row.price_ending,
                    row.costing_price_policy, dict(row.overrides or {}))


def value(db: Session, restaurant_id: str, name: str):
    """One threshold or weight: the restaurant's own, else the spec's default. Numbers given as text are decimals."""
    held = for_restaurant(db, restaurant_id).overrides.get(name, DEFAULTS[name])
    return Decimal(held) if isinstance(held, str) else held
