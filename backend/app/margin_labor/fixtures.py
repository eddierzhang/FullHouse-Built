"""A small, exact kitchen for tests, browser tests and backtests: four dishes and a sub-recipe whose costs are
round numbers, with the invoices, menu prices and sales behind them. The burger is the spec's worked example
(section 5): $2.4406 a plate, 20.3% of $12.00.

Used only by seed scripts and tests. Nothing in the app calls it.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.margin_labor.business import zone
from app.margin_labor.models import ChannelPayout, ChannelTerms, PosItem, SalesOrder, SalesOrderLine, SupplierSchedule
from app.prices.engine import normalize
from app.prices.models import Invoice, InvoiceLine, ItemAlias, PriceItem
from app.recipes.models import CONFIRMED, Recipe, RecipeLine
from app.restaurant.models import Supplier
from app.sales.models import SalesItem

SUPPLIER = "Margin Farm"
#: name -> (invoice unit, units a pack, dollars a pack)
ITEMS = {
    "Ground beef": ("g", 1000, 12.00), "Bun": ("ct", 12, 5.40), "Onion": ("g", 1000, 2.00), "Sauce base": ("g", 1000, 6.00),
    "Potato": ("g", 1000, 1.50), "Fryer oil": ("ml", 1000, 3.00), "Lettuce": ("g", 1000, 4.00), "Chicken wings": ("g", 1000, 7.00),
}
#: dish -> (POS item id, menu price, sold a day, lines: (ingredient or sub-recipe, qty, unit, yield))
DISHES = {
    "Burger": ("BURGER", "12.00", 30, [("Ground beef", 150, "g", "1"), ("Bun", 1, "each", "1"), ("Onion", 30, "g", "0.85"), ("House sauce", 20, "g", "1")]),
    "Fries": ("FRIES", "5.00", 24, [("Potato", 200, "g", "0.8"), ("Fryer oil", 20, "ml", "1")]),
    "House Salad": ("SALAD", "9.00", 14, [("Lettuce", 150, "g", "0.9"), ("Onion", 20, "g", "1")]),
    "Wings": ("WINGS", "11.00", 10, [("Chicken wings", 400, "g", "1")]),
}


#: Packs on the weekly delivery, a little over what a week of sales uses. Wings also come on Mondays: 40 kg a
#: week bought against 28 kg used, which is the kitchen's one unexplained gap ($49.00 a week).
WEEKLY = {"Ground beef": 32, "Bun": 18, "Onion": 10, "Sauce base": 5, "Potato": 42, "Fryer oil": 4, "Lettuce": 17, "Chicken wings": 20}
EXTRA = {"Chicken wings": 20}


def seed_deliveries(db: Session, restaurant_id: str, supplier, items: dict, week_end: date, weeks: int = 12, extra_weeks: int | None = None) -> None:
    """A delivery every Thursday, and wings again every Monday, for the `weeks` ending on the Sunday `week_end`."""
    def invoice(day: date, packs: dict, reference: str) -> None:
        row = Invoice(restaurant_id=restaurant_id, supplier_id=supplier.id, invoice_date=day, status="complete", reference=reference,
                      total=sum(q * ITEMS[n][2] for n, q in packs.items()))
        db.add(row)
        db.flush()
        for number, (name, qty) in enumerate(packs.items(), start=1):
            db.add(InvoiceLine(restaurant_id=restaurant_id, invoice_id=row.id, supplier_id=supplier.id, line_no=number, description=name, qty=qty,
                               unit_price=ITEMS[name][2], extended=qty * ITEMS[name][2], item_id=items[name].id, pack_size=ITEMS[name][1]))

    for k in range(weeks - 1, -1, -1):
        if extra_weeks is None or k < extra_weeks:
            invoice(week_end - timedelta(days=6 + 7 * k), EXTRA, f"MF-M{k:02d}")
        invoice(week_end - timedelta(days=3 + 7 * k), WEEKLY, f"MF-T{k:02d}")
    db.flush()


def seed_kitchen(db: Session, restaurant_id: str, today: date, sales_days: int = 90, first_invoice_days_ago: int = 40, deliveries: bool = True) -> dict:
    """Write the kitchen for one restaurant. Returns the rows by name."""
    supplier = Supplier(name=SUPPLIER, restaurant_id=restaurant_id, lead_time_days=2)
    db.add(supplier)
    db.flush()
    items = {}
    for name, (unit, pack, _price) in ITEMS.items():
        items[name] = PriceItem(restaurant_id=restaurant_id, name=name, unit=unit, pack_unit="case", pack_size=pack)
        db.add(items[name])
    db.flush()
    for name, item in items.items():
        # The supplier's printed line is the item's name: a later invoice typed with it is matched without a question.
        db.add(ItemAlias(restaurant_id=restaurant_id, supplier_id=supplier.id, description_key=normalize(name), item_id=item.id, pack_size=item.pack_size))
    invoice = Invoice(restaurant_id=restaurant_id, supplier_id=supplier.id, invoice_date=today - timedelta(days=first_invoice_days_ago), status="complete",
                      reference="MF-1001", total=sum(2 * p for _u, _k, p in ITEMS.values()))
    db.add(invoice)
    db.flush()
    for number, (name, (_unit, pack, price)) in enumerate(ITEMS.items(), start=1):
        db.add(InvoiceLine(restaurant_id=restaurant_id, invoice_id=invoice.id, supplier_id=supplier.id, line_no=number, description=name, qty=2,
                           unit_price=price, extended=2 * price, item_id=items[name].id, pack_size=pack))
    sauce = Recipe(restaurant_id=restaurant_id, name="House sauce", yield_qty=500, yield_unit="g", status=CONFIRMED, source="user")
    db.add(sauce)
    db.flush()
    db.add(RecipeLine(restaurant_id=restaurant_id, recipe_id=sauce.id, position=0, item_id=items["Sauce base"].id, qty=500, unit="g", ingredient_text="Sauce base"))
    recipes = {"House sauce": sauce}
    for name, (pos_id, price, sold, lines) in DISHES.items():
        recipe = Recipe(restaurant_id=restaurant_id, name=name, pos_item_id=pos_id, yield_qty=1, yield_unit="plate", status=CONFIRMED, source="user")
        db.add(recipe)
        db.flush()
        recipes[name] = recipe
        for position, (ingredient, qty, unit, yield_pct) in enumerate(lines):
            db.add(RecipeLine(restaurant_id=restaurant_id, recipe_id=recipe.id, position=position, qty=qty, unit=unit, ingredient_text=ingredient,
                              item_id=items[ingredient].id if ingredient in items else None, sub_recipe_id=sauce.id if ingredient == "House sauce" else None,
                              yield_pct=Decimal(yield_pct)))
        db.add(PosItem(restaurant_id=restaurant_id, pos_item_id=pos_id, name=name, category="Food", price=Decimal(price)))
        for back in range(1, sales_days + 1):
            db.add(SalesItem(restaurant_id=restaurant_id, date=today - timedelta(days=back), pos_item_id=pos_id, pos_item_name=name, qty=sold,
                             net=float(Decimal(price) * sold), source="pos"))
    db.flush()
    if deliveries:
        seed_deliveries(db, restaurant_id, supplier, items, today - timedelta(days=today.weekday() + 1))
    return {"supplier": supplier, "items": items, "recipes": recipes, "invoice": invoice}


#: platform -> (commission, processing, fixed fee an order, packaging an order)
TERMS = {"DoorDash": ("0.25", "0.03", "0.30", "0.70"), "Uber Eats": ("0.30", "0.03", "2.00", "1.00")}


def seed_orders(db: Session, restaurant_id: str, today: date, tz: str, days: int = 35, terms: bool = True) -> None:
    """Order-level sales for the last `days` business dates, the same every day: two dine-in orders (two burgers and
    a wings), three DoorDash orders (a burger at $13.00 and a wings at $12.00; two of the three have a payout row)
    and one Uber Eats order (fries at $5.00, no payout rows). Fries lose 8.5 cents an order on Uber Eats."""
    def order(day: date, external: str, channel: str, platform: str | None, lines: list[tuple[str, str, int, str]], covers: int | None) -> None:
        opened = datetime.combine(day, time(18, 0), tzinfo=zone(tz)).astimezone(timezone.utc)
        net = sum((Decimal(price) * qty for _i, _n, qty, price in lines), Decimal(0))
        row = SalesOrder(restaurant_id=restaurant_id, connection_key="fixture", external_id=external, opened_at=opened, closed_at=opened + timedelta(minutes=40),
                         business_date=day, channel=channel, platform=platform, covers=covers, gross=net, net=net, status="closed")
        db.add(row)
        db.flush()
        for number, (pos_id, name, qty, price) in enumerate(lines, start=1):
            db.add(SalesOrderLine(restaurant_id=restaurant_id, order_id=row.id, line_no=number, business_date=day, pos_item_id=pos_id, pos_item_name=name,
                                  qty=Decimal(qty), net=Decimal(price) * qty))

    for back in range(days, 0, -1):
        day = today - timedelta(days=back)
        for n in range(2):
            order(day, f"IN-{day.isoformat()}-{n}", "dine_in", None, [("BURGER", "Burger", 2, "12.00"), ("WINGS", "Wings", 1, "11.00")], 2)
        for n in range(3):
            order(day, f"DD-{day.isoformat()}-{n}", "delivery_platform", "DoorDash", [("BURGER", "Burger", 1, "13.00"), ("WINGS", "Wings", 1, "12.00")], None)
            if n < 2:
                db.add(ChannelPayout(restaurant_id=restaurant_id, platform="DoorDash", external_order_id=f"DD-{day.isoformat()}-{n}", gross=Decimal("25.00"),
                                     commission=Decimal("6.25"), fees=Decimal("0.75"), payout=Decimal("18.00"),
                                     paid_at=datetime.combine(day + timedelta(days=2), time(12, 0), tzinfo=timezone.utc)))
        order(day, f"UE-{day.isoformat()}", "delivery_platform", "Uber Eats", [("FRIES", "Fries", 1, "5.00")], None)
    if terms:
        for platform, (commission, processing, fixed, packaging) in TERMS.items():
            db.add(ChannelTerms(restaurant_id=restaurant_id, platform=platform, commission_pct=Decimal(commission), processing_pct=Decimal(processing),
                                fixed_fee_per_order=Decimal(fixed), packaging_cost_per_order=Decimal(packaging), effective_from=date(2000, 1, 1)))
    db.flush()


def seed_schedule(db: Session, restaurant_id: str, supplier_id: str, minimum: str | None = None) -> None:
    """The supplier delivers on Thursdays (order in by Wednesday 14:00) and Mondays (by Sunday 14:00)."""
    for cutoff, delivery in ((2, 3), (6, 0)):
        db.add(SupplierSchedule(restaurant_id=restaurant_id, supplier_id=supplier_id, order_cutoff_weekday=cutoff, order_cutoff_time="14:00", delivery_weekday=delivery,
                                minimum_order_amount=None if minimum is None else Decimal(minimum)))
    db.flush()
