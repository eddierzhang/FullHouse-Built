"""The public demo: a small Italian bistro with six weeks of trading.

`reset_demo_data` wipes every table and loads the same restaurant again,
with dates moved to end today. A public demo runs it at startup and on a
schedule, so whatever visitors approve, delete or revert is gone by the
next reset.

The data is shaped to give every page something true to show: eight weeks
of supplier invoices with a price that climbed, a cheaper supplier and a
short delivery (app/prices/demo.py), sales with a weekly rhythm (so the
forecast uses weekday means), recipes (so food cost and days of cover are
computed), ingredients running short, a supplier covering most of the
stock, a team member without shifts, and a queue of proposals -- one of
which is missing its price.

Rows are written with Core inserts, not the ORM, so loading the demo does
not bury the change history under a thousand "created" entries: the
history starts with the first thing a visitor changes.
"""

import logging
import random
import uuid
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy.orm import Session

from app.agents.definitions import AGENT_DEF
from app.agents.models import AgentAction, AgentDefinition, AgentEvent, AgentRun
from app.auth.models import User
from app.auth.security import hash_password
from app.db.session import Base
from app.customers.models import CustomerRequest
from app.restaurant.models import (
    InventoryAdjustment,
    InventoryItem,
    MenuItem,
    Order,
    OrderItem,
    RecipeItem,
    Restaurant,
    Shift,
    Staff,
    Supplier,
)

logger = logging.getLogger(__name__)

#: The account the demo's login page offers. Public on purpose: the demo
#: holds nothing but the sample restaurant, and resets daily.
DEMO_EMAIL = "demo@fullhouse.app"
DEMO_PASSWORD = "demo-bistro"
DEMO_NAME = "Demo Manager"

#: The demo restaurant's profile, as if its owner had finished setup.
DEMO_PROFILE = dict(
    name="Demo Restaurant",
    street="214 Mulberry Street",
    city="New York",
    region="NY",
    postal_code="10012",
    country="United States",
    phone="+1 212 555 0148",
    email="hello@demo-restaurant.example.com",
    website="https://demo-restaurant.example.com",
    timezone="America/New_York",
    opening_hours={
        **{day: {"closed": False, "open": "11:30", "close": "22:00"} for day in ("mon", "tue", "wed", "thu")},
        "fri": {"closed": False, "open": "11:30", "close": "23:00"},
        "sat": {"closed": False, "open": "11:30", "close": "23:00"},
        "sun": {"closed": False, "open": "12:00", "close": "21:00"},
    },
    inventory_settings={"safety_days": 1, "default_lead_time_days": 2, "daily_stock_check": "08:00"},
    staff_roles=["Head Chef", "Line Cook", "Server", "Host", "Bartender"],
    # The head chef works every night, so the weekly cap has room for her.
    scheduling_rules={"max_shift_hours": 10, "min_rest_hours": 10, "max_weekly_hours": 60, "min_staff_per_open_day": 3},
    completed_steps=["details", "hours", "inventory", "staff", "scheduling"],
)

HISTORY_DAYS = 42
#: Same restaurant on every reset; only the dates move.
RANDOM_SEED = 20260913

SUPPLIERS = [
    # name, contact, lead time (days)
    ("Sysco Foods", "orders@sysco.example.com", 2),
    ("Green Valley Farms", "hello@greenvalley.example.com", 1),
    ("Harbor Dairy Co.", "sales@harbordairy.example.com", 3),
    ("Empire Wine & Spirits", "orders@empirewine.example.com", 5),
]

