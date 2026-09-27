"""Extra state for browser tests, layered on top of seed_data.py.

Real agents on a real model are nondeterministic, so the end-to-end tests
don't wait for one to propose something. Instead this plants the
situations the UI has to handle -- a stock shortage with a reorder waiting,
and a promotion proposed without a price -- so every test starts from a
known queue.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.models import AgentAction, AgentDefinition, AgentRun  # noqa: E402
from app.bootstrap import setup  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.restaurant.models import InventoryItem, MenuItem  # noqa: E402


def _proposal(db, agent_key: str, action_type: str, payload: dict) -> None:
    agent = db.query(AgentDefinition).filter(AgentDefinition.key == agent_key).one()
    run = AgentRun(agent_definition_id=agent.id, status="succeeded", trigger_type="manual",
                   input="Seeded for end-to-end tests", output_summary="Seeded proposal.")
    db.add(run)
    db.flush()
    db.add(AgentAction(run_id=run.id, agent_definition_id=agent.id, action_type=action_type, payload=payload))


def seed() -> None:
    setup()
    db = SessionLocal()
    try:
        if db.query(AgentAction).count():
            print("E2E proposals already present, skipping")
            return

        tomatoes = db.query(InventoryItem).filter(InventoryItem.name == "Tomatoes").one()
        tomatoes.quantity_on_hand = 3
        db.add(tomatoes)

        _proposal(db, "inventory", "inventory_reorder", {
            "inventory_item_id": tomatoes.id, "item_name": "Tomatoes", "quantity": 25,
            "note": "Below the reorder point.",
        })

        bread = db.query(MenuItem).filter(MenuItem.name == "Garlic Bread").one()
        _proposal(db, "marketing", "menu_change", {
            "menu_item_id": bread.id, "item_name": "Garlic Bread", "change_type": "promotion",
            "details": "Buy one get one free to lift a slow seller.", "new_price": None,
        })

        db.commit()
        print("Seeded E2E proposals")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
