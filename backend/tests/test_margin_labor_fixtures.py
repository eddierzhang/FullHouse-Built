from decimal import Decimal as D
from app.margin_labor import fixtures, menu_costs
from app.margin_labor.models import DishCost
from app.platform import flags, jobs
from test_restaurant_setup import _set_up


def test_the_fixture_kitchen_costs_what_it_says(client, db):
    rid = _set_up(client, db)["id"]
    flags.set_flag(db, rid, flags.ML_MENU_COSTS, True)
    db.commit()
    made = fixtures.seed_kitchen(db, rid, menu_costs.load(db, rid).today)
    db.commit()
    jobs.drain(db, queues=("derive",))
    db.commit()
    costs = {name: db.query(DishCost).filter_by(recipe_id=r.id, is_current=True).one() for name, r in made["recipes"].items()}
    assert {n: (c.plate_cost, c.food_cost_pct) for n, c in costs.items() if n != "House sauce"} == {
        "Burger": (D("2.4406"), D("0.2034")), "Fries": (D("0.4350"), D("0.0870")), "House Salad": (D("0.7067"), D("0.0785")), "Wings": (D("2.8000"), D("0.2545"))}
    assert all(c.lines_priced == c.lines_total for c in costs.values())
    # A typed invoice from the same supplier is matched by its aliases: no questions, and the price counts at once.
    body = {"supplier_name": fixtures.SUPPLIER, "invoice_date": menu_costs.load(db, rid).today.isoformat(),
            "lines": [{"description": "Ground beef", "qty": 2, "unit_price": 22.0}]}
    saved = client.post("/api/v1/prices/invoices", json=body)
    assert saved.status_code == 201, saved.text
    jobs.drain(db, queues=("derive",))
    db.commit()
    db.expire_all()
    assert db.query(DishCost).filter_by(recipe_id=made["recipes"]["Burger"].id, is_current=True).one().plate_cost == D("3.9406")
