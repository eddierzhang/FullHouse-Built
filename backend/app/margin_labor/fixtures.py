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
from app.margin_labor.models import ChannelPayout, ChannelTerms, LaborPunch, PosItem, SalesOrder, SalesOrderLine, SupplierSchedule
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


def seed_deliveries(db: Session, restaurant_id: str, supplier, items: dict, week_end: date, weeks: int = 12, extra_weeks: int | None = None, scale: int = 1) -> None:
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
            invoice(week_end - timedelta(days=6 + 7 * k), {n: q * scale for n, q in EXTRA.items()}, f"MF-M{k:02d}")
        invoice(week_end - timedelta(days=3 + 7 * k), {n: q * scale for n, q in WEEKLY.items()}, f"MF-T{k:02d}")
    db.flush()


def seed_kitchen(db: Session, restaurant_id: str, today: date, sales_days: int = 90, first_invoice_days_ago: int = 40, deliveries: bool = True, scale: int = 1) -> dict:
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
            db.add(SalesItem(restaurant_id=restaurant_id, date=today - timedelta(days=back), pos_item_id=pos_id, pos_item_name=name, qty=sold * scale,
                             net=float(Decimal(price) * sold * scale), source="pos"))
    db.flush()
    if deliveries:
        seed_deliveries(db, restaurant_id, supplier, items, today - timedelta(days=today.weekday() + 1), scale=scale)
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


#: (first slot, last slot exclusive, covers an order) for each service; an order every 15 minutes.
LUNCH = (time(11, 30), 10, 4)
DINNER = (time(17, 0), 16, 6)
BUSY_DINNER_COVERS = 8   # Fridays and Saturdays
#: role -> (lunch heads, dinner heads, busy dinner heads), with the hours each service is staffed
STAFFING = {"server": (2, 3, 4), "line_cook": (1, 2, 2)}
LUNCH_SHIFT = (time(11, 0), time(14, 30))
DINNER_SHIFT = (time(16, 30), time(21, 30))


def seed_service(db: Session, restaurant_id: str, today: date, tz: str, days: int = 70) -> None:
    """Timestamped dine-in orders and punches, the same every week: an order every 15 minutes through lunch (4 covers)
    and dinner (6 covers; 8 on Fridays and Saturdays), each cover one burger at $20. Servers and line cooks are
    clocked so that a server always handles 8 covers an hour: 2 at lunch, 3 at dinner, 4 on a busy dinner."""
    local = zone(tz)
    for back in range(days, 0, -1):
        day = today - timedelta(days=back)
        busy = day.weekday() in (4, 5)
        for name, (start, count, covers) in (("L", LUNCH), ("D", DINNER)):
            covers = BUSY_DINNER_COVERS if busy and name == "D" else covers
            for n in range(count):
                opened = (datetime.combine(day, start, tzinfo=local) + timedelta(minutes=15 * n)).astimezone(timezone.utc)
                net = Decimal(20 * covers)
                order = SalesOrder(restaurant_id=restaurant_id, connection_key="fixture", external_id=f"SV-{day.isoformat()}-{name}{n}", opened_at=opened,
                                   closed_at=opened + timedelta(minutes=50), business_date=day, channel="dine_in", covers=covers, gross=net, net=net, status="closed")
                db.add(order)
                db.flush()
                db.add(SalesOrderLine(restaurant_id=restaurant_id, order_id=order.id, line_no=1, business_date=day, pos_item_id="BURGER", pos_item_name="Burger",
                                      qty=Decimal(covers), net=net))
        _punches(db, restaurant_id, day, local)
    db.flush()


#: Who works which service in the sample restaurant: (role, service) -> names, in the order heads are added.
ON_SHIFT = {("server", "L"): ("Ana Rivera", "Ben Tran"), ("server", "D"): ("Cal Ortiz", "Dee Shah", "Eve Park", "Fay Lund"),
            ("line_cook", "L"): ("Gil Moreno",), ("line_cook", "D"): ("Hal Brandt", "Ivy Chen")}