CUSTOMER_REQUESTS = [
    # days ago, channel, kind, customer, contact, what they said
    (0, "in_person", "complaint", "Dana Whitfield", "555-0142",
     "We waited 45 minutes for two margherita pizzas on Friday night and nobody came to tell us why. "
     "The food was good when it arrived, but we nearly left."),
    (1, "phone", "request", "Marcus Lee", "marcus.lee@example.com",
     "Can you do a table for 14 on the 24th for my mother's 70th? Two of us are coeliac, so we'd need to "
     "know what on the menu is gluten free. Please call me back."),
    (1, "online_review", "complaint", None, None,
     "Tiramisu was still frozen in the middle. Second time this month. Three stars, down from five."),
    (2, "email", "question", "Priya Nair", "priya.nair@example.com",
     "Do you take bookings for the patio, and is it heated in the evenings?"),
    (3, "in_person", "compliment", "The Okafor family", None,
     "Best chicken parmesan we've had in the city. Our server Maria remembered our daughter's allergy "
     "without being asked. Thank you!"),
    (4, "phone", "complaint", "Tom Becker", "555-0177",
     "I was charged twice for a takeaway order on Tuesday, $38.50 each time. I'd like one of them refunded."),
]

INVENTORY = [
    # name, unit, on hand, reorder at, reorder qty, unit cost, supplier (None = unassigned)
    ("Flour", "lb", 62, 25, 50, 0.95, "Sysco Foods"),
    ("Mozzarella", "lb", 16, 12, 30, 6.80, "Harbor Dairy Co."),
    ("Tomatoes", "lb", 6, 15, 30, 2.90, "Green Valley Farms"),
    ("Basil", "bunch", 3, 8, 20, 2.40, "Green Valley Farms"),
    ("Romaine Lettuce", "head", 9, 10, 30, 2.10, "Green Valley Farms"),
    ("Parmesan", "lb", 7, 4, 10, 16.00, "Harbor Dairy Co."),
    ("Chicken Breast", "lb", 11, 15, 40, 5.40, "Sysco Foods"),
    ("Mascarpone", "lb", 5, 3, 8, 9.50, "Harbor Dairy Co."),
    ("Espresso Beans", "lb", 4, 2, 6, 18.00, "Sysco Foods"),
    ("Garlic", "lb", 3, 2, 5, 4.50, "Green Valley Farms"),
    ("Olive Oil", "L", 8, 4, 12, 12.00, "Sysco Foods"),
    ("Ciabatta", "loaf", 10, 12, 30, 3.60, None),
]

MENU = [
    # name, category, price, description, popularity weight, recipe [(ingredient, qty per dish)]
    ("Margherita Pizza", "Entree", 14.00, "San Marzano tomato, fresh mozzarella, basil.", 30,
     [("Flour", 0.5), ("Mozzarella", 0.3), ("Tomatoes", 0.25), ("Basil", 0.1), ("Olive Oil", 0.02)]),
    ("Chicken Pesto Pizza", "Entree", 16.00, "Grilled chicken, basil pesto, mozzarella.", 18,
     [("Flour", 0.5), ("Mozzarella", 0.3), ("Chicken Breast", 0.25), ("Basil", 0.15), ("Olive Oil", 0.03)]),
    ("Grilled Chicken Sandwich", "Entree", 13.50, "On toasted ciabatta with romaine and tomato.", 16,
     [("Chicken Breast", 0.4), ("Ciabatta", 0.5), ("Romaine Lettuce", 0.1), ("Tomatoes", 0.1)]),
    ("Caesar Salad", "Salad", 11.00, "Romaine, parmesan, ciabatta croutons.", 20,
     [("Romaine Lettuce", 0.5), ("Parmesan", 0.05), ("Ciabatta", 0.1), ("Olive Oil", 0.02)]),
    ("Caprese Salad", "Salad", 10.00, "Tomato, mozzarella, basil, olive oil.", 10,
     [("Tomatoes", 0.35), ("Mozzarella", 0.2), ("Basil", 0.1), ("Olive Oil", 0.02)]),
    ("Garlic Bread", "Appetizer", 6.00, "Ciabatta, roasted garlic butter.", 4,
     [("Ciabatta", 0.5), ("Garlic", 0.03), ("Olive Oil", 0.02)]),
    ("Tiramisu", "Dessert", 8.00, "Mascarpone, espresso, cocoa.", 12,
     [("Mascarpone", 0.15), ("Espresso Beans", 0.02)]),
    ("Affogato", "Dessert", 6.50, "Vanilla gelato drowned in espresso.", 3,
     [("Espresso Beans", 0.03)]),
]

