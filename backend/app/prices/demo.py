"""Eight weeks of supplier invoices for the demo restaurant, and the demo's first digest.

Shaped so every part of the digest has something true to show: an item
another supplier sells for less, a price that climbed, a short delivery
with no credit, a wine that only trends, an item with one invoice (not
enough to compare), and three printed lines the agent can't place without
asking. Loaded by the public demo and the browser tests' seed.
"""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.prices import engine, schemas
from app.prices.models import ItemAlias, PriceItem
from app.restaurant.models import Supplier

#: Suppliers the invoices come from. The wine distributor is the demo's single-source one.
SUPPLIERS = ("Sysco Foods", "Green Valley Farms", "Harbor Dairy Co.", "Empire Wine & Spirits")

#: name, category, unit, pack unit, pack size
ITEMS = [
    ("Tomatoes, 25 lb case", "food", "lb", "case", 25),
    ("Chicken breast, 40 lb case", "food", "lb", "case", 40),
    ("Mozzarella, 6 lb case", "food", "lb", "case", 6),
    ("Flour, 50 lb bag", "dry", "lb", "bag", 50),
    ("Napkins, 3000 ct", "paper", "ct", "case", 3000),
    ("Chianti, 750 ml, 12 ct", "wine", "bottle", "case", 12),
    ("Olive oil, 4 gal case", "food", "gal", "case", 4),
    ("Romaine, 24 ct case", "food", "ct", "case", 24),
    ("Parmesan, 10 lb wheel", "food", "lb", "wheel", 10),
    ("Basil, 1 lb", "food", "lb", "each", 1),
    ("Butter, unsalted, 36 lb case", "food", "lb", "case", 36),
]

#: supplier -> printed description -> item name. What the owner has already confirmed.
ALIASES = {
    "Green Valley Farms": {"TOMATO ROMA 25#": "Tomatoes, 25 lb case", "ROMAINE 24CT": "Romaine, 24 ct case",
                           "BASIL FRESH 1#": "Basil, 1 lb"},
    "Sysco Foods": {"TOMATOES ROMA 25LB": "Tomatoes, 25 lb case", "CHIX BRST BNLS 4/10#": "Chicken breast, 40 lb case",
                    "FLOUR AP 50#": "Flour, 50 lb bag", "NAPKIN DNR 3000CT": "Napkins, 3000 ct",
                    "OLIVE OIL EV 4/1GAL": "Olive oil, 4 gal case", "LETT ROMAINE 24CT": "Romaine, 24 ct case"},
    "Harbor Dairy Co.": {"MOZZ FRESH 6/1#": "Mozzarella, 6 lb case", "PARM REGG WHL 10#": "Parmesan, 10 lb wheel"},
    "Empire Wine & Spirits": {"CHIANTI CLASSICO 12/750": "Chianti, 750 ml, 12 ct"},
}

#: Per week, 0 = eight weeks ago .. 7 = this week. Each entry: supplier, days before the
#: week's end it delivers, lines (description, qty, pack text, price per pack[, delivered qty]).
def invoices(week: int) -> list[tuple[str, int, list[tuple]]]:
    tomato_gvf = [28.80, 28.80, 29.40, 29.40, 30.00, 30.60, 31.20, 31.20][week]
    chicken = 75.60 if week < 5 else 87.60
    napkins = 26.40 if week < 6 else 29.10
    chianti = 216.00 if week < 6 else 237.00
    out = [
        ("Sysco Foods", 2, [
            ("CHIX BRST BNLS 4/10#", 3, "CS", chicken),
            ("FLOUR AP 50#", 4, "BG", 18.90),
            ("OLIVE OIL EV 4/1GAL", 1, "CS", 96.00),
            ("NAPKIN DNR 3000CT", 1, "CS", napkins),
        ]),
        ("Green Valley Farms", 1, [
            ("TOMATO ROMA 25#", 6, "CS", tomato_gvf),
            ("ROMAINE 24CT", 2, "CS", 28.10),
        ]),
        ("Harbor Dairy Co.", 4, [
            ("MOZZ FRESH 6/1#", 5, "CS", 31.90, 4) if week == 6 else ("MOZZ FRESH 6/1#", 5, "CS", 31.90),
            ("PARM REGG WHL 10#", 1, "EA", 160.00),
        ]),
    ]
    if week in (0, 1, 6, 7):
        # Sysco's tomatoes, bought now and then: the comparison that says move them.
        out[0][2].append(("TOMATOES ROMA 25LB", 2, "CS", 27.60))
    if week % 2 == 1:
        out.append(("Empire Wine & Spirits", 3, [("CHIANTI CLASSICO 12/750", 1, "CS", chianti)]))
    if week == 7:
        out[0][2].append(("LETT ROMAINE 24CT", 2, "CS", 26.00))
        out[1][2].append(("BASIL FRESH 1#", 4, "EA", 9.80))
        # Three lines nobody has confirmed yet: the agent asks, biggest first.
        out[0][2].append(("CRM HVY 12/QT", 3, "CS", 44.40))
        out[1][2].append(("KALE GRN 24CT", 4, "CS", 26.00))
        out[2][2].append(("BUTTER UNS CS", 2, "CS", 98.40))
    return out


def seed(db: Session, restaurant_id: str | None, suppliers: dict[str, str], today: date | None = None) -> dict:
    """Load the invoices for `restaurant_id`, matching through `suppliers` (name -> id), and build the digest."""
    today = today or date.today()
    items = {}
    for name, category, unit, pack_unit, pack_size in ITEMS:
        item = PriceItem(restaurant_id=restaurant_id, name=name, category=category, unit=unit, pack_unit=pack_unit,
                         pack_size=pack_size)
        db.add(item)
        items[name] = item
    db.flush()
    for supplier_name, printed in ALIASES.items():
        for description, item_name in printed.items():
            item = items[item_name]
            db.add(ItemAlias(restaurant_id=restaurant_id, supplier_id=suppliers[supplier_name],
                             description_key=engine.normalize(description), item_id=item.id, pack_size=item.pack_size))
    # Butter's alias knows the item but not the pack, so its line raises a pack question.
    db.add(ItemAlias(restaurant_id=restaurant_id, supplier_id=suppliers["Harbor Dairy Co."],
                     description_key=engine.normalize("BUTTER UNS CS"), item_id=items["Butter, unsalted, 36 lb case"].id,
                     pack_size=None))
    db.flush()

    count, questions = 0, 0
    for week in range(8):
        for supplier_name, lag, lines in invoices(week):
            # Each supplier delivers on its own day of the week; the latest round ended `lag` days ago.
            day = today - timedelta(days=(7 - week) * 7 + lag)
            supplier = db.get(Supplier, suppliers[supplier_name])
            rows = [
                schemas.LineIn(description=l[0], qty=l[1], pack_text=l[2], unit_price=l[3],
                               delivered_qty=l[4] if len(l) > 4 else None)
                for l in lines
            ]
            _, asked = engine.ingest_invoice(db, restaurant_id, supplier, day, rows, source="billcom",
                                             reference=f"{supplier_name.split()[0][:3].upper()}-{day.strftime('%m%d')}",
                                             today=today, stock=False)
            count += 1
            questions += len(asked)
    digest = engine.build_digest(db, restaurant_id, today)
    # Eight weeks of history, not eight weeks of deliveries: the stock figures stand as seeded.
    from app.prices import stock

    stock.mark_counted(db, restaurant_id)
    stock.link_items(db, restaurant_id)
    db.flush()
    return {"invoices": count, "questions": questions, "digest_total": digest.total, "cards": len(digest.cards)}