def _punches(db: Session, restaurant_id: str, day: date, local, named: bool = False) -> None:
    """One day's clock-ins: two servers at lunch, three at dinner (four on Friday and Saturday), and the cooks.
    `named` clocks the people of `TEAM` in; otherwise the punches say "Server 1" and so on."""
    busy = day.weekday() in (4, 5)
    for role, (lunch, dinner, busy_dinner) in STAFFING.items():
        for service, (start, end), heads in (("L", LUNCH_SHIFT, lunch), ("D", DINNER_SHIFT, busy_dinner if busy else dinner)):
            for head in range(heads):
                who = ON_SHIFT[(role, service)][head] if named else f"{role.replace('_', ' ').title()} {head + 1}"
                db.add(LaborPunch(restaurant_id=restaurant_id, employee_name=who, role_key=role,
                                  clock_in=datetime.combine(day, start, tzinfo=local).astimezone(timezone.utc),
                                  clock_out=datetime.combine(day, end, tzinfo=local).astimezone(timezone.utc), business_date=day, source="csv",
                                  external_id=f"SV-{day.isoformat()}-{role}-{service}{head}"))


#: name -> (role on the staff list, hourly rate or None). One cook has no rate on file: the role's median costs her shifts.
TEAM = {"Ana Rivera": ("Server", "15.00"), "Ben Tran": ("Server", "15.00"), "Cal Ortiz": ("Server", "16.00"), "Dee Shah": ("Server", "15.00"), "Eve Park": ("Server", "17.00"),
        "Fay Lund": ("Server", "15.00"), "Gil Moreno": ("Line Cook", "20.00"), "Hal Brandt": ("Line Cook", "22.00"), "Ivy Chen": ("Line Cook", None)}


def seed_team(db: Session, restaurant_id: str) -> dict:
    """Nine people on the staff list, with the rates scheduling costs a week by. Returns the staff rows by name."""
    from app.margin_labor.models import EmployeeWage
    from app.restaurant.models import Staff

    out = {}
    for name, (role, rate) in TEAM.items():
        out[name] = Staff(restaurant_id=restaurant_id, name=name, role=role)
        db.add(out[name])
    db.flush()
    for name, (_role, rate) in TEAM.items():
        if rate is not None:
            db.add(EmployeeWage(restaurant_id=restaurant_id, staff_id=out[name].id, hourly_rate=Decimal(rate)))
    db.flush()
    return out


#: (people, role, start minute, end minute, weekdays or None for every day)
WEEK_SHIFTS = (
    (("Ana Rivera", "Ben Tran"), "server", 11 * 60, 15 * 60, None),
    (("Cal Ortiz", "Dee Shah", "Eve Park", "Fay Lund"), "server", 16 * 60 + 30, 21 * 60 + 30, None),
    (("Gil Moreno",), "line_cook", 10 * 60 + 30, 14 * 60 + 30, None),
    (("Hal Brandt", "Ivy Chen"), "line_cook", 16 * 60, 21 * 60 + 30, None),
)


def seed_week_schedule(db: Session, restaurant_id: str, team: dict, week_of: date, tz: str):
    """A published schedule for one week, the same every day: two servers at lunch, four at dinner, and the cooks.
    Written by hand so that who is on at any minute is known exactly. Four at dinner is one more than a slow
    night needs even if it turns busy, which is what a cut suggestion looks for."""
    from app.margin_labor.models import Schedule, ScheduleShift

    local = zone(tz)
    schedule = Schedule(restaurant_id=restaurant_id, week_of=week_of, status="published", version=1, published_at=datetime.now(timezone.utc), published_by="fixture",
                        generated_at=datetime.now(timezone.utc))
    db.add(schedule)
    db.flush()
    for n in range(7):
        day = week_of + timedelta(days=n)
        midnight = datetime.combine(day, time(0, 0), tzinfo=local)
        for names, role, start, end, weekdays in WEEK_SHIFTS:
            if weekdays is not None and day.weekday() not in weekdays:
                continue
            for name in names:
                db.add(ScheduleShift(restaurant_id=restaurant_id, schedule_id=schedule.id, staff_id=team[name].id, role_key=role, business_date=day,
                                     start_at=(midnight + timedelta(minutes=start)).astimezone(timezone.utc), end_at=(midnight + timedelta(minutes=end)).astimezone(timezone.utc)))
    db.flush()
    return schedule


