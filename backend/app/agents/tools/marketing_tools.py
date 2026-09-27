import json

from anthropic import beta_tool
from sqlalchemy.orm import Session

from app.agents import crud as agent_crud
from app.agents.models import AgentRun
from app.restaurant import crud as restaurant_crud


def build_tools(db: Session, run: AgentRun, agent_definition_id: str) -> list:
    @beta_tool
    def get_top_selling_items(limit: int = 5) -> str:
        """List the best-selling menu items by total quantity sold.

        Args:
            limit: How many items to return. Defaults to 5.
        """
        ranked = restaurant_crud.top_selling_items(db, limit=limit)
        return json.dumps([{"id": m.id, "name": m.name, "qty_sold": qty} for m, qty in ranked])

    @beta_tool
    def get_slow_moving_items(limit: int = 5) -> str:
        """List the worst-selling menu items by total quantity sold - good
        candidates for a promotion or menu removal.

        Args:
            limit: How many items to return. Defaults to 5.
        """
        ranked = restaurant_crud.slow_moving_items(db, limit=limit)
        return json.dumps([{"id": m.id, "name": m.name, "qty_sold": qty} for m, qty in ranked])

    @beta_tool
    def draft_menu_change_proposal(
        menu_item_id: str, change_type: str, details: str, new_price: float | None = None
    ) -> str:
        """Propose a menu change (e.g. a promotion, price change, or item
        removal). Queues the proposal for operator approval rather than
        editing the live menu.

        Args:
            menu_item_id: The id of the menu item this proposal concerns.
            change_type: One of "promotion", "price_change", "remove", "description_update".
            details: A short description of the proposed change and why.
            new_price: Required when change_type is "price_change" - the exact new
                price. Approval applies this number directly, so a price stated only
                in `details` cannot be carried out.
        """
        items = restaurant_crud.list_menu_items(db)
        item = next((m for m in items if m.id == menu_item_id), None)
        if not item:
            return f"Error: no menu item with id {menu_item_id}"
        if change_type == "price_change" and new_price is None:
            return "Error: change_type 'price_change' requires new_price."
        action = agent_crud.create_action(
            db,
            run_id=run.id,
            agent_definition_id=agent_definition_id,
            action_type="menu_change",
            payload={
                "menu_item_id": item.id,
                "item_name": item.name,
                "change_type": change_type,
                "details": details,
                "new_price": new_price,
            },
        )
        return f"Menu change proposal {action.id} for '{item.name}' ({change_type}) submitted for operator approval."

    return [get_top_selling_items, get_slow_moving_items, draft_menu_change_proposal]
