import json

from anthropic import beta_tool
from sqlalchemy.orm import Session

from app.agents import crud as agent_crud
from app.agents.models import AgentRun
from app.restaurant import crud as restaurant_crud


def build_tools(db: Session, run: AgentRun, agent_definition_id: str) -> list:
    @beta_tool
    def list_suppliers() -> str:
        """List all suppliers with their lead times."""
        suppliers = restaurant_crud.list_suppliers(db)
        return json.dumps(
            [{"id": s.id, "name": s.name, "lead_time_days": s.lead_time_days} for s in suppliers]
        )

    @beta_tool
    def get_items_by_supplier(supplier_id: str) -> str:
        """List inventory items sourced from a given supplier, with current stock.

        Args:
            supplier_id: The id of the supplier to look up.
        """
        supplier = restaurant_crud.get_supplier(db, supplier_id)
        if not supplier:
            return f"Error: no supplier with id {supplier_id}"
        items = [i for i in restaurant_crud.list_inventory_items(db) if i.supplier_id == supplier_id]
        return json.dumps(
            [{"id": i.id, "name": i.name, "quantity_on_hand": i.quantity_on_hand, "unit": i.unit} for i in items]
        )

    @beta_tool
    def draft_purchase_order(supplier_id: str, inventory_item_id: str, quantity: float, note: str = "") -> str:
        """Draft a purchase order to a supplier for an inventory item. Queues
        it for operator approval rather than sending a real order.

        Args:
            supplier_id: The id of the supplier to order from.
            inventory_item_id: The id of the inventory item being ordered.
            quantity: The quantity to order, in the item's unit.
            note: Optional note with reasoning (e.g. lead time, upcoming demand).
        """
        supplier = restaurant_crud.get_supplier(db, supplier_id)
        item = restaurant_crud.get_inventory_item(db, inventory_item_id)
        if not supplier or not item:
            return "Error: supplier or inventory item not found"
        action = agent_crud.create_action(
            db,
            run_id=run.id,
            agent_definition_id=agent_definition_id,
            action_type="purchase_order",
            payload={
                "supplier_id": supplier.id,
                "supplier_name": supplier.name,
                "inventory_item_id": item.id,
                "item_name": item.name,
                "quantity": quantity,
                "note": note,
            },
        )
        return f"Purchase order draft {action.id} for {quantity} {item.unit} of '{item.name}' from '{supplier.name}' submitted for operator approval."

    return [list_suppliers, get_items_by_supplier, draft_purchase_order]