def seed_today(db: Session, restaurant_id: str, day: date, tz: str, until: time, covers_factor: float) -> None:
    """Today's orders up to `until`, at `covers_factor` times the usual covers: a slow night at 0.5, a busy one at 2."""
    local = zone(tz)
    busy = day.weekday() in (4, 5)
    for name, (start, count, covers) in (("L", LUNCH), ("D", DINNER)):
        covers = BUSY_DINNER_COVERS if busy and name == "D" else covers
        for n in range(count):
            opened = datetime.combine(day, start, tzinfo=local) + timedelta(minutes=15 * n)
            if opened.time() > until:
                continue
            guests = max(1, round(covers * covers_factor))
            net = Decimal(20 * guests)
            order = SalesOrder(restaurant_id=restaurant_id, connection_key="fixture", external_id=f"SV-{day.isoformat()}-{name}{n}", opened_at=opened.astimezone(timezone.utc),
                               business_date=day, channel="dine_in", covers=guests, gross=net, net=net, status="open")
            db.add(order)
            db.flush()
            db.add(SalesOrderLine(restaurant_id=restaurant_id, order_id=order.id, line_no=1, business_date=day, pos_item_id="BURGER", pos_item_name="Burger",
                                  qty=Decimal(guests), net=net))
    db.flush()


# --- one restaurant with everything: the preview demo ---------------------------------------------------

DEMO_SCALE = 3


def _spread(counts: dict[str, int], orders: int) -> list[dict[str, int]]:
    """Share each dish's units evenly across `orders` orders, the same way every time."""
    units = sorted(((k + 0.5) / n, pos) for pos, n in counts.items() for k in range(n))
    out: list[dict[str, int]] = [{} for _ in range(orders)]
    for index, (_at, pos) in enumerate(units):
        out[index % orders][pos] = out[index % orders].get(pos, 0) + 1
    return out


