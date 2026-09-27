import json
from datetime import datetime, timedelta, timezone

from anthropic import beta_tool
from sqlalchemy.orm import Session

from app.agents.models import AgentRun
from app.restaurant import crud as restaurant_crud


def build_tools(db: Session, run: AgentRun, agent_definition_id: str) -> list:
    @beta_tool
    def get_revenue_summary(days: int = 7) -> str:
        """Summarize completed-order revenue over the last N days.

        Args:
            days: How many trailing days to summarize. Defaults to 7.
        """
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        orders = restaurant_crud.list_orders_between(db, start, end)
        total = sum(o.total for o in orders)
        return json.dumps(
            {
                "days": days,
                "order_count": len(orders),
                "total_revenue": round(total, 2),
                "average_order_value": round(total / len(orders), 2) if orders else 0,
            }
        )

    @beta_tool
    def get_menu_margins() -> str:
        """List every menu item's price, cost, and profit margin, sorted by
        margin percent ascending (worst margins first)."""
        items = restaurant_crud.list_menu_items(db)
        rows = []
        for item in items:
            margin = item.price - item.cost
            margin_pct = round((margin / item.price) * 100, 1) if item.price else 0
            rows.append(
                {"id": item.id, "name": item.name, "price": item.price, "cost": item.cost,
                 "margin": round(margin, 2), "margin_pct": margin_pct}
            )
        rows.sort(key=lambda r: r["margin_pct"])
        return json.dumps(rows)

    @beta_tool
    def get_inventory_value() -> str:
        """Return the total dollar value of inventory currently on hand
        (quantity_on_hand * unit_cost, summed across all items)."""
        items = restaurant_crud.list_inventory_items(db)
        total = sum(i.quantity_on_hand * i.unit_cost for i in items)
        return json.dumps({"total_inventory_value": round(total, 2), "item_count": len(items)})

    return [get_revenue_summary, get_menu_margins, get_inventory_value]
