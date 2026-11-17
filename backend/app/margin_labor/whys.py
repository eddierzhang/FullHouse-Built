"""`GET /why/{table}/{row_id}`: the stored basis of a derived row, for the Why panel. One explainer per table;
each reads the row's `basis` and never recomputes. A row of another restaurant is not found."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.margin_labor.models import DishCost, Finding
from app.platform import flags


def explain(db: Session, restaurant_id: str, table: str, row_id: str, figure: str, user) -> dict | None:
    from app.margin_labor import menu_costs_read

    if table == "dish_costs" and flags.enabled(db, restaurant_id, flags.ML_MENU_COSTS):
        row = db.get(DishCost, row_id)
        return menu_costs_read.why_dish_cost(db, row, figure) if row is not None and row.restaurant_id == restaurant_id else None
    if table == "findings":
        row = db.get(Finding, row_id)
        return menu_costs_read.why_finding(db, row) if row is not None and row.restaurant_id == restaurant_id else None
    return None
