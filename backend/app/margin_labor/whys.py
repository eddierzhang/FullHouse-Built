"""`GET /why/{table}/{row_id}`: the stored basis of a derived row, for the Why panel. One explainer per table;
each reads the row's `basis` and never recomputes. A row of another restaurant is not found."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.margin_labor.models import DishCost, Finding, UsageVariance
from app.platform import flags


def explain(db: Session, restaurant_id: str, table: str, row_id: str, figure: str, user) -> dict | None:
    from app.margin_labor import menu_costs_read

    if table == "dish_costs" and flags.enabled(db, restaurant_id, flags.ML_MENU_COSTS):
        row = db.get(DishCost, row_id)
        return menu_costs_read.why_dish_cost(db, row, figure) if row is not None and row.restaurant_id == restaurant_id else None
    if table == "usage_variance" and flags.enabled(db, restaurant_id, flags.ML_USAGE):
        from app.margin_labor import price_points, usage_read
        from app.prices.models import PriceItem

        row = db.get(UsageVariance, row_id)
        if row is None or row.restaurant_id != restaurant_id:
            return None
        item = db.get(PriceItem, row.canonical_item_id)
        return usage_read.why_row(db, row, price_points.base_unit(item)[0] if item is not None else "each")
    if table == "findings":
        row = db.get(Finding, row_id)
        if row is None or row.restaurant_id != restaurant_id:
            return None
        if row.type in ("usage_variance", "usage_data_gap"):
            return explain(db, restaurant_id, "usage_variance", (row.basis or {}).get("row_id", ""), figure, user)
        return menu_costs_read.why_finding(db, row)
    return None
