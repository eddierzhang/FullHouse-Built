"""Seed sample restaurant data plus Maestro and its 5 specialist definitions.

Run from backend/ after applying migrations:
    alembic upgrade head
    python scripts/seed_data.py
"""
import os
import sys
from datetime import date, datetime, time, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.definitions import BOSS_DEF, SUBAGENT_DEFS  # noqa: E402
from app.agents.models import AgentDefinition  # noqa: E402
from app.bootstrap import setup  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.restaurant.models import (  # noqa: E402
    InventoryItem,
    MenuItem,
    Order,
    OrderItem,
    Shift,
    Staff,
    Supplier,
)


def seed():
    setup()  # register change tracking before writing anything
    db = SessionLocal()
    try:
        if db.query(AgentDefinition).count() == 0:
            for d in SUBAGENT_DEFS:
                db.add(AgentDefinition(role="subagent", model="claude-opus-5", enabled=True, **d))
            db.add(AgentDefinition(role="boss", model="claude-opus-5", enabled=True, **BOSS_DEF))
            db.commit()
            print("Seeded agent definitions: boss + 5 subagents")
        else:
            print("Agent definitions already present, skipping")

        if db.query(Supplier).count() == 0:
            sysco = Supplier(name="Sysco Foods", contact_info="orders@sysco-demo.com", lead_time_days=2)
            local_farm = Supplier(name="Green Valley Farms", contact_info="hello@greenvalley-demo.com", lead_time_days=1)
            db.add_all([sysco, local_farm])
            db.commit()

            inventory = [
                InventoryItem(name="Chicken Breast", unit="lb", quantity_on_hand=8, reorder_threshold=15,
                              reorder_qty=40, unit_cost=3.20, supplier_id=sysco.id),
                InventoryItem(name="Romaine Lettuce", unit="head", quantity_on_hand=6, reorder_threshold=10,
                              reorder_qty=30, unit_cost=1.10, supplier_id=local_farm.id),
                InventoryItem(name="Mozzarella Cheese", unit="lb", quantity_on_hand=25, reorder_threshold=10,
                              reorder_qty=20, unit_cost=4.50, supplier_id=sysco.id),
                InventoryItem(name="Tomatoes", unit="lb", quantity_on_hand=4, reorder_threshold=12,
                              reorder_qty=25, unit_cost=1.80, supplier_id=local_farm.id),
                InventoryItem(name="Flour", unit="lb", quantity_on_hand=50, reorder_threshold=20,
                              reorder_qty=50, unit_cost=0.60, supplier_id=sysco.id),
            ]
            db.add_all(inventory)
            db.commit()
            print("Seeded suppliers + inventory")
        else:
            print("Suppliers already present, skipping")

        if db.query(MenuItem).count() == 0:
            menu = [
                MenuItem(name="Margherita Pizza", category="Entree", price=14.00, cost=4.20),
                MenuItem(name="Caesar Salad", category="Salad", price=11.00, cost=2.80),
                MenuItem(name="Grilled Chicken Sandwich", category="Entree", price=13.50, cost=5.10),
                MenuItem(name="Garlic Bread", category="Appetizer", price=6.00, cost=1.10),
                MenuItem(name="Tiramisu", category="Dessert", price=8.00, cost=2.40),
            ]
            db.add_all(menu)
            db.commit()
            print("Seeded menu items")
        else:
            menu = list(db.query(MenuItem))
            print("Menu items already present, skipping")

        if db.query(Staff).count() == 0:
            staff = [
                Staff(name="Alex Rivera", role="Server", email="alex@example.com"),
                Staff(name="Jordan Lee", role="Line Cook", email="jordan@example.com"),
                Staff(name="Sam Patel", role="Host", email="sam@example.com"),
            ]
            db.add_all(staff)
            db.commit()

            today = date.today()
            shifts = [
                Shift(staff_id=staff[0].id, date=today + timedelta(days=1), start_time=time(17, 0), end_time=time(23, 0), role="Server"),
                Shift(staff_id=staff[1].id, date=today + timedelta(days=1), start_time=time(15, 0), end_time=time(23, 0), role="Line Cook"),
                Shift(staff_id=staff[2].id, date=today + timedelta(days=2), start_time=time(17, 0), end_time=time(22, 0), role="Host"),
            ]
            db.add_all(shifts)
            db.commit()
            print("Seeded staff + shifts")
        else:
            print("Staff already present, skipping")

        if db.query(Order).count() == 0:
            menu = list(db.query(MenuItem))
            now = datetime.now(timezone.utc)
            for days_ago in range(5):
                order = Order(status="completed", channel="dine_in", table_number=days_ago + 1,
                               created_at=now - timedelta(days=days_ago, hours=2))
                total = 0.0
                for item, qty in [(menu[0], 3), (menu[1], 2), (menu[4], 1)]:
                    order.items.append(OrderItem(menu_item_id=item.id, qty=qty, unit_price=item.price))
                    total += item.price * qty
                order.total = total
                db.add(order)
            db.commit()
            print("Seeded sample orders")
        else:
            print("Orders already present, skipping")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
