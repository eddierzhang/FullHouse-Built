"""Scenarios: a small restaurant in a known state, a request, and what a
competent agent should do about it.

Each one checks behaviour, not wording. Two are deliberately negative --
nothing needs doing -- because an agent that proposes something every time
looks busy while being wrong.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy.orm import Session

from app.agents.models import AgentDefinition
from app.restaurant.models import InventoryItem, MenuItem, Order, OrderItem, Shift, Staff, Supplier


@dataclass
class ExpectedProposal:
    action_type: str
    #: (payload, facts) -> whether this proposal is the right one.
    matches: Callable[[dict, dict], bool]
    describe: str


@dataclass
class Scenario:
    id: str
    agent_key: str
    prompt: str
    summary: str
    #: Tweaks the base world; returns facts (ids) the checks refer to.
    setup: Callable[[Session, dict], dict]
    #: Every one of these must be called.
    expected_tools: set[str] = field(default_factory=set)
    expected_proposal: ExpectedProposal | None = None
    #: Negative scenarios: the correct outcome is to propose nothing.
    forbid_proposals: bool = False


def build_world(db: Session) -> dict:
    """The shared baseline: healthy stock, a sold menu, a staffed schedule."""
    from app.agents.definitions import BOSS_DEF, SUBAGENT_DEFS

    for d in SUBAGENT_DEFS:
        db.add(AgentDefinition(role="subagent", enabled=True, **d))
    db.add(AgentDefinition(role="boss", enabled=True, **BOSS_DEF))

    sysco = Supplier(name="Sysco Foods", contact_info="orders@sysco.example", lead_time_days=2)
    farm = Supplier(name="Green Valley Farms", contact_info="hello@farm.example", lead_time_days=1)
    db.add_all([sysco, farm])
    db.flush()

    stock = {
        "flour": InventoryItem(name="Flour", unit="lb", quantity_on_hand=60, reorder_threshold=20,
                               reorder_qty=50, unit_cost=0.6, supplier_id=sysco.id),
        "cheese": InventoryItem(name="Mozzarella Cheese", unit="lb", quantity_on_hand=30, reorder_threshold=10,
                                reorder_qty=20, unit_cost=4.5, supplier_id=sysco.id),
        "tomatoes": InventoryItem(name="Tomatoes", unit="lb", quantity_on_hand=40, reorder_threshold=12,
                                  reorder_qty=25, unit_cost=1.8, supplier_id=farm.id),
    }
    db.add_all(stock.values())

    menu = {
        "pizza": MenuItem(name="Margherita Pizza", category="Entree", price=14.0, cost=4.2),
        "salad": MenuItem(name="Caesar Salad", category="Salad", price=11.0, cost=2.8),
        "bread": MenuItem(name="Garlic Bread", category="Appetizer", price=6.0, cost=1.1),
    }
    db.add_all(menu.values())

    staff = {
        "ana": Staff(name="Ana Diaz", role="Server", email="ana@example.com"),
        "jordan": Staff(name="Jordan Lee", role="Line Cook", email="jordan@example.com"),
    }
    db.add_all(staff.values())
    db.flush()

    now = datetime.now(timezone.utc)
    for days_ago, pizzas, salads in ((5, 4, 2), (4, 5, 2), (3, 3, 3), (2, 4, 1), (1, 4, 2)):
        order = Order(status="completed", channel="dine_in", created_at=now - timedelta(days=days_ago))
        order.items.append(OrderItem(menu_item_id=menu["pizza"].id, qty=pizzas, unit_price=14.0))
        order.items.append(OrderItem(menu_item_id=menu["salad"].id, qty=salads, unit_price=11.0))
        order.total = pizzas * 14.0 + salads * 11.0
        db.add(order)

    today = date.today()
    for offset in range(1, 4):
        db.add(Shift(staff_id=staff["jordan"].id, date=today + timedelta(days=offset),
                     start_time=time(15, 0), end_time=time(23, 0), role="Line Cook"))

    db.commit()
    return {
        "suppliers": {"sysco": sysco.id, "farm": farm.id},
        "stock": {k: v.id for k, v in stock.items()},
        "menu": {k: v.id for k, v in menu.items()},
        "staff": {k: v.id for k, v in staff.items()},
        "tomorrow": (today + timedelta(days=1)).isoformat(),
    }


def _set_stock(name: str, quantity: float):
    def setup(db: Session, facts: dict) -> dict:
        item = db.get(InventoryItem, facts["stock"][name])
        item.quantity_on_hand = quantity
        db.add(item)
        db.commit()
        return facts
    return setup


def _unchanged(db: Session, facts: dict) -> dict:
    return facts


SCENARIOS: list[Scenario] = [
    Scenario(
        id="inventory-shortage",
        agent_key="inventory",
        summary="Tomatoes are below their reorder point; everything else is fine.",
        prompt="Check our stock and propose reorders for anything that needs it.",
        setup=_set_stock("tomatoes", 3),
        expected_tools={"list_low_stock_items"},
        expected_proposal=ExpectedProposal(
            action_type="inventory_reorder",
            matches=lambda p, f: p.get("inventory_item_id") == f["stock"]["tomatoes"],
            describe="a reorder for Tomatoes",
        ),
    ),
    Scenario(
        id="inventory-nothing-low",
        agent_key="inventory",
        summary="Every item is comfortably above its reorder point.",
        prompt="Check our stock and propose reorders for anything that needs it.",
        setup=_unchanged,
        expected_tools={"list_low_stock_items"},
        forbid_proposals=True,
    ),
    Scenario(
        id="marketing-slow-seller",
        agent_key="marketing",
        summary="Garlic Bread has never sold while pizza and salad sell daily.",
        prompt="Find our weakest-selling dish and propose one change to improve it.",
        setup=_unchanged,
        expected_tools={"get_slow_moving_items"},
        expected_proposal=ExpectedProposal(
            action_type="menu_change",
            matches=lambda p, f: p.get("menu_item_id") == f["menu"]["bread"],
            describe="a menu change for Garlic Bread",
        ),
    ),
    Scenario(
        id="supply-purchase-order",
        agent_key="supply_chain",
        summary="Flour is low, and Sysco Foods is its supplier.",
        prompt="Draft a purchase order for anything running low, from the supplier that stocks it.",
        setup=_set_stock("flour", 5),
        expected_tools={"list_suppliers"},
        expected_proposal=ExpectedProposal(
            action_type="purchase_order",
            matches=lambda p, f: (
                p.get("inventory_item_id") == f["stock"]["flour"]
                and p.get("supplier_id") == f["suppliers"]["sysco"]
            ),
            describe="a purchase order for Flour from Sysco Foods",
        ),
    ),
    Scenario(
        id="employee-coverage-gap",
        agent_key="employee_management",
        summary="No server is scheduled tomorrow; Ana Diaz is the only server.",
        prompt=(
            "We have no server scheduled for dinner tomorrow. Propose a shift to cover it, "
            "using someone who works as a server."
        ),
        setup=_unchanged,
        expected_tools={"list_staff"},
        expected_proposal=ExpectedProposal(
            action_type="shift_change",
            matches=lambda p, f: p.get("staff_id") == f["staff"]["ana"] and p.get("date") == f["tomorrow"],
            describe="a shift tomorrow for Ana Diaz",
        ),
    ),
    Scenario(
        id="profit-report-read-only",
        agent_key="profit",
        summary="A reporting request; the profit agent should inform, not act.",
        prompt="Summarise our revenue and margins over the last week.",
        setup=_unchanged,
        expected_tools={"get_revenue_summary"},
        forbid_proposals=True,
    ),
]
