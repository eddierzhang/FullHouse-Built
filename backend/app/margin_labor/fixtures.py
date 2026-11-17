"""A small, exact kitchen for tests, browser tests and backtests: four dishes and a sub-recipe whose costs are
round numbers, with the invoices, menu prices and sales behind them. The burger is the spec's worked example
(section 5): $2.4406 a plate, 20.3% of $12.00.

Used only by seed scripts and tests. Nothing in the app calls it.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.margin_labor.models import PosItem
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


def seed_kitchen(db: Session, restaurant_id: str, today: date, sales_days: int = 28, first_invoice_days_ago: int = 40) -> dict:
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
    return {"supplier": supplier, "items": items, "recipes": recipes, "invoice": invoice}