def seed_trading_days(db: Session, restaurant_id: str, today: date, tz: str, days: int = 70, scale: int = DEMO_SCALE, until: time | None = None) -> None:
    """Order-level sales and punches for a whole restaurant, the same every day and consistent with `seed_kitchen` at the
    same scale: dine-in orders every 15 minutes through lunch and dinner at menu prices, DoorDash orders (a burger and a
    wings; two in three have a payout row) and Uber Eats orders (fries, on contract rates), which add up to exactly what
    the kitchen's daily sales say was sold. With `until`, today's orders so far are written too, with one dine-in
    order in seven missing: a slightly slow day."""
    local = zone(tz)
    names = {pos: name for name, (pos, _p, _s, _l) in DISHES.items()}
    price = {pos: Decimal(p) for _name, (pos, p, _s, _l) in DISHES.items()}
    sold = {pos: n * scale for _name, (pos, _p, n, _l) in DISHES.items()}
    doordash, uber = 3 * scale, scale
    dine_in = {"BURGER": sold["BURGER"] - doordash, "FRIES": sold["FRIES"] - uber, "SALAD": sold["SALAD"], "WINGS": sold["WINGS"] - doordash}
    slots = [(LUNCH[0], n, LUNCH[2]) for n in range(LUNCH[1])] + [(DINNER[0], n, DINNER[2]) for n in range(DINNER[1])]
    baskets = _spread(dine_in, len(slots))

    def order(day: date, external: str, at: datetime, channel: str, platform: str | None, lines: list[tuple[str, int, Decimal]], covers: int | None) -> None:
        net = sum((p * q for _pos, q, p in lines), Decimal(0))
        row = SalesOrder(restaurant_id=restaurant_id, connection_key="fixture", external_id=external, opened_at=at.astimezone(timezone.utc),
                         closed_at=(at + timedelta(minutes=45)).astimezone(timezone.utc), business_date=day, channel=channel, platform=platform, covers=covers,
                         gross=net, net=net, status="closed")
        db.add(row)
        db.flush()
        for number, (pos, qty, each) in enumerate(lines, start=1):
            db.add(SalesOrderLine(restaurant_id=restaurant_id, order_id=row.id, line_no=number, business_date=day, pos_item_id=pos, pos_item_name=names[pos],
                                  qty=Decimal(qty), net=each * qty))

    def trade(day: date, so_far: time | None = None) -> None:
        busy = day.weekday() in (4, 5)
        for index, (start, n, covers) in enumerate(slots):
            covers = BUSY_DINNER_COVERS if busy and start == DINNER[0] else covers
            at = datetime.combine(day, start, tzinfo=local) + timedelta(minutes=15 * n)
            if so_far is not None and (at.time() > so_far or index % 7 == 6):
                continue
            order(day, f"IN-{day.isoformat()}-{index:02d}", at, "dine_in", None, [(pos, qty, price[pos]) for pos, qty in sorted(baskets[index].items())], covers)
        for n in range(doordash):
            at = datetime.combine(day, time(12, 0) if n % 2 == 0 else time(18, 0), tzinfo=local) + timedelta(minutes=10 * n)
            if so_far is not None and at.time() > so_far:
                continue
            order(day, f"DD-{day.isoformat()}-{n}", at, "delivery_platform", "DoorDash", [("BURGER", 1, Decimal("13.00")), ("WINGS", 1, Decimal("12.00"))], None)
            if n % 3 != 2 and so_far is None:
                db.add(ChannelPayout(restaurant_id=restaurant_id, platform="DoorDash", external_order_id=f"DD-{day.isoformat()}-{n}", gross=Decimal("25.00"),
                                     commission=Decimal("6.25"), fees=Decimal("0.75"), payout=Decimal("18.00"),
                                     paid_at=datetime.combine(day + timedelta(days=2), time(12, 0), tzinfo=timezone.utc)))
        for n in range(uber):
            at = datetime.combine(day, time(12, 15) if n % 2 == 0 else time(18, 45), tzinfo=local) + timedelta(minutes=20 * n)
            if so_far is not None and at.time() > so_far:
                continue
            order(day, f"UE-{day.isoformat()}-{n}", at, "delivery_platform", "Uber Eats", [("FRIES", 1, Decimal("5.00"))], None)
        if so_far is None:
            _punches(db, restaurant_id, day, local, named=True)

    for back in range(days, 0, -1):
        trade(today - timedelta(days=back))
    if until is not None:
        trade(today, until)
    for platform, (commission, processing, fixed, packaging) in TERMS.items():
        db.add(ChannelTerms(restaurant_id=restaurant_id, platform=platform, commission_pct=Decimal(commission), processing_pct=Decimal(processing),
                            fixed_fee_per_order=Decimal(fixed), packaging_cost_per_order=Decimal(packaging), effective_from=date(2000, 1, 1)))
    db.flush()


def seed_preview(db: Session, restaurant_id: str, today: date, tz: str, now_local: datetime | None = None) -> dict:
    """Everything for one demo restaurant with all five modules live: the kitchen at three times the test volume, order-level
    sales through the day on three channels, punches, a team with rates, this week's schedule published, a beef price
    that rose two days ago, and today's sales so far running a little under the forecast."""
    from app.margin_labor.models import LaborRules

    made = seed_kitchen(db, restaurant_id, today, scale=DEMO_SCALE)
    seed_schedule(db, restaurant_id, made["supplier"].id, minimum="150")
    until = None
    if now_local is not None:
        # After midnight the business date is still yesterday: the whole day has traded.
        until = time(23, 59) if now_local.date() > today else now_local.time()
    seed_trading_days(db, restaurant_id, today, tz, until=until)
    team = seed_team(db, restaurant_id)
    db.add(LaborRules(restaurant_id=restaurant_id, weekly_overtime_hours=40, daily_overtime_hours=8, min_rest_hours=10, min_shift_hours=4, max_shift_hours=8,
                      max_hours_per_day=10, max_consecutive_days=6, break_after_hours=6, break_minutes=30, min_paid_hours=1, advance_notice_days=3, allow_on_call=False,
                      confirmed_by="the owner", confirmed_at=datetime.now(timezone.utc)))
    seed_week_schedule(db, restaurant_id, team, today - timedelta(days=today.weekday()), tz)
    # Ground beef went from $12.00 to $22.00 a kilo two days ago: the burger passes the food cost target.
    beef = made["items"]["Ground beef"]
    rise = Invoice(restaurant_id=restaurant_id, supplier_id=made["supplier"].id, invoice_date=today - timedelta(days=2), status="complete", reference="MF-2040", total=44.0)
    db.add(rise)
    db.flush()
    db.add(InvoiceLine(restaurant_id=restaurant_id, invoice_id=rise.id, supplier_id=made["supplier"].id, line_no=1, description="Ground beef", qty=2, unit_price=22.0,
                       extended=44.0, item_id=beef.id, pack_size=1000))
    db.flush()
    return {**made, "team": team}


