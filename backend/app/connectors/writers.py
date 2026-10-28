"""Writing what connectors deliver into the day tables, once: each row upserts on its unique key."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.connectors.models import Connection
from app.sales.models import LaborDay, SalesDay, SalesItem


def _date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def write_sales_days(db: Session, restaurant_id: str, rows: list[dict], connection: Connection | None) -> int:
    written = 0
    for raw in rows:
        day, source = _date(raw["date"]), raw.get("source", "csv")
        row = db.query(SalesDay).filter(SalesDay.restaurant_id == restaurant_id, SalesDay.date == day, SalesDay.source == source).first()
        if row is None:
            row = SalesDay(restaurant_id=restaurant_id, date=day, source=source, net_sales=0)
            db.add(row)
        row.net_sales = round(float(raw["net_sales"]), 2)
        row.gross_sales = round(float(raw["gross_sales"]), 2) if raw.get("gross_sales") is not None else row.gross_sales
        row.covers = int(raw["covers"]) if raw.get("covers") is not None else row.covers
        row.tax = round(float(raw["tax"]), 2) if raw.get("tax") is not None else row.tax
        row.tips = round(float(raw["tips"]), 2) if raw.get("tips") is not None else row.tips
        row.connection_id = connection.id if connection else row.connection_id
        written += 1
    db.flush()
    return written


def write_sales_items(db: Session, restaurant_id: str, rows: list[dict], connection: Connection | None) -> int:
    written = 0
    for raw in rows:
        day, source = _date(raw["date"]), raw.get("source", "csv")
        item_id = str(raw.get("pos_item_id") or raw["pos_item_name"])[:120]
        row = (
            db.query(SalesItem).filter(SalesItem.restaurant_id == restaurant_id, SalesItem.date == day,
                                       SalesItem.pos_item_id == item_id, SalesItem.source == source).first()
        )
        if row is None:
            row = SalesItem(restaurant_id=restaurant_id, date=day, pos_item_id=item_id, source=source,
                            pos_item_name=str(raw["pos_item_name"])[:200], qty=0, net=0)
            db.add(row)
        row.pos_item_name = str(raw["pos_item_name"])[:200]
        row.qty = round(float(raw["qty"]), 4)
        row.net = round(float(raw["net"]), 2)
        row.connection_id = connection.id if connection else row.connection_id
        written += 1
    db.flush()
    return written


def write_labor_days(db: Session, restaurant_id: str, rows: list[dict], connection: Connection | None) -> int:
    written = 0
    for raw in rows:
        day, source = _date(raw["date"]), raw.get("source", "csv")
        row = db.query(LaborDay).filter(LaborDay.restaurant_id == restaurant_id, LaborDay.date == day, LaborDay.source == source).first()
        if row is None:
            row = LaborDay(restaurant_id=restaurant_id, date=day, source=source, hours=0, dollars=0)
            db.add(row)
        row.hours = round(float(raw["hours"]), 2)
        row.dollars = round(float(raw["dollars"]), 2)
        row.connection_id = connection.id if connection else row.connection_id
        written += 1
    db.flush()
    return written


def days_touched(rows: list[dict]) -> set[date]:
    return {_date(r["date"]) for r in rows if r.get("date")}
