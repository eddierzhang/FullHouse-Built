import json

from anthropic import beta_tool
from sqlalchemy.orm import Session

from app.agents import crud as agent_crud
from app.agents.models import AgentRun
from app.restaurant import crud as restaurant_crud


def build_tools(db: Session, run: AgentRun, agent_definition_id: str) -> list:
    @beta_tool
    def list_low_stock_items() -> str:
        """List inventory items at or below their reorder threshold.

        Returns a JSON array of items needing attention, each with id, name,
        quantity_on_hand, reorder_threshold, reorder_qty, unit, and supplier_id.
        """
        items = restaurant_crud.get_low_stock_items(db)
        return json.dumps(
            [
                {
                    "id": i.id,
                    "name": i.name,
                    "quantity_on_hand": i.quantity_on_hand,
                    "reorder_threshold": i.reorder_threshold,
                    "reorder_qty": i.reorder_qty,
                    "unit": i.unit,
                    "supplier_id": i.supplier_id,
                }
                for i in items
            ]
        )

    @beta_tool
    def list_all_inventory() -> str:
        """List every inventory item with its current stock level."""
        items = restaurant_crud.list_inventory_items(db)
        return json.dumps(
            [
                {"id": i.id, "name": i.name, "quantity_on_hand": i.quantity_on_hand, "unit": i.unit}
                for i in items
            ]
        )

    @beta_tool
    def propose_reorder(inventory_item_id: str, quantity: float, note: str = "") -> str:
        """Propose reordering an inventory item. Queues the proposal for
        operator approval rather than placing a real order.

        Args:
            inventory_item_id: The id of the inventory item to reorder.
            quantity: The quantity to reorder, in the item's unit.
            note: Optional note explaining why this reorder is recommended.
        """
        item = restaurant_crud.get_inventory_item(db, inventory_item_id)
        if not item:
            return f"Error: no inventory item with id {inventory_item_id}"
        action = agent_crud.create_action(
            db,
            run_id=run.id,
            agent_definition_id=agent_definition_id,
            action_type="inventory_reorder",
            payload={"inventory_item_id": item.id, "item_name": item.name, "quantity": quantity, "note": note},
        )
        return f"Reorder proposal {action.id} for {quantity} {item.unit} of '{item.name}' submitted for operator approval."

    return [list_low_stock_items, list_all_inventory, propose_reorder]