#: ingredient -> (unit on the Inventory page, invoice units in one of them)
STOCK_UNITS = {"Ground beef": ("kg", 1000), "Bun": ("each", 1), "Onion": ("kg", 1000), "Sauce base": ("kg", 1000), "Potato": ("kg", 1000), "Fryer oil": ("L", 1000),
               "Lettuce": ("kg", 1000), "Chicken wings": ("kg", 1000)}
CATEGORIES = {"Burger": "Mains", "Fries": "Sides", "House Salad": "Salads", "Wings": "Starters"}


def seed_books(db: Session, restaurant_id: str, made: dict, today: date, tz: str, scale: int = DEMO_SCALE) -> dict:
    """The same restaurant on the older pages, written from what `seed_preview` and the recost already hold, so every
    page agrees: the Inventory items are the invoice items (stock on hand is the last Thursday delivery less what the
    recipes used since), the Menu dishes are the recipes (same price; cost is the current cost per plate), the
    orders behind Profit & forecast are the point-of-sale orders line for line, and the shifts on the staff page
    are the published schedule. Call after `menu_costs.recost`."""
    from app.margin_labor.models import DishCost, Schedule, ScheduleShift
    from app.restaurant.models import InventoryItem, MenuItem, Order, OrderItem, RecipeItem, Shift
    from app.stock import ledger

    local = zone(tz)
    supplier, items, recipes = made["supplier"], made["items"], made["recipes"]
    supplier.contact_info = "orders@marginfarm.example · 555 0142"
    latest = {name: ITEMS[name][2] for name in ITEMS}
    latest["Ground beef"] = 22.00  # the price on the newest invoice
    # What one plate takes of each ingredient, in invoice units, with trim and the sauce's own recipe followed.
    per_plate: dict[str, dict[str, float]] = {}
    for dish, (_pos, _price, _sold, lines) in DISHES.items():
        per_plate[dish] = {}
        for ingredient, qty, _unit, yield_pct in lines:
            name = "Sauce base" if ingredient == "House sauce" else ingredient
            per_plate[dish][name] = per_plate[dish].get(name, 0.0) + qty / float(yield_pct)
    daily = {name: sum(per_plate[dish].get(name, 0.0) * DISHES[dish][2] * scale for dish in DISHES) for name in ITEMS}
    since_delivery = (today.weekday() - 3) % 7  # days of service since Thursday's delivery
    stock = {}
    for name, (unit, pack, _price) in ITEMS.items():
        shown, per = STOCK_UNITS[name]
        use = daily[name] / per
        # Three days carried over from the week before, except the wings: bought twice a week and still short,
        # which is the gap the Usage page reports.
        carried = 0.5 if name == "Chicken wings" else 3
        on_hand = round(max(WEEKLY[name] * scale * pack / per - use * since_delivery, 0) + use * carried, 1)
        stock[name] = InventoryItem(restaurant_id=restaurant_id, name=name, unit=shown, quantity_on_hand=0, reorder_threshold=round(use * 2, 1),
                                    reorder_qty=float(WEEKLY[name] * scale * pack / per), supplier_id=supplier.id, unit_cost=round(latest[name] / (pack / per), 4),
                                    pack_unit="case", pack_size=pack / per)
        db.add(stock[name])
        db.flush()
        ledger.move(db, stock[name], on_hand, "delivery", source_key=f"sample-opening-{name}", note="Opening stock for the sample restaurant")
    dishes = {}
    for dish, (pos, price, _sold, _lines) in DISHES.items():
        cost = db.query(DishCost).filter_by(restaurant_id=restaurant_id, recipe_id=recipes[dish].id, is_current=True).first()
        dishes[pos] = MenuItem(restaurant_id=restaurant_id, name=dish, category=CATEGORIES[dish], price=float(price), cost=round(float(cost.plate_cost), 2) if cost else 0.0)
        db.add(dishes[pos])
        db.flush()
        for name, amount in per_plate[dish].items():
            db.add(RecipeItem(menu_item_id=dishes[pos].id, inventory_item_id=stock[name].id, quantity=round(amount / STOCK_UNITS[name][1], 6)))
    for sale in db.query(SalesOrder).filter_by(restaurant_id=restaurant_id).order_by(SalesOrder.opened_at).all():
        order = Order(restaurant_id=restaurant_id, status="completed", channel="dine_in" if sale.channel == "dine_in" else "delivery", total=float(sale.net),
                      created_at=sale.opened_at)
        db.add(order)
        db.flush()
        for line in db.query(SalesOrderLine).filter_by(order_id=sale.id).order_by(SalesOrderLine.line_no).all():
            db.add(OrderItem(order_id=order.id, menu_item_id=dishes[line.pos_item_id].id, qty=int(line.qty), unit_price=float(line.net / line.qty)))
    titles = {"server": "Server", "line_cook": "Line Cook"}
    for schedule in db.query(Schedule).filter_by(restaurant_id=restaurant_id, status="published").all():
        for shift in db.query(ScheduleShift).filter_by(schedule_id=schedule.id).all():
            db.add(Shift(restaurant_id=restaurant_id, staff_id=shift.staff_id, date=shift.business_date, start_time=shift.start_at.astimezone(local).time(),
                         end_time=shift.end_at.astimezone(local).time(), role=titles.get(shift.role_key, shift.role_key), published_at=schedule.published_at))
    db.flush()
    _weekly_books(db, restaurant_id, today)
    return {"stock": stock, "dishes": dishes}


