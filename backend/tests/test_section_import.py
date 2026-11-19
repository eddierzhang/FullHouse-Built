"""A restaurant's own CSVs: any of the kinds the app reads is recognised from its column names, checked row by
row, and entered. The same file twice changes nothing; a file with a bad row enters nothing; an unclear file
enters nothing until a person says what it is."""

import json

from app.audit.models import ChangeLog
from app.margin_labor.models import EmployeeWage
from app.prices.models import Invoice, InvoiceLine
from app.restaurant.models import InventoryItem, MenuItem, Staff, Supplier
from test_restaurant_setup import _set_up

API = "/api/v1/imports"

SUPPLIERS = "Supplier,Contact,Lead time days\nGreen Valley Farms,orders@greenvalley.example,2\nHarbor Dairy,rep@harbordairy.example,1\n"
DISHES = "Dish,Category,Price,Cost,Description\nMargherita Pizza,Entree,$14.00,2.50,Tomato and mozzarella\nCaesar Salad,Salad,11,1.5,\n"
STAFF = "Name,Role,Email,Phone,Hire date,Hourly rate\nAlex Rivera,Server,alex@example.com,555 0110,2025-03-01,15.00\nJordan Lee,Chef,,,9/15/2024,22.50\n"
INVOICES = ("Supplier,Invoice number,Date,Description,Qty,Pack,Unit price,Amount\n"
            "Sysco Foods,SYS-1120,2026-11-02,CHKN BRST 40LB,2,40 lb case,87.60,175.20\n"
            "Sysco Foods,SYS-1120,2026-11-02,FLOUR 50LB,3,50 lb bag,18.90,56.70\n"
            "Harbor Dairy,HAR-88,2026-11-03,MOZZ 6LB,4,6 lb case,31.90,127.60\n")


def test_each_kind_is_recognised_and_its_rows_are_entered_once(client, db):
    rid = _set_up(client, db)["id"]

    done = client.post(API, json={"csv": SUPPLIERS}).json()
    assert (done["kind"], done["confidence"], done["saved"], done["new"], done["section"]) == ("suppliers", 1.0, True, 2, "Suppliers & ordering")
    farm = db.query(Supplier).filter_by(restaurant_id=rid, name="Green Valley Farms").one()
    assert (farm.contact_info, farm.lead_time_days) == ("orders@greenvalley.example", 2)

    done = client.post(API, json={"csv": DISHES}).json()
    assert (done["kind"], done["saved"], done["new"]) == ("dishes", True, 2)
    pizza = db.query(MenuItem).filter_by(restaurant_id=rid, name="Margherita Pizza").one()
    assert (pizza.category, pizza.price, pizza.cost) == ("Entree", 14.0, 2.5)

    done = client.post(API, json={"csv": STAFF}).json()
    assert (done["kind"], done["saved"], done["new"], done["rates_set"]) == ("staff", True, 2, 2)
    alex = db.query(Staff).filter_by(restaurant_id=rid, name="Alex Rivera").one()
    assert (alex.role, alex.email, alex.hire_date.isoformat()) == ("Server", "alex@example.com", "2025-03-01")
    assert str(db.query(EmployeeWage).filter_by(staff_id=alex.id).one().hourly_rate) == "15.0000"

    done = client.post(API, json={"csv": INVOICES}).json()
    assert (done["kind"], done["saved"], done["new"], done["invoices"]) == ("invoices", True, 3, 2)
    assert db.query(Invoice).filter_by(restaurant_id=rid).count() == 2 and db.query(InvoiceLine).filter_by(restaurant_id=rid).count() == 3
    assert done["questions"] >= 1  # lines it cannot place yet are asked about, as with a typed invoice

    # The same four files again: nothing new, nothing changed.
    before = (db.query(Supplier).count(), db.query(MenuItem).count(), db.query(Staff).count(), db.query(Invoice).count(), db.query(InvoiceLine).count())
    for text in (SUPPLIERS, DISHES, STAFF, INVOICES):
        again = client.post(API, json={"csv": text}).json()
        assert again["saved"] is True and again["new"] == 0 and again["updated"] == 0 and again["skipped"] == len(text.strip().splitlines()) - 1
    assert (db.query(Supplier).count(), db.query(MenuItem).count(), db.query(Staff).count(), db.query(Invoice).count(), db.query(InvoiceLine).count()) == before


def test_stock_is_recognised_and_goes_through_the_stock_import(client, db):
    rid = _set_up(client, db)["id"]
    done = client.post(API, json={"csv": "Item,Unit,On hand,Reorder at,Unit cost,Supplier\nTomatoes,lb,40,15,2.90,Green Valley Farms\n"}).json()
    assert (done["kind"], done["saved"], done["new"], done["new_suppliers"]) == ("inventory", True, 1, ["Green Valley Farms"])
    assert db.query(InventoryItem).filter_by(restaurant_id=rid, name="Tomatoes").one().quantity_on_hand == 40


