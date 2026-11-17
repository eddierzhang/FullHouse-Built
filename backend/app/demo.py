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
    name="Botanica",
    street="214 Mulberry Street",
    city="New York",
    region="NY",
    postal_code="10012",
    country="United States",
    phone="+1 212 555 0148",
    email="hello@botanica.example.com",
    website="https://botanica.example.com",
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
#: The bistro's size. Orders, stock and invoices all scale from these, so the pages agree with each
#: other: about $21,000 a week in sales, food bought at about 30% of it, labor at about 31%.
VOLUME = 5.0
STOCK_SCALE = 5
#: Invoice lines for things no recipe uses (napkins, wine) are scaled by this; everything a recipe uses is
#: bought at what the recipes say was used, a little over (BUY_OVER), so purchases and usage tell one story.
INVOICE_SCALE = 5
BUY_OVER = 1.04
#: printed invoice line -> (stock item, how much of the stock unit one pack holds, packs a week before scaling)
BOUGHT_AS = {
    "CHIX BRST BNLS 4/10#": ("Chicken Breast", 40, 3), "FLOUR AP 50#": ("Flour", 50, 4), "OLIVE OIL EV 4/1GAL": ("Olive Oil", 15.14, 1),
    "TOMATO ROMA 25#": ("Tomatoes", 25, 6), "TOMATOES ROMA 25LB": ("Tomatoes", 25, 6), "ROMAINE 24CT": ("Romaine Lettuce", 24, 2),
    "LETT ROMAINE 24CT": ("Romaine Lettuce", 24, 2), "MOZZ FRESH 6/1#": ("Mozzarella", 6, 5), "PARM REGG WHL 10#": ("Parmesan", 10, 1),
    "CIABATTA LOAF 12CT": ("Ciabatta", 12, 1), "ESPRESSO BEANS 5#": ("Espresso Beans", 5, 1), "MASCARPONE 5# TUB": ("Mascarpone", 5, 1),
    "GARLIC PEELED 5#": ("Garlic", 5, 1),
}
#: Staples the recipes use that the test fixture's invoices leave out. Bought every week, at a steady price.
MORE_ITEMS = [
    ("Ciabatta, 12 ct case", "food", "ct", "case", 12), ("Espresso beans, 5 lb bag", "dry", "lb", "bag", 5),
    ("Mascarpone, 5 lb tub", "food", "lb", "tub", 5), ("Garlic, peeled, 5 lb bag", "food", "lb", "bag", 5),
]
MORE_LINES = {
    "Sysco Foods": [("CIABATTA LOAF 12CT", 1, "CS", 43.20, "Ciabatta, 12 ct case"), ("ESPRESSO BEANS 5#", 1, "BG", 90.00, "Espresso beans, 5 lb bag")],
    "Harbor Dairy Co.": [("MASCARPONE 5# TUB", 1, "EA", 47.50, "Mascarpone, 5 lb tub")],
    "Green Valley Farms": [("GARLIC PEELED 5#", 1, "BG", 22.50, "Garlic, peeled, 5 lb bag")],
}
LABOR_SHARE = 0.31
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
    # Unit costs are what the demo's invoices charge (a 25 lb case of tomatoes at $31.20 is $1.25 a lb), so
    # stock value, plate cost and the variance report are in the same dollars as the invoices.
    ("Flour", "lb", 62, 25, 50, 0.38, "Sysco Foods"),
    ("Mozzarella", "lb", 16, 12, 30, 5.32, "Harbor Dairy Co."),
    ("Tomatoes", "lb", 6, 15, 30, 1.25, "Green Valley Farms"),
    ("Basil", "bunch", 3, 8, 20, 2.40, "Green Valley Farms"),
    ("Romaine Lettuce", "head", 9, 10, 30, 1.17, "Green Valley Farms"),
    ("Parmesan", "lb", 7, 4, 10, 16.00, "Harbor Dairy Co."),
    ("Chicken Breast", "lb", 11, 15, 40, 2.19, "Sysco Foods"),
    ("Mascarpone", "lb", 5, 3, 8, 9.50, "Harbor Dairy Co."),
    ("Espresso Beans", "lb", 4, 2, 6, 18.00, "Sysco Foods"),
    ("Garlic", "lb", 3, 2, 5, 4.50, "Green Valley Farms"),
    ("Olive Oil", "L", 8, 4, 12, 6.34, "Sysco Foods"),
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
    stocked = [(name, unit, on_hand * STOCK_SCALE, threshold * STOCK_SCALE, reorder_qty * STOCK_SCALE, cost, supplier)
               for name, unit, on_hand, threshold, reorder_qty, cost, supplier in INVENTORY]
    _insert(db, InventoryItem, [
        dict(id=inventory[name], restaurant_id=restaurant_id, name=name, unit=unit, quantity_on_hand=on_hand,
             reorder_threshold=threshold, reorder_qty=reorder_qty, unit_cost=cost,
             supplier_id=suppliers.get(supplier), updated_at=now)
        for name, unit, on_hand, threshold, reorder_qty, cost, supplier in stocked
    ])
    # The stock history (opening count, deliveries, sales, waste, a count) is written by _stock_story below.

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

    # What a week uses of each ingredient, from the orders through the recipes: what the invoices then buy.
    recipes = {name: recipe for name, _, _, _, _, recipe in MENU}
    dish_names = {menu[name]: name for name in menu}
    weekly_use: dict[str, float] = {}
    for line in lines:
        for ingredient, per_plate in recipes[dish_names[line["menu_item_id"]]]:
            weekly_use[ingredient] = weekly_use.get(ingredient, 0.0) + per_plate * line["qty"] / ((HISTORY_DAYS + 1) / 7)

    def bought(description: str) -> float:
        if description not in BOUGHT_AS:
            return INVOICE_SCALE
        ingredient, per_pack, base_packs = BOUGHT_AS[description]
        return weekly_use.get(ingredient, 0.0) * BUY_OVER / (per_pack * base_packs)

    prices = seed_invoices(db, restaurant_id, suppliers, today, scale=bought, more_items=MORE_ITEMS, more_lines=MORE_LINES)
    # Two orders in flight: flour due tomorrow, and mozzarella that should have come yesterday.
    from app.restaurant.models import PurchaseOrder

    db.add_all([
        PurchaseOrder(restaurant_id=restaurant_id, supplier_id=suppliers["Sysco Foods"], supplier_name="Sysco Foods",
                      inventory_item_id=inventory["Flour"], item_name="Flour", quantity=100 * STOCK_SCALE, unit="lb",
                      packs=2 * STOCK_SCALE, pack_unit="bag", estimated_cost=37.80 * STOCK_SCALE, status="ordered", ordered_at=now - timedelta(days=1),
                      expected_on=today + timedelta(days=1), placed_by=DEMO_EMAIL,
                      note="Placed by phone with the Sysco rep."),
        PurchaseOrder(restaurant_id=restaurant_id, supplier_id=suppliers["Harbor Dairy Co."], supplier_name="Harbor Dairy Co.",
                      inventory_item_id=inventory["Mozzarella"], item_name="Mozzarella", quantity=30 * STOCK_SCALE, unit="lb",
                      packs=5 * STOCK_SCALE, pack_unit="case", estimated_cost=159.50 * STOCK_SCALE, status="shipped", ordered_at=now - timedelta(days=4),
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
    # Voice is built but has only been tried with typed text, never a real microphone: off in the demo for now.
    # An owner can switch it on under Profile, Features.
    flags.set_flag(db, restaurant_id, flags.V3_VOICE, False)
    # The POS and payroll figures are the same trading the operations pages show: each day's sales
    # are that day's orders, so Today, Profit and the Digest can never disagree.
    by_day: dict[date, float] = {}
    for order in orders:
        by_day[order["created_at"].date()] = by_day.get(order["created_at"].date(), 0.0) + order["total"]
    order_days = {order["id"]: order["created_at"].date() for order in orders}
    for offset in range(1, 29):
        day = today - timedelta(days=offset)
        net = round(by_day.get(day, 0.0), 2)
        covers = sum(1 for d in order_days.values() if d == day)
        db.add(SalesDay(restaurant_id=restaurant_id, date=day, net_sales=net, gross_sales=None, covers=covers, source="square"))
        dollars = round(net * LABOR_SHARE * rng.uniform(0.96, 1.04), 2)
        db.add(LaborDay(restaurant_id=restaurant_id, date=day, hours=round(dollars / 14.2, 1), dollars=dollars, source="gusto"))
    from app.sales.models import SalesItem

    dish_of = {menu[name]: name for name in menu}
    sold: dict[tuple[date, str], list[float]] = {}
    for line in lines:
        day = order_days[line["order_id"]]
        if not (today - timedelta(days=29) < day < today):
            continue
        cell = sold.setdefault((day, dish_of[line["menu_item_id"]]), [0.0, 0.0])
        cell[0] += line["qty"]
        cell[1] += line["qty"] * line["unit_price"]
    for (day, dish), (qty, net) in sorted(sold.items()):
        db.add(SalesItem(restaurant_id=restaurant_id, date=day, pos_item_id=_pos_id(dish), pos_item_name=dish, qty=qty, net=round(net, 2), source="square"))
    db.flush()
    _stock_story(db, restaurant_id, now, today, inventory, stocked, sold)
    # Three whole weeks of prime cost, so the chart has a line to draw; the digest reports the latest.
    for back in (2, 1, 0):
        pnl.compute_week(db, restaurant_id, pnl.last_complete_week(today) - timedelta(weeks=back))
    _digest_history(db, restaurant_id, suppliers, now, today)
    # The top sellers get a recipe proposed, built from what the demo buys; the owner's first "Learn" step.
    from app.recipes import engine as recipe_engine

    _confirmed_recipes(db, restaurant_id, menu)
    proposals += len(recipe_engine.onboarding(db, restaurant_id, today))
    _back_office(db, restaurant_id, now, today)
    _spread_out_the_agents_work(db, restaurant_id, now)
    proposals += _ordering(db, restaurant_id, suppliers, now, today)
    # The schedules every restaurant gets at setup, made here so they are not the first thing in History.
    from app.platform import workflows

    workflows.ensure_defaults(db, restaurant_id)

    # The seed's own bookkeeping isn't history anyone made: a visitor starts with an empty record.
    from app.audit.models import ChangeLog

    db.flush()
    db.query(ChangeLog).delete()
    db.commit()
    counts = {"orders": len(orders), "order_items": len(lines), "proposals": proposals, **{f"price_{k}": v for k, v in prices.items()}}
    logger.info("demo data reset: %s", counts)
    return counts


def _digest_history(db: Session, restaurant_id: str, suppliers: dict, now: datetime, today: date) -> None:
    """The digests of the five weeks before this one, as they would have read then, and one card the owner
    acted on: the chicken rise was raised with the Sysco rep nine days ago. So "found so far" has a history."""
    from app.prices import engine as price_engine
    from app.prices.models import SENT, PriceItem

    for back in range(5, 0, -1):
        price_engine.build_digest(db, restaurant_id, today - timedelta(weeks=back))
    chicken = db.query(PriceItem).filter(PriceItem.restaurant_id == restaurant_id, PriceItem.name.like("Chicken breast%")).first()
    if chicken is not None:
        draft = price_engine.draft_for(db, chicken, db.get(Supplier, suppliers["Sysco Foods"]), "price_challenge", today)
        draft.status, draft.sent_at, draft.sent_by = SENT, now - timedelta(days=9), DEMO_EMAIL
    db.flush()


#: Where the demo keeps things, in the order someone walks the shelves.
STORAGE = {
    "Walk-in": ["Mozzarella", "Mascarpone", "Parmesan", "Chicken Breast", "Tomatoes", "Romaine Lettuce", "Basil", "Garlic"],
    "Dry store": ["Flour", "Olive Oil", "Espresso Beans", "Ciabatta"],
}
#: days ago, item, amount in the item's unit (before STOCK_SCALE), reason key, note
WASTE = [
    (9, "Mozzarella", 0.4, "dropped", "tub tipped in the walk-in"),
    (5, "Basil", 1.0, "spoiled", "wilted over the weekend"),
    (3, "Tomatoes", 1.2, "spoiled", "soft case from Friday"),
    (2, "Chicken Breast", 0.6, "overcooked", None),
    (1, "Romaine Lettuce", 0.8, "prep", "outer leaves"),
]
#: What last week's walk-in count found against what the ledger expected (before STOCK_SCALE).
COUNT_FOUND = {"Mozzarella": -4.0, "Chicken Breast": -6.4, "Basil": -2.0, "Parmesan": -0.6, "Tomatoes": 0.4}
STORY_DAYS = 28


def _stock_story(db: Session, restaurant_id: str, now: datetime, today: date, inventory: dict, stocked: list, sold: dict) -> None:
    """Two weeks of stock movements that end at today's figures: an opening count, a delivery every other
    morning, each day's sales through the recipes, a little waste, and a walk-in count a week ago that found
    some missing. Written straight to the ledger, so each item's history adds up to what is on hand.
    From today on, the stock job takes new sales off by itself."""
    from app.restaurant.inventory import REASONS
    from app.stock import engine as stock_engine
    from app.stock.models import POSTED, StockCount, StockCountLine, StorageArea

    assert "sale" in REASONS
    areas = {}
    for position, (name, names) in enumerate(STORAGE.items()):
        area = StorageArea(restaurant_id=restaurant_id, name=name, position=position)
        db.add(area)
        db.flush()
        areas[name] = area
        for shelf, item_name in enumerate(names):
            db.execute(InventoryItem.__table__.update().where(InventoryItem.id == inventory[item_name])
                       .values(storage_area_id=area.id, shelf_order=shelf))
    recipes = {name: recipe for name, _, _, _, _, recipe in MENU}
    suppliers_of = {name: supplier for name, *_rest, supplier in stocked}
    count_day = today - timedelta(days=7)
    count = StockCount(restaurant_id=restaurant_id, storage_area_id=areas["Walk-in"].id, status=POSTED, started_by=DEMO_EMAIL,
                       started_at=datetime.combine(count_day, time(15, 0), tzinfo=timezone.utc),
                       posted_at=datetime.combine(count_day, time(15, 25), tzinfo=timezone.utc))
    db.add(count)
    db.flush()

    rows = []
    for name, unit, on_hand, _threshold, _reorder_qty, cost, _supplier in stocked:
        item_id = inventory[name]
        moves: list[tuple[datetime, float, str, str | None, str, str | None, str | None]] = []  # when, change, reason, note, actor type, actor, key
        usage = {}
        for back in range(STORY_DAYS, 0, -1):
            day = today - timedelta(days=back)
            for dish, recipe in recipes.items():
                per_plate = dict(recipe).get(name)
                plates = sold.get((day, dish), [0.0])[0]
                if per_plate and plates:
                    used = round(per_plate * plates, 4)
                    usage[day] = usage.get(day, 0.0) + used
                    moves.append((datetime.combine(day, time(23, 30), tzinfo=timezone.utc), -used, "sale", f"{plates:g} × {dish}, {day.isoformat()}",
                                  "system", "system", f"sale:{day.isoformat()}:square:{_pos_id(dish)}:{item_id}"))
        for back in range(STORY_DAYS, 0, -2):
            day = today - timedelta(days=back)
            amount = round(usage.get(day, 0.0) + usage.get(day + timedelta(days=1), 0.0), 2)
            if amount > 0:
                moves.append((datetime.combine(day, time(9, 10), tzinfo=timezone.utc), amount, "delivery",
                              f"Delivered by {suppliers_of.get(name) or 'the supplier'}", "human", DEMO_EMAIL, None))
        for back, wasted, amount, reason, note in WASTE:
            if wasted == name:
                text = stock_engine.WASTE_REASONS[reason] + (f": {note}" if note else "")
                moves.append((datetime.combine(today - timedelta(days=back), time(21, 40), tzinfo=timezone.utc), -amount * STOCK_SCALE, "waste",
                              text, "human", DEMO_EMAIL, None))
        counted = name in STORAGE["Walk-in"]
        if counted:
            moves.append((count.posted_at, COUNT_FOUND.get(name, 0.0) * STOCK_SCALE, "count", "Counted in Walk-in", "human", DEMO_EMAIL,
                          f"count:{count.id}:{item_id}"))
        moves.sort(key=lambda m: m[0])
        level = round(on_hand - sum(m[1] for m in moves), 4)  # the opening count that makes it all add up
        rows.append(dict(restaurant_id=restaurant_id, inventory_item_id=item_id, change=level, quantity_after=level, reason="initial",
                         note="Counted when Botanica started on FullHouse", actor_type="system", actor_id="system", request_id=None,
                         source_key=None, created_at=now - timedelta(days=HISTORY_DAYS)))
        for when, change, reason, note, actor_type, actor_id, key in moves:
            before = level
            level = round(level + change, 4)
            if reason == "count":
                db.add(StockCountLine(restaurant_id=restaurant_id, count_id=count.id, inventory_item_id=item_id, item_name=name, unit=unit,
                                      expected_qty=before, counted_qty=level, unit_cost=cost))
                if change == 0:
                    continue  # counted and found right: a line on the sheet, nothing in the ledger
            rows.append(dict(restaurant_id=restaurant_id, inventory_item_id=item_id, change=round(change, 4), quantity_after=level, reason=reason,
                             note=note, actor_type=actor_type, actor_id=actor_id, request_id=None, source_key=key, created_at=when))
    _insert(db, InventoryAdjustment, rows)
    restaurant = db.get(Restaurant, restaurant_id)
    restaurant.inventory_settings = {**(restaurant.inventory_settings or {}), "deplete_from": today.isoformat()}
    db.flush()
    stock_engine.rollup(db, restaurant_id, today=today)


#: supplier -> (order weekdays, cutoff, delivery weekdays, minimum dollars, order address)
ORDER_DAYS = {
    "Green Valley Farms": ([0, 2, 4], "14:00", [1, 3, 5], 150, "orders@greenvalley.example.com"),
    "Sysco Foods": ([1, 4], "15:00", [3, 6], 300, "orders@sysco.example.com"),
    "Harbor Dairy Co.": ([0, 3], "12:00", [3, 6], 250, "sales@harbordairy.example.com"),
    "Empire Wine & Spirits": ([2], "16:00", [0], 200, "orders@empirewine.example.com"),
}
#: supplier -> the owner's autopilot limits. Sysco's order is bigger than its limit, so it waits for a person.
AUTOPILOT_LIMITS = {"Green Valley Farms": (1500, 4000), "Sysco Foods": (750, 2500)}


def _ordering(db: Session, restaurant_id: str, suppliers: dict, now: datetime, today: date) -> int:
    """Each supplier's order days, autopilot switched on for two of them, and today's drafts run through the
    limits: the farm's order passes and waits out its cancel window; Sysco's is over its limit and is held.
    Returns how many proposals were left for a person."""
    from app.ordering import autopilot, pars
    from app.ordering.models import AutopilotPolicy, SupplierCalendar

    for name, (order_days, cutoff, delivery_days, minimum, address) in ORDER_DAYS.items():
        db.add(SupplierCalendar(restaurant_id=restaurant_id, supplier_id=suppliers[name], order_days=order_days, cutoff_time=cutoff,
                                delivery_days=delivery_days, minimum_dollars=minimum, channel="email", order_email=address))
    for name, (per_order, weekly) in AUTOPILOT_LIMITS.items():
        db.add(AutopilotPolicy(restaurant_id=restaurant_id, supplier_id=suppliers[name], enabled=True, per_order_cap=per_order,
                               weekly_cap=weekly, updated_by=DEMO_EMAIL))
    db.flush()
    pars.recompute(db, restaurant_id)
    held = 0
    for name in AUTOPILOT_LIMITS:
        action = autopilot.draft_for(db, restaurant_id, db.get(Supplier, suppliers[name]), today)
        if action is not None:
            decision = autopilot.decide(db, action, now, today)
            held += 1 if decision is None or decision.outcome == "held" else 0
    return held


def _pos_id(dish: str) -> str:
    return "".join(ch for ch in dish.upper() if ch.isalnum())[:12]


#: Dishes the owner has already confirmed a recipe for, in the units the invoices price:
#: dish -> [(invoice item, quantity, unit, as written on the card)].
CONFIRMED_RECIPES = {
    "Margherita Pizza": [("Flour, 50 lb bag", 0.5, "lb", "00 flour"), ("Mozzarella, 6 lb case", 0.3, "lb", "fresh mozzarella"),
                         ("Tomatoes, 25 lb case", 0.25, "lb", "roma tomatoes"), ("Basil, 1 lb", 0.5, "oz", "basil"),
                         ("Olive oil, 4 gal case", 0.5, "fl oz", "olive oil")],
    "Chicken Pesto Pizza": [("Flour, 50 lb bag", 0.5, "lb", "00 flour"), ("Mozzarella, 6 lb case", 0.3, "lb", "fresh mozzarella"),
                            ("Chicken breast, 40 lb case", 0.25, "lb", "grilled chicken"), ("Basil, 1 lb", 0.75, "oz", "basil pesto"),
                            ("Olive oil, 4 gal case", 1, "fl oz", "olive oil")],
    "Grilled Chicken Sandwich": [("Chicken breast, 40 lb case", 0.4, "lb", "chicken breast"), ("Romaine, 24 ct case", 0.1, "ct", "romaine"),
                                 ("Tomatoes, 25 lb case", 0.1, "lb", "sliced tomato")],
    "Caesar Salad": [("Romaine, 24 ct case", 0.5, "ct", "romaine heart"), ("Parmesan, 10 lb wheel", 0.8, "oz", "shaved parmesan"),
                     ("Olive oil, 4 gal case", 0.5, "fl oz", "olive oil")],
    "Caprese Salad": [("Tomatoes, 25 lb case", 0.35, "lb", "tomato"), ("Mozzarella, 6 lb case", 0.2, "lb", "fresh mozzarella"),
                      ("Basil, 1 lb", 0.5, "oz", "basil"), ("Olive oil, 4 gal case", 0.5, "fl oz", "olive oil")],
}


def _confirmed_recipes(db: Session, restaurant_id: str, menu: dict) -> None:
    """Recipes the owner has confirmed for five dishes, costed from this week's invoices."""
    from app.prices.models import PriceItem
    from app.recipes import engine as recipe_engine

    items = {i.name: i for i in db.query(PriceItem).filter(PriceItem.restaurant_id == restaurant_id)}
    for dish, lines in CONFIRMED_RECIPES.items():
        recipe_engine.write_recipe(db, restaurant_id, {
            "name": dish, "pos_item_id": _pos_id(dish), "menu_item_id": menu[dish], "source": "user",
            "lines": [{"item_id": items[item].id, "qty": qty, "unit": unit, "ingredient_text": text}
                      for item, qty, unit, text in lines if item in items],
        }, confirmed_by=DEMO_EMAIL)
    recipe_engine.compute_plate_costs(db, restaurant_id)


def _back_office(db: Session, restaurant_id: str, now: datetime, today: date) -> None:
    """Documents on file, dates on the calendar, a forecast and tomorrow's prep sheet."""
    from app.calendar import engine as calendar_engine
    from app.documents.models import COMPLETE, Document
    from app.forecast import engine as forecast_engine
    from app.forecast import prep

    filed = [
        ("permit", "health-permit-2026.pdf", "Health department permit", 24, "annual", None),
        ("license", "liquor-license.pdf", "Liquor license renewal", 68, "annual", None),
        ("inspection", "hood-inspection-report.pdf", "Kitchen hood cleaning and inspection", 9, "every 6 months", None),
        ("warranty", "walk-in-cooler-warranty.pdf", "Walk-in cooler warranty ends", 140, None,
         {"name": "Walk-in cooler", "vendor": "Polar King", "purchased_on": (today - timedelta(days=590)).isoformat(),
          "service_interval_days": 180}),
    ]
    for index, (doc_type, filename, title, due_in, recurrence, asset) in enumerate(filed):
        document = Document(restaurant_id=restaurant_id, doc_type=doc_type, type_confidence=0.97, filename=filename,
                            media_type="application/pdf", source="email" if index % 2 else "upload", status=COMPLETE,
                            extracted={"title": title, "due_on": (today + timedelta(days=due_in)).isoformat()},
                            confidence=0.95, created_at=now - timedelta(days=30 - index * 6))
        db.add(document)
        db.flush()
        payload = {"kind": doc_type, "title": title, "due_on": (today + timedelta(days=due_in)).isoformat(),
                   "recurrence": recurrence, "source_document_id": document.id}
        if asset:
            payload["asset"] = {**asset, "source_document_id": document.id}
        item = calendar_engine.write_item(db, restaurant_id, payload)
        document.result_table, document.result_id = "calendar_items", item.id
    db.add(Document(restaurant_id=restaurant_id, doc_type="price_list", type_confidence=0.94, filename="sysco-price-list-q4.pdf",
                    media_type="application/pdf", source="email", status=COMPLETE, extracted={"supplier": "Sysco Foods", "lines": 6},
                    confidence=0.92, created_at=now - timedelta(days=12)))
    db.flush()
    forecast_engine.run(db, restaurant_id, today)
    tomorrow = today + timedelta(days=1)
    lines = prep.lines_for(db, restaurant_id, tomorrow)
    if lines:
        prep.write_sheet(db, restaurant_id, {"date": tomorrow.isoformat(), "lines": lines}, None)
    # The point of sale and payroll the sales and labor figures came from, shown as connected.
    from app.connectors.models import Connection
    from app.connectors.registry import seed_providers

    seed_providers(db)
    for provider, kind in (("square", "pos"), ("gusto", "payroll")):
        db.add(Connection(restaurant_id=restaurant_id, kind=kind, provider=provider, status="active",
                          last_sync_at=now - timedelta(hours=3), settings={"sample": True}))
    db.flush()


def _spread_out_the_agents_work(db: Session, restaurant_id: str, now: datetime) -> None:
    """Runs made while loading are stamped this instant; a real week's work is spread over days."""
    fresh = (db.query(AgentRun).filter(AgentRun.restaurant_id == restaurant_id, AgentRun.created_at >= now - timedelta(seconds=90))
             .order_by(AgentRun.created_at).all())
    for index, run in enumerate(fresh):
        moment = now - timedelta(hours=3 + index * 7, minutes=11 * index)
        run.created_at = run.started_at = moment
        run.finished_at = moment + timedelta(seconds=9)
        for action in db.query(AgentAction).filter(AgentAction.run_id == run.id):
            action.created_at = moment + timedelta(seconds=8)
        for seq, event in enumerate(db.query(AgentEvent).filter(AgentEvent.run_id == run.id).order_by(AgentEvent.seq)):
            event.ts = moment + timedelta(seconds=2 * seq)
    db.flush()


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
        base = VOLUME * ORDERS_PER_WEEKDAY[day.weekday()] * (0.85 + 0.15 * (HISTORY_DAYS - days_ago) / HISTORY_DAYS)
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
         f"Tomatoes are at {6 * STOCK_SCALE} lb against a reorder point of {15 * STOCK_SCALE}, with a 1-day lead time. "
         f"Proposed a {30 * STOCK_SCALE} lb reorder.",
         "inventory_reorder",
         {"inventory_item_id": inventory["Tomatoes"], "item_name": "Tomatoes", "quantity": 30 * STOCK_SCALE,
          "note": f"{6 * STOCK_SCALE} lb on hand, reorder point {15 * STOCK_SCALE} lb. Margherita and Caprese both depend on it."}),
        ("supply_chain", "Check what needs ordering from suppliers this week.",
         [("list_suppliers", "Sysco Foods (2d), Green Valley Farms (1d), Harbor Dairy Co. (3d)")],
         "Chicken Breast is short and Sysco needs two days' notice, so I drafted the order now.",
         "purchase_order",
         {"supplier_id": suppliers["Sysco Foods"], "supplier_name": "Sysco Foods",
          "inventory_item_id": inventory["Chicken Breast"], "item_name": "Chicken Breast", "quantity": 40 * STOCK_SCALE,
          "note": f"{11 * STOCK_SCALE} lb left; two pizzas and the sandwich use it."}),
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