def _weekly_books(db: Session, restaurant_id: str, today: date) -> None:
    """Sales and labor by day, and prime cost by week, from the same orders and clock-ins: each day's sales are its
    orders, each day's labor is its punched hours at each person's rate (the role's median where none is on file)."""
    from statistics import median

    from sqlalchemy import func

    from app.margin_labor.models import EmployeeWage
    from app.restaurant.models import Staff
    from app.sales import pnl
    from app.sales.models import LaborDay, SalesDay

    for day, net, covers in (db.query(SalesOrder.business_date, func.sum(SalesOrder.net), func.sum(SalesOrder.covers))
                             .filter(SalesOrder.restaurant_id == restaurant_id, SalesOrder.business_date < today).group_by(SalesOrder.business_date)):
        db.add(SalesDay(restaurant_id=restaurant_id, date=day, net_sales=float(net), gross_sales=float(net), covers=int(covers or 0), source="pos"))
    rates = {name: float(rate) for name, rate in db.query(Staff.name, EmployeeWage.hourly_rate).join(EmployeeWage, EmployeeWage.staff_id == Staff.id)
             .filter(Staff.restaurant_id == restaurant_id)}
    by_role: dict[str, list[float]] = {}
    for (role, _service), names in ON_SHIFT.items():
        by_role.setdefault(role, []).extend(rates[n] for n in names if n in rates)
    days: dict[date, list[float]] = {}
    for punch in db.query(LaborPunch).filter(LaborPunch.restaurant_id == restaurant_id, LaborPunch.business_date < today):
        hours = (punch.clock_out - punch.clock_in).total_seconds() / 3600
        rate = rates.get(punch.employee_name, median(by_role.get(punch.role_key) or [0.0]))
        cell = days.setdefault(punch.business_date, [0.0, 0.0])
        cell[0] += hours
        cell[1] += hours * rate
    for day, (hours, dollars) in days.items():
        db.add(LaborDay(restaurant_id=restaurant_id, date=day, hours=round(hours, 2), dollars=round(dollars, 2), source="payroll"))
    db.flush()
    week = pnl.last_complete_week(today)
    for back in range(8):
        pnl.compute_week(db, restaurant_id, week - timedelta(days=7 * back))
    db.flush()