STAFF = [
    ("Maria Gonzalez", "Head Chef"),
    ("Jordan Lee", "Line Cook"),
    ("Alex Rivera", "Server"),
    ("Chris Nguyen", "Server"),
    ("Sam Patel", "Host"),
    ("Taylor Brooks", "Bartender"),  # deliberately left off the schedule
]

#: Orders per weekday (Monday first): quiet early week, a Friday/Saturday rush.
ORDERS_PER_WEEKDAY = [14, 12, 16, 20, 30, 34, 24]
CHANNELS = [("dine_in", 70), ("takeout", 20), ("delivery", 10)]


def _id() -> str:
    return uuid.uuid4().hex


def _insert(db: Session, model, rows: list[dict]) -> None:
    """Bulk insert. Every row must name the same columns: an executemany
    takes its column list from the first row and silently drops the rest."""
    if not rows:
        return
    columns = set(rows[0])
    if any(set(row) != columns for row in rows):
        raise ValueError(f"{model.__tablename__} rows name different columns")
    db.execute(model.__table__.insert(), rows)


def wipe(db: Session) -> None:
    """Delete every row in every application table, children first."""
    for table in reversed(Base.metadata.sorted_tables):
        db.execute(table.delete())


def reset_demo_data(db: Session, now: datetime | None = None) -> dict:
    """Replace everything with the demo restaurant. Returns row counts."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    rng = random.Random(RANDOM_SEED)

    wipe(db)

    # Accounts go too -- anyone who signed up signs up again after a reset.
    restaurant_id = _id()
    _insert(db, Restaurant, [dict(id=restaurant_id, setup_completed_at=now, created_at=now, **DEMO_PROFILE)])
    _insert(db, User, [dict(id=_id(), email=DEMO_EMAIL, name=DEMO_NAME, role="owner", restaurant_id=restaurant_id,
                            password_hash=hash_password(DEMO_PASSWORD), created_at=now)])

    agent_id = _id()
    _insert(db, AgentDefinition, [dict(id=agent_id, role="main", model="claude-opus-5", enabled=True,
                                       created_at=now, schedule_cron="0 9 * * *", **AGENT_DEF)])

    suppliers = {name: _id() for name, _, _ in SUPPLIERS}
    _insert(db, Supplier, [
        dict(id=suppliers[name], name=name, contact_info=contact, lead_time_days=lead)
        for name, contact, lead in SUPPLIERS
    ])

    inventory = {row[0]: _id() for row in INVENTORY}
    unit_costs = {row[0]: row[5] for row in INVENTORY}
    _insert(db, InventoryItem, [
        dict(id=inventory[name], restaurant_id=restaurant_id, name=name, unit=unit, quantity_on_hand=on_hand,
             reorder_threshold=threshold, reorder_qty=reorder_qty, unit_cost=cost,
             supplier_id=suppliers.get(supplier), updated_at=now)
        for name, unit, on_hand, threshold, reorder_qty, cost, supplier in INVENTORY
    ])
    # Each item's stock history opens with what it has now, so it adds up.
    _insert(db, InventoryAdjustment, [
        dict(restaurant_id=restaurant_id, inventory_item_id=inventory[name], change=on_hand, quantity_after=on_hand,
             reason="initial", note="On hand when the demo opened", actor_type="system", actor_id="system",
             created_at=now)
        for name, _unit, on_hand, *_rest in INVENTORY
        if on_hand
    ])

    menu = {row[0]: _id() for row in MENU}
    _insert(db, MenuItem, [
        dict(id=menu[name], name=name, category=category, price=price, description=description,
             cost=round(sum(unit_costs[ingredient] * qty for ingredient, qty in recipe), 2), is_available=True)
        for name, category, price, description, _, recipe in MENU
    ])
    _insert(db, RecipeItem, [
        dict(menu_item_id=menu[name], inventory_item_id=inventory[ingredient], quantity=qty)
        for name, _, _, _, _, recipe in MENU
        for ingredient, qty in recipe
    ])

    staff = {name: _id() for name, _ in STAFF}
    _insert(db, Staff, [
        dict(id=staff[name], restaurant_id=restaurant_id, name=name, role=role,
             email=f"{name.split()[0].lower()}@bistro.example.com")
        for name, role in STAFF
    ])
    _insert(db, Shift, [dict(row, restaurant_id=restaurant_id, published_at=now) for row in _shifts(today, staff)])

    orders, lines = _orders(rng, now, menu)
    _insert(db, Order, orders)
    _insert(db, OrderItem, lines)

    proposals = _proposals(db, now, today, agent_id, suppliers, inventory, menu, staff)

    # What customers have said lately, waiting to be read and summarised.
    _insert(db, CustomerRequest, [
        dict(id=_id(), restaurant_id=restaurant_id, received_at=now - timedelta(days=days, hours=2 + index),
             created_at=now - timedelta(days=days, hours=1 + index), channel=channel, kind=kind,
             customer_name=name, customer_contact=contact, message=message, logged_by=DEMO_EMAIL, status="open")
        for index, (days, channel, kind, name, contact, message) in enumerate(CUSTOMER_REQUESTS)
    ])

    # Suppliers, dishes, orders and the agent's work are the demo restaurant's
    # too -- everything was wiped above, so nothing else is unclaimed.
    from app.restaurant.crud import claim_unassigned

    claim_unassigned(db, db.get(Restaurant, restaurant_id))

    # Eight weeks of supplier invoices, and this week's digest: the main event.
    from app.prices.demo import seed as seed_invoices

    prices = seed_invoices(db, restaurant_id, suppliers, today)
    # Two orders in flight: flour due tomorrow, and mozzarella that should have come yesterday.
    from app.restaurant.models import PurchaseOrder

    db.add_all([
        PurchaseOrder(restaurant_id=restaurant_id, supplier_id=suppliers["Sysco Foods"], supplier_name="Sysco Foods",
                      inventory_item_id=inventory["Flour"], item_name="Flour", quantity=100, unit="lb", packs=2,
                      pack_unit="bag", estimated_cost=37.80, status="ordered", ordered_at=now - timedelta(days=1),
                      expected_on=today + timedelta(days=1), placed_by=DEMO_EMAIL,
                      note="Placed by phone with the Sysco rep."),
        PurchaseOrder(restaurant_id=restaurant_id, supplier_id=suppliers["Harbor Dairy Co."], supplier_name="Harbor Dairy Co.",
                      inventory_item_id=inventory["Mozzarella"], item_name="Mozzarella", quantity=30, unit="lb", packs=5,
                      pack_unit="case", estimated_cost=159.50, status="shipped", ordered_at=now - timedelta(days=4),
                      shipped_at=now - timedelta(days=2), expected_on=today - timedelta(days=1), placed_by=DEMO_EMAIL),
    ])
    db.flush()
    # What the invoices price and the stock figures say is low: a reorder proposed from the invoices.
    from app.prices import stock

    proposals += len(stock.sync(db, restaurant_id, today, trigger="demo loaded").proposed)

    # Every v2 phase on, and two weeks of sales and labor as a POS and payroll would deliver them,
    # so the digest opens on a prime-cost line rather than the connect prompt.
    from app.platform import flags
    from app.sales import pnl
    from app.sales.models import LaborDay, SalesDay

    flags.enable_all(db, restaurant_id)
    for offset in range(1, 15):
        day = today - timedelta(days=offset)
        weekend = day.weekday() >= 4
        db.add(SalesDay(restaurant_id=restaurant_id, date=day, net_sales=round(2650 + (900 if weekend else 0) + rng.uniform(-150, 150), 2),
                        gross_sales=None, covers=74 + (26 if weekend else 0), source="csv"))
        db.add(LaborDay(restaurant_id=restaurant_id, date=day, hours=round(62 + (14 if weekend else 0), 1),
                        dollars=round(880 + (190 if weekend else 0) + rng.uniform(-30, 30), 2), source="csv"))
    from app.sales.models import SalesItem

    for offset in range(1, 29):
        day = today - timedelta(days=offset)
        for pos_id, name, sold, price in (("MARG", "Margherita Pizza", 26, 14.0), ("CAESAR", "Caesar Salad", 18, 11.0), ("TIRA", "Tiramisu", 11, 8.0)):
            units = sold + (6 if day.weekday() >= 4 else 0) + rng.randint(-2, 2)
            db.add(SalesItem(restaurant_id=restaurant_id, date=day, pos_item_id=pos_id, pos_item_name=name, qty=units, net=round(units * price, 2), source="csv"))
    db.flush()
    pnl.compute_week(db, restaurant_id, pnl.last_complete_week(today))
    # The top sellers get a recipe proposed, built from what the demo buys; the owner's first "Learn" step.
    from app.recipes import engine as recipe_engine

    proposals += len(recipe_engine.onboarding(db, restaurant_id, today))

    # The seed's own bookkeeping isn't history anyone made: a visitor starts with an empty record.
    from app.audit.models import ChangeLog

    db.flush()
    db.query(ChangeLog).delete()
    db.commit()
    counts = {"orders": len(orders), "order_items": len(lines), "proposals": proposals, **{f"price_{k}": v for k, v in prices.items()}}
    logger.info("demo data reset: %s", counts)
    return counts


def _shifts(today: date, staff: dict) -> list[dict]:
    rota = [
        ("Maria Gonzalez", time(14, 0), time(22, 0)),
        ("Jordan Lee", time(15, 0), time(23, 0)),
        ("Alex Rivera", time(17, 0), time(23, 0)),
        ("Chris Nguyen", time(17, 0), time(22, 0)),
        ("Sam Patel", time(17, 0), time(22, 0)),
    ]
    rows = []
    for offset in range(1, 8):
        day = today + timedelta(days=offset)
        for i, (name, start, end) in enumerate(rota):
            # Two people rest each day, on a rotation -- and nobody covers
            # the Friday host stand, which is the gap an agent can fill.
            if (offset + i) % 5 in (0, 1) and name != "Maria Gonzalez":
                continue
            if day.weekday() == 4 and name == "Sam Patel":
                continue
            rows.append(dict(staff_id=staff[name], date=day, start_time=start, end_time=end,
                             role=dict((n, r) for n, r in STAFF)[name]))
    return rows


def _orders(rng: random.Random, now: datetime, menu: dict) -> tuple[list[dict], list[dict]]:
    names = [row[0] for row in MENU]
    weights = [row[4] for row in MENU]
    prices = {row[0]: row[2] for row in MENU}
    channels, channel_weights = zip(*CHANNELS)

    orders, lines = [], []
    for days_ago in range(HISTORY_DAYS, -1, -1):
        day = now.date() - timedelta(days=days_ago)
        # A gentle upward trend, and some day-to-day noise.
        base = ORDERS_PER_WEEKDAY[day.weekday()] * (0.85 + 0.15 * (HISTORY_DAYS - days_ago) / HISTORY_DAYS)
        for _ in range(max(1, round(base * rng.uniform(0.85, 1.15)))):
            placed = datetime.combine(day, time(11, 30), tzinfo=timezone.utc) + timedelta(
                minutes=rng.randint(0, 10 * 60)
            )
            if placed >= now:
                continue  # today only has the orders already taken
            order_id = _id()
            total = 0.0
            for dish in set(rng.choices(names, weights, k=rng.choice((1, 2, 2, 3, 3, 4)))):
                qty = rng.choice((1, 1, 1, 2))
                total += prices[dish] * qty
                lines.append(dict(order_id=order_id, menu_item_id=menu[dish], qty=qty, unit_price=prices[dish]))
            channel = rng.choices(channels, channel_weights)[0]
            orders.append(dict(
                id=order_id, status="completed", channel=channel, total=round(total, 2), created_at=placed,
                table_number=rng.randint(1, 18) if channel == "dine_in" else None,
            ))
    return orders, lines


def _proposals(db, now, today, agent_id, suppliers, inventory, menu, staff) -> int:
    """Finished runs that each left a proposal waiting for a decision."""
    friday = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
    planted = [
        ("inventory", "Morning stock check.",
         [("list_low_stock_items", "4 items are below their reorder point: Tomatoes, Basil, Chicken Breast, "
                                   "Romaine Lettuce.")],
         "Tomatoes are at 6 lb against a reorder point of 15, with a 1-day lead time. Proposed a 30 lb reorder.",
         "inventory_reorder",
         {"inventory_item_id": inventory["Tomatoes"], "item_name": "Tomatoes", "quantity": 30,
          "note": "6 lb on hand, reorder point 15 lb. Margherita and Caprese both depend on it."}),
        ("supply_chain", "Check what needs ordering from suppliers this week.",
         [("list_suppliers", "Sysco Foods (2d), Green Valley Farms (1d), Harbor Dairy Co. (3d)")],
         "Chicken Breast is short and Sysco needs two days' notice, so I drafted the order now.",
         "purchase_order",
         {"supplier_id": suppliers["Sysco Foods"], "supplier_name": "Sysco Foods",
          "inventory_item_id": inventory["Chicken Breast"], "item_name": "Chicken Breast", "quantity": 40,
          "note": "11 lb left; two pizzas and the sandwich use it."}),
        ("marketing", "Find slow sellers worth promoting.",
         [("get_slow_moving_items", "Affogato and Garlic Bread sell least over the last 30 days.")],
         "Garlic Bread is a slow seller with a high margin. Suggested a promotion, but I didn't set a price.",
         "menu_change",
         {"menu_item_id": menu["Garlic Bread"], "item_name": "Garlic Bread", "change_type": "promotion",
          "details": "Half price with any pizza on weeknights to lift a slow seller.", "new_price": None}),
        ("employee_management", "Look for gaps in next week's schedule.",
         [("get_upcoming_shifts", f"No host is scheduled on {friday.isoformat()}, the busiest night.")],
         f"Nobody covers the host stand on Friday {friday.isoformat()}. Proposed Sam Patel for the dinner shift.",
         "shift_change",
         {"staff_id": staff["Sam Patel"], "staff_name": "Sam Patel", "date": friday.isoformat(),
          "start_time": "17:00:00", "end_time": "22:00:00", "role": "Host",
          "note": "Friday is the busiest night and has no host."}),
    ]

    # The first field of each is the area it's about; one agent covers them all.
    for minutes_ago, (_area, task, tool_calls, summary, action_type, payload) in zip(
        (95, 70, 45, 20), planted
    ):
        started = now - timedelta(minutes=minutes_ago)
        run_id = _id()
        _insert(db, AgentRun, [dict(
            id=run_id, agent_definition_id=agent_id, status="succeeded", trigger_type="scheduled",
            input=task, output_summary=summary, tokens_used=None, depth=0,
            started_at=started, finished_at=started + timedelta(seconds=12), created_at=started,
        )])
        events = [("status_change", {"status": "running"}), ("log", {"text": task})]
        for tool, result in tool_calls:
            events += [("tool_call", {"tool": tool, "input": {}}), ("tool_result", {"tool": tool, "result": result})]
        events += [("log", {"text": summary}), ("status_change", {"status": "succeeded"})]
        _insert(db, AgentEvent, [
            dict(run_id=run_id, seq=seq, type=kind, payload=body, ts=started + timedelta(seconds=seq * 2))
            for seq, (kind, body) in enumerate(events, start=1)
        ])
        _insert(db, AgentAction, [dict(
            id=_id(), run_id=run_id, agent_definition_id=agent_id, action_type=action_type,
            payload=payload, status="pending", created_at=started + timedelta(seconds=10),
        )])
    return len(planted)


def reset_now() -> dict:
    """Reset through the app's own database, stopping any runs in flight first."""
    from app.agents import executor
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        for (run_id,) in db.query(AgentRun.id).filter(AgentRun.status.in_(("queued", "running"))):
            executor.request_cancel(run_id)
        return reset_demo_data(db)
    finally:
        db.close()
