"""Every proposal carries three plain sentences: the issue, what approving changes, and what the owner should know.

They are written from the payload (and a read-only lookup where it helps), the same for a model's proposal and
the rule-based assistant's, and they say what the applier would do without doing it.
"""

from app.agents.explain import explain
from app.agents.models import AgentAction, AgentDefinition, AgentRun
from app.restaurant.models import InventoryItem, MenuItem

KINDS = ["inventory_reorder", "purchase_order", "shift_change", "menu_change", "recipe", "agreed_price", "menu_reprice",
         "calendar_item", "prep_sheet"]


def _action(db, action_type, payload):
    definition = db.query(AgentDefinition).first()
    if definition is None:
        definition = AgentDefinition(key="inventory", name="Inventory", description="d", role="subagent", system_prompt="s")
        db.add(definition)
        db.commit()
    run = AgentRun(agent_definition_id=definition.id, status="succeeded")
    db.add(run)
    db.commit()
    action = AgentAction(run_id=run.id, agent_definition_id=definition.id, action_type=action_type, payload=payload)
    db.add(action)
    db.commit()
    return action


def test_a_reorder_says_the_stock_position_the_draft_and_that_nothing_is_sent(db):
    stock = InventoryItem(name="Chicken Breast", unit="lb", quantity_on_hand=8.0, reorder_threshold=20.0)
    db.add(stock)
    db.commit()
    action = _action(db, "purchase_order", {"inventory_item_id": stock.id, "item_name": "Chicken Breast", "quantity": 40, "packs": 1,
                                            "pack_unit": "case", "supplier_name": "Sysco Foods", "estimated_cost": 87.6, "priced_on": "2026-10-26"})
    e = explain(action, db)
    assert e.issue == "Chicken Breast is at 8 lb on hand against a reorder point of 20 lb."
    assert e.change.startswith("Approving drafts an order for 1 case (40 lb) of Chicken Breast from Sysco Foods, about $87.60.")
    assert "Nothing is sent" in e.change
    assert e.know.startswith("The price is Sysco Foods's from Oct 26")


def test_a_weekly_draft_with_lines_tells_the_owner_to_trim_before_approving(db):
    lines = [{"description": "Tomatoes", "qty": 2, "unit": "case", "unit_price_expected": 31.2}, {"description": "Romaine", "qty": 1, "unit": "case", "unit_price_expected": 28.1}]
    action = _action(db, "purchase_order", {"supplier_name": "Green Valley Farms", "lines": lines, "expected_total": 90.5,
                                            "note": "Your usual week from Green Valley Farms, from 7 invoices over the last 8 weeks: Tomatoes 2 case. About $90.50. Drafted, not sent."})
    e = explain(action, db)
    assert e.issue == "Your usual week from Green Valley Farms, from 7 invoices over the last 8 weeks: Tomatoes 2 case."
    assert "drafts one order of 2 lines to Green Valley Farms, about $90.50" in e.change
    assert "drop a line you don't need before approving" in e.know


def test_a_menu_promotion_names_the_current_price_and_the_pos_step(db):
    dish = MenuItem(name="Garlic Bread", price=6.0, category="Starters")
    db.add(dish)
    db.commit()
    action = _action(db, "menu_change", {"menu_item_id": dish.id, "item_name": "Garlic Bread", "change_type": "promotion", "new_price": 4.5,
                                         "promo_ends_at": "2026-11-02T00:00:00", "details": "Slow mover on weeknights."})
    e = explain(action, db)
    assert e.issue == "Slow mover on weeknights."
    assert e.change.startswith("Approving sets Garlic Bread from $6.00 to $4.50 until Nov 2.")
    assert "POS" in e.know


def test_every_kind_has_all_three_sentences_and_an_unknown_kind_is_honest(db):
    for kind in KINDS:
        e = explain(_action(db, kind, {"lines": [], "name": "x", "item_name": "x", "title": "x"}), db)
        assert e.issue and e.change and e.know, kind
        assert e.change.startswith("Approving"), kind
    e = explain(_action(db, "launch_rocket", {"note": "Because."}), db)
    assert e.issue == "Because." and "changes nothing" in e.change


def test_the_api_lists_the_explanation_with_each_action(client, db):
    from test_restaurant_setup import _set_up

    restaurant = _set_up(client, db)
    stock = InventoryItem(name="Basil", unit="bunch", quantity_on_hand=1.0, reorder_threshold=4.0, restaurant_id=restaurant["id"])
    db.add(stock)
    db.commit()
    action = _action(db, "inventory_reorder", {"inventory_item_id": stock.id, "item_name": "Basil", "quantity": 6, "note": "Pesto weekend."})
    action.restaurant_id = restaurant["id"]
    db.commit()
    (row,) = [a for a in client.get("/api/v1/agents/actions?status=pending").json() if a["id"] == action.id]
    assert row["explain"]["issue"] == "Basil is at 1 bunch on hand against a reorder point of 4 bunch. Pesto weekend."
    assert set(row["explain"]) == {"issue", "change", "know"}
