"""Extra state for browser tests, layered on top of seed_data.py.

Real agents on a real model are nondeterministic, so the end-to-end tests
don't wait for one to propose something. Instead this plants the
situations the UI has to handle -- a stock shortage with a reorder waiting,
and a promotion proposed without a price -- so every test starts from a
known queue.
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.definitions import AGENT_KEY  # noqa: E402
from app.agents.models import AgentAction, AgentDefinition, AgentRun  # noqa: E402
from app.auth.models import User  # noqa: E402
from app.auth.security import hash_password  # noqa: E402
from app.bootstrap import setup  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.demo import DEMO_PROFILE  # noqa: E402
from app.restaurant.models import InventoryItem, MenuItem, Restaurant  # noqa: E402


def _proposal(db, agent_key: str, action_type: str, payload: dict) -> None:
    agent = db.query(AgentDefinition).filter(AgentDefinition.key == agent_key).one()
    run = AgentRun(agent_definition_id=agent.id, status="succeeded", trigger_type="manual",
                   input="Seeded for end-to-end tests", output_summary="Seeded proposal.")
    db.add(run)
    db.flush()
    db.add(AgentAction(run_id=run.id, agent_definition_id=agent.id, action_type=action_type, payload=payload))


#: The account every browser test signs in as. Matches e2e/app.spec.ts.
E2E_EMAIL = "e2e@example.com"
E2E_PASSWORD = "e2e-password"


def seed() -> None:
    setup()
    db = SessionLocal()
    try:
        # E2E_FRESH_SITE=1 leaves the restaurant unset, for the test that
        # walks through setup from a brand-new owner's sign-up.
        restaurant = db.query(Restaurant).first()
        if restaurant is None and os.environ.get("E2E_FRESH_SITE") != "1":
            restaurant = Restaurant(**DEMO_PROFILE, setup_completed_at=datetime.now(timezone.utc))
            db.add(restaurant)
            db.flush()
            # The sample stock seed_data.py loaded is this restaurant's, as setup would make it.
            from app.restaurant.inventory import claim_unassigned

            claim_unassigned(db, restaurant)
        if not db.query(User).filter(User.email == E2E_EMAIL).first():
            db.add(User(email=E2E_EMAIL, name="E2E Manager", password_hash=hash_password(E2E_PASSWORD),
                        restaurant_id=restaurant.id if restaurant else None))
        db.commit()

        if db.query(AgentAction).count():
            print("E2E proposals already present, skipping")
            return

        tomatoes = db.query(InventoryItem).filter(InventoryItem.name == "Tomatoes").one()
        tomatoes.quantity_on_hand = 3
        db.add(tomatoes)

        _proposal(db, AGENT_KEY, "inventory_reorder", {
            "inventory_item_id": tomatoes.id, "item_name": "Tomatoes", "quantity": 25,
            "note": "Below the reorder point.",
        })

        bread = db.query(MenuItem).filter(MenuItem.name == "Garlic Bread").one()
        _proposal(db, AGENT_KEY, "menu_change", {
            "menu_item_id": bread.id, "item_name": "Garlic Bread", "change_type": "promotion",
            "details": "Buy one get one free to lift a slow seller.", "new_price": None,
        })

        db.commit()
        print("Seeded E2E proposals")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
