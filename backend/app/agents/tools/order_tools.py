"""Purchase orders and prep sheets: the agent drafts, a person sends.

`propose_purchase_order` writes one `purchase_order` proposal for a supplier from eight weeks of
invoices (cap 3 a week); `prep_sheet` one `prep_sheet` proposal for a day from the forecast and
the recipes (cap 2). Neither can set an order to sent: that is a button on the approved order,
and the write guard refuses any tool that tries.
"""

from __future__ import annotations

import json
from datetime import date

from anthropic import beta_tool
from sqlalchemy.orm import Session

from app.agents.models import AgentRun
from app.restaurant.models import Supplier


def build_tools(db: Session, run: AgentRun, agent_definition_id: str) -> list:
    restaurant_id = run.restaurant_id

    @beta_tool
    def list_open_orders() -> str:
        """Orders drafted, sent or on their way: id, supplier, status, expected date, lines. Read-only."""
        from app.restaurant import orders

        out = []
        for o in orders.open_orders(db, restaurant_id):
            out.append({"id": o.id, "supplier": o.supplier_name, "status": o.status, "expected_on": o.expected_on.isoformat() if o.expected_on else None,
                        "lines": [{"description": l.description, "qty": l.qty, "unit": l.unit} for l in o.lines] or [{"description": o.item_name, "qty": o.quantity, "unit": o.unit}]})
        return json.dumps(out)

    @beta_tool
    def propose_purchase_order(supplier_id: str) -> str:
        """Draft this week's order to a supplier from the last eight weeks of invoices: median weekly quantity per
        item at the latest price, with the invoice lines cited. A proposal for the owner; sending is theirs.

        Args:
            supplier_id: The supplier, from list_suppliers.
        """
        from app.restaurant import po_draft

        supplier = db.get(Supplier, supplier_id)
        if supplier is None or supplier.restaurant_id != restaurant_id:
            return "Error: no such supplier for this restaurant."
        action = po_draft.propose(db, restaurant_id, supplier, run=run)
        db.commit()
        if action is None:
            return "Held: fewer than four invoices from this supplier in eight weeks, or the weekly cap of three order drafts is reached."
        return f"Order draft {action.id} for {supplier.name} submitted: {action.payload['note'][:300]}"

    @beta_tool
    def prep_sheet(for_date: str) -> str:
        """Propose a prep sheet for a day: forecast quantity times each recipe's lines, per ingredient, citing the
        forecasts and recipes used. Nothing is subtracted for stock on hand; there are no counts here.

        Args:
            for_date: YYYY-MM-DD, tomorrow or the day after.
        """
        from app.forecast import prep

        try:
            day = date.fromisoformat(for_date.strip())
        except ValueError:
            return "Error: for_date must be YYYY-MM-DD."
        action = prep.propose(db, restaurant_id, day, run=run)
        db.commit()
        if action is None:
            return "Held: no forecast and confirmed recipes for that day, or the weekly cap of two prep sheets is reached."
        return f"Prep sheet proposal {action.id} for {day.isoformat()}: {len(action.payload['lines'])} ingredients."

    return [list_open_orders, propose_purchase_order, prep_sheet]
