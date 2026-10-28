"""The weekly prime-cost P&L.

For a week (Monday to Sunday): purchases by category from the week's confirmed invoice lines
(the item's category: food and dry goods, paper, beverages), sales from `sales_days`, labor
from `labor_days`. Food, paper, labor and prime cost as a share of net sales. A week with any
day missing sales or labor is written with nulls, and the digest says what to connect rather
than show a wrong number.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.prices.engine import COMPLETE, week_start
from app.prices.models import Invoice, InvoiceLine, PriceItem
from app.sales.models import LaborDay, PnlWeek, SalesDay

FOOD = ("food", "dry")
PAPER = ("paper",)
BEV = ("beer", "wine", "liquor")


def last_complete_week(today: date | None = None) -> date:
    """The Monday of the most recent whole week: what Monday's digest reports on."""
    today = today or date.today()
    return week_start(today) - timedelta(days=7)


def compute_week(db: Session, restaurant_id: str, week_of: date) -> PnlWeek:
    week_of = week_start(week_of)
    end = week_of + timedelta(days=6)
    purchases = {"food": 0.0, "paper": 0.0, "bev": 0.0}
    rows = (
        db.query(InvoiceLine.extended, InvoiceLine.credited, PriceItem.category)
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .outerjoin(PriceItem, PriceItem.id == InvoiceLine.item_id)
        .filter(Invoice.restaurant_id == restaurant_id, Invoice.status == COMPLETE,
                Invoice.invoice_date >= week_of, Invoice.invoice_date <= end)
    )
    for extended, credited, category in rows:
        amount = float(extended or 0) - float(credited or 0)
        if category in PAPER:
            purchases["paper"] += amount
        elif category in BEV:
            purchases["bev"] += amount
        else:
            purchases["food"] += amount  # food, dry, and lines not yet matched to an item
    days = [week_of + timedelta(days=i) for i in range(7)]
    sales = {}
    for row in db.query(SalesDay).filter(SalesDay.restaurant_id == restaurant_id, SalesDay.date >= week_of, SalesDay.date <= end):
        sales[row.date] = sales.get(row.date, 0.0) + float(row.net_sales)
    labor = {}
    for row in db.query(LaborDay).filter(LaborDay.restaurant_id == restaurant_id, LaborDay.date >= week_of, LaborDay.date <= end):
        labor[row.date] = labor.get(row.date, 0.0) + float(row.dollars)
    missing = {
        "sales": [d.isoformat() for d in days if d not in sales],
        "labor": [d.isoformat() for d in days if d not in labor],
    }
    sales_net = round(sum(sales.values()), 2) if not missing["sales"] else None
    labor_dollars = round(sum(labor.values()), 2) if not missing["labor"] else None

    row = db.query(PnlWeek).filter(PnlWeek.restaurant_id == restaurant_id, PnlWeek.week_of == week_of).first()
    if row is None:
        row = PnlWeek(restaurant_id=restaurant_id, week_of=week_of)
        db.add(row)
    row.purchases_food = round(purchases["food"], 2)
    row.purchases_paper = round(purchases["paper"], 2)
    row.purchases_bev = round(purchases["bev"], 2)
    row.sales_net = sales_net
    row.labor_dollars = labor_dollars
    row.missing = missing
    if sales_net and sales_net > 0:
        row.food_pct = round(100 * (purchases["food"] + purchases["bev"]) / sales_net, 2)
        row.paper_pct = round(100 * purchases["paper"] / sales_net, 2)
        row.labor_pct = round(100 * labor_dollars / sales_net, 2) if labor_dollars is not None else None
        row.prime_pct = (round(100 * (purchases["food"] + purchases["bev"] + purchases["paper"] + labor_dollars) / sales_net, 2)
                         if labor_dollars is not None else None)
    else:
        row.food_pct = row.paper_pct = row.labor_pct = row.prime_pct = None
    db.flush()
    from app.platform import events

    events.emit(db, restaurant_id, "pnl.computed", "pnl_weeks", row.id,
                {"week_of": week_of.isoformat(), "prime_pct": row.prime_pct, "complete": row.prime_pct is not None})
    return row


def prime_line(db: Session, restaurant_id: str | None, today: date | None = None) -> dict | None:
    """What the digest shows at the top, for the last complete week. None when the phase is off."""
    from app.platform import flags

    if restaurant_id is None or not flags.enabled(db, restaurant_id, flags.V2_CONNECT):
        return None
    week_of = last_complete_week(today)
    row = db.query(PnlWeek).filter(PnlWeek.restaurant_id == restaurant_id, PnlWeek.week_of == week_of).first()
    if row is None:
        row = compute_week(db, restaurant_id, week_of)
    if row.prime_pct is not None:
        return {
            "week_of": week_of, "complete": True, "prime_pct": row.prime_pct, "food_pct": row.food_pct,
            "paper_pct": row.paper_pct, "labor_pct": row.labor_pct, "sales_net": row.sales_net,
            "labor_dollars": row.labor_dollars, "purchases": round(row.purchases_food + row.purchases_paper + row.purchases_bev, 2),
            "prompt": None,
        }
    missing = row.missing or {}
    if missing.get("sales") and missing.get("labor"):
        prompt = "Connect your POS and payroll to see prime cost."
    elif missing.get("sales"):
        prompt = "Connect your POS to see food cost %."
    elif missing.get("labor"):
        prompt = "Connect payroll to see labor and prime cost."
    else:
        prompt = "No sales recorded last week, so there is no food cost % to show."
    return {"week_of": week_of, "complete": False, "prime_pct": None, "food_pct": row.food_pct, "paper_pct": row.paper_pct,
            "labor_pct": row.labor_pct, "sales_net": row.sales_net, "labor_dollars": row.labor_dollars,
            "purchases": round(row.purchases_food + row.purchases_paper + row.purchases_bev, 2), "prompt": prompt}