def test_a_bad_row_enters_nothing_and_says_which_row(client, db):
    rid = _set_up(client, db)["id"]
    done = client.post(API, json={"csv": "Dish,Category,Price\nSoup,Starter,6.50\nBread,Starter,free\nSoup,Starter,7\n"}).json()
    assert done["saved"] is False and done["errors"] == 2 and db.query(MenuItem).filter_by(restaurant_id=rid).count() == 0
    problems = {r["line"]: r["messages"][0] for r in done["rows"] if r["status"] == "error"}
    assert "isn't a number" in problems[3] and "also on line 2" in problems[4]


def test_an_unclear_file_enters_nothing_until_someone_says_what_it_is(client, db):
    rid = _set_up(client, db)["id"]
    unclear = client.post(API, json={"csv": "Name,Price\nSoup,6.50\n"}).json()
    assert unclear["needs_kind"] is True and unclear["saved"] is False and unclear["confidence"] < 0.8 and db.query(MenuItem).filter_by(restaurant_id=rid).count() == 0
    assert {"dishes", "staff", "invoices", "inventory", "orders", "punches"} <= {c["kind"] for c in unclear["choices"]}
    named = client.post(API, json={"csv": "Name,Price\nSoup,6.50\n", "kind": "dishes"}).json()
    assert named["saved"] is True and db.query(MenuItem).filter_by(restaurant_id=rid, name="Soup").one().category == "Uncategorized"
    nothing = client.post(API, json={"csv": "foo,bar\n1,2\n"}).json()
    assert nothing["needs_kind"] is True and nothing["kind"] is None


def test_existing_rows_are_skipped_unless_asked_to_update(client, db):
    rid = _set_up(client, db)["id"]
    client.post(API, json={"csv": DISHES})
    dearer = "Dish,Category,Price\nMargherita Pizza,Entree,15.50\n"
    kept = client.post(API, json={"csv": dearer}).json()
    assert kept["skipped"] == 1 and db.query(MenuItem).filter_by(restaurant_id=rid, name="Margherita Pizza").one().price == 14.0
    changed = client.post(API, json={"csv": dearer, "update_existing": True}).json()
    db.expire_all()
    assert changed["updated"] == 1 and db.query(MenuItem).filter_by(restaurant_id=rid, name="Margherita Pizza").one().price == 15.5
    assert db.query(MenuItem).filter_by(restaurant_id=rid).count() == 2


def test_a_dry_run_reports_and_saves_nothing(client, db):
    rid = _set_up(client, db)["id"]
    seen = client.post(API, json={"csv": SUPPLIERS, "dry_run": True}).json()
    assert seen["saved"] is False and seen["new"] == 2 and db.query(Supplier).filter_by(restaurant_id=rid).count() == 0
    assert client.get(f"{API}/staff/template").text.startswith("Name,Role,Email")


def test_hourly_rates_from_a_file_stay_out_of_the_history_and_the_reply(client, db):
    _set_up(client, db)
    done = client.post(API, json={"csv": STAFF})
    assert "22.5" not in done.text and "15.00" not in done.text and "hourly" not in done.text.lower()
    history = json.dumps([[c.before, c.after] for c in db.query(ChangeLog).all()], default=str)
    assert "hourly_rate" not in history and "22.5000" not in history and "22.50" not in history
    assert db.query(ChangeLog).filter(ChangeLog.entity_type == "staff").count() == 2  # the people themselves are in the history


def test_a_restaurants_file_only_ever_writes_to_that_restaurant(client, db):
    rid = _set_up(client, db)["id"]
    other = Supplier(restaurant_id=None, name="Somewhere Else Foods")
    db.add(other)
    db.commit()
    client.post(API, json={"csv": SUPPLIERS})
    assert {s.restaurant_id for s in db.query(Supplier).filter(Supplier.name.in_(["Green Valley Farms", "Harbor Dairy"]))} == {rid}
    assert client.post(API, json={"csv": SUPPLIERS, "kind": "payroll"}).status_code == 404


def test_a_role_the_restaurant_has_not_listed_is_a_problem_row_that_says_what_to_do(client, db):
    rid = _set_up(client, db)["id"]
    done = client.post(API, json={"csv": "Name,Role\nSam Patel,Astronaut\n", "kind": "staff"}).json()
    assert done["saved"] is False and "isn't one of this restaurant's roles" in done["rows"][0]["messages"][0] and db.query(Staff).filter_by(restaurant_id=rid).count() == 0
