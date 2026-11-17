"""Phase 6: voice. What was said becomes a preview; only a confirm writes (I15); the budget holds (I16)."""

import itertools
from datetime import date

import pytest

from app.notify import service as notify
from app.platform import flags
from app.restaurant import orders
from app.restaurant.models import InventoryAdjustment, InventoryItem, Supplier
from app.stock.models import StockCount, StorageArea
from app.voice import parser
from app.voice.models import VoiceCommand
from test_restaurant_details import _second_restaurant
from test_restaurant_setup import _set_up

API = "/api/v1/voice"
ITEMS = [
    {"id": "moz", "name": "Mozzarella", "unit": "lb", "pack_size": 6, "pack_unit": "case"},
    {"id": "bas", "name": "Basil", "unit": "bunch", "pack_size": None, "pack_unit": None},
    {"id": "chi", "name": "Chicken Breast", "unit": "lb", "pack_size": 40, "pack_unit": "case"},
    {"id": "rom", "name": "Romaine Lettuce", "unit": "head", "pack_size": 24, "pack_unit": "case"},
    {"id": "tom", "name": "Tomatoes", "unit": "lb", "pack_size": 25, "pack_unit": "case"},
    {"id": "flo", "name": "Flour", "unit": "lb", "pack_size": 50, "pack_unit": "bag"},
    {"id": "oil", "name": "Olive Oil", "unit": "L", "pack_size": None, "pack_unit": None},
    {"id": "par", "name": "Parmesan", "unit": "lb", "pack_size": None, "pack_unit": None},
    {"id": "gar", "name": "Garlic", "unit": "lb", "pack_size": None, "pack_unit": None},
    {"id": "esp", "name": "Espresso Beans", "unit": "lb", "pack_size": None, "pack_unit": None},
    {"id": "cia", "name": "Ciabatta", "unit": "loaf", "pack_size": None, "pack_unit": None},
    {"id": "mas", "name": "Mascarpone", "unit": "lb", "pack_size": None, "pack_unit": None},
]
AREAS = ["Walk-in", "Dry store"]
SUPPLIERS = ["Sysco Foods", "Green Valley Farms", "Harbor Dairy Co."]
#: How each item is said, with the unit word people use for it.
SPOKEN = {"moz": ("mozzarella", "pounds"), "bas": ("basil", "bunches"), "chi": ("chicken", "pounds"), "rom": ("romaine", "heads"), "tom": ("tomatoes", "pounds"),
          "flo": ("flour", "pounds"), "oil": ("olive oil", "liters"), "par": ("parmesan", "pounds"), "gar": ("garlic", "pounds"),
          "esp": ("espresso beans", "pounds"), "cia": ("ciabatta", "loaves"), "mas": ("mascarpone", "pounds")}
#: The same amounts said several ways.
NUMBERS = [("sixteen", 16), ("16", 16), ("three", 3), ("twenty five", 25), ("two and a half", 2.5), ("a dozen", 12), ("forty", 40), ("7.5", 7.5),
           ("one hundred and ten", 110), ("half a", 0.5), ("three quarters of a", 0.75), ("one point five", 1.5), ("a couple", 2), ("nineteen", 19)]


def _phrases():
    """At least 200 distinct things a person would say, each with the command it should become."""
    out = []
    ids = list(SPOKEN)
    for (item_id, (word, unit)), (number, value) in zip(itertools.cycle(SPOKEN.items()), NUMBERS * 6):
        singular = number in ("half a", "three quarters of a")
        said_unit = unit[:-1] if singular and unit.endswith("s") and unit != "bunches" and unit != "loaves" else ("bunch" if singular and unit == "bunches" else "loaf" if singular and unit == "loaves" else unit)
        # item first, the amount after it; and the amount first
        if not singular:
            out.append((f"{word} {number} {said_unit}", "count", [(item_id, value)], {}))
        out.append((f"{number} {said_unit} of {word}", "count", [(item_id, value)], {}))
    for index in range(0, len(ids) - 1, 2):  # two items in one breath, no commas, as speech arrives
        (a, (wa, ua)), (b, (wb, ub)) = (ids[index], SPOKEN[ids[index]]), (ids[index + 1], SPOKEN[ids[index + 1]])
        out.append((f"{wa} twelve {ua} {wb} four {ub}", "count", [(a, 12), (b, 4)], {}))
        out.append((f"walk in {wa} nine {ua} and {wb} thirty one {ub}", "count", [(a, 9), (b, 31)], {"area": "Walk-in"}))
        out.append((f"in the dry store eight {ua} of {wa} two {ub} of {wb}", "count", [(a, 8), (b, 2)], {"area": "Dry store"}))
        out.append((f"{wa} 14, {wb} 6", "count", [(a, 14), (b, 6)], {}))
    for item_id, (word, unit) in SPOKEN.items():
        out.append((f"waste two {unit} of {word} dropped", "waste", [(item_id, 2)], {"reason": "dropped"}))
        out.append((f"threw out one {unit[:-1] if unit.endswith('s') and unit not in ('bunches', 'loaves') else unit} of {word} it went bad", "waste", [(item_id, 1)], {"reason": "spoiled"}))
        out.append((f"staff meal three {unit} of {word}", "waste", [(item_id, 3)], {"reason": "staff_meal"}))
        out.append((f"we have about eleven {unit} of {word} left", "count", [(item_id, 11)], {}))
    for name in SUPPLIERS:
        first = name.split()[0].lower()
        out.append((f"{first} delivery everything arrived", "receive", [], {"supplier": name, "all_arrived": True}))
        out.append((f"{name.lower()} delivery came in all here", "receive", [], {"supplier": name, "all_arrived": True}))
        out.append((f"{first} delivery everything arrived except one case of romaine", "receive", [("rom", 24)], {"supplier": name, "all_arrived": True}))
        out.append((f"{first} delivered short two bags of flour", "receive", [("flo", 100)], {"supplier": name, "all_arrived": True}))
    for said, unit_said, expected in [("mozzarella two cases", None, [("moz", 12)]), ("chicken one case", None, [("chi", 40)]), ("flour three bags", None, [("flo", 150)]),
                                      ("mozzarella eight ounces", None, [("moz", 0.5)]), ("parmesan two kilos", None, [("par", 4.4092)]), ("olive oil two gallons", None, [("oil", 7.5708)])]:
        out.append((said, "count", expected, {}))
    for question in ["what did I pay for tomatoes last month", "how much did harbor dairy short me this quarter", "which supplier is cheapest on paper goods",
                     "is chicken going up", "talking points for tomorrow's meeting?", "who do I buy flour from"]:
        out.append((question, "ask", [], {}))
    for said, route in [("show me what's low", "inventory"), ("go to the count sheet", "count"), ("open deliveries", "receive"), ("take me to the digest", "digest"),
                        ("show me savings", "insights"), ("open the schedule", "staff"), ("pull up the menu", "menu"), ("show me waste", "variance")]:
        out.append((said, "navigate", [], {"route": route}))
    return out


PHRASES = _phrases()


def _right(said, intent, lines, extra) -> bool:
    command = parser.parse(said, ITEMS, {}, AREAS, SUPPLIERS)
    got = [(l.inventory_item_id, round(l.quantity, 4)) for l in command.lines]
    return (command.intent == intent and got == [(i, round(q, 4)) for i, q in lines] and not command.unmatched
            and all(getattr(command, key) == value for key, value in extra.items()))


def test_acceptance_two_hundred_spoken_lines_parse_at_ninety_five_percent_or_better():
    assert len(PHRASES) >= 200 and len({p[0] for p in PHRASES}) == len(PHRASES)
    wrong = [p[0] for p in PHRASES if not _right(*p)]
    assert len(wrong) / len(PHRASES) <= 0.05, f"{len(wrong)} of {len(PHRASES)} wrong: {wrong[:12]}"


@pytest.mark.parametrize("said, value", [("sixteen", 16), ("twenty five", 25), ("a hundred and ten", 110), ("two and a half", 2.5), ("half", 0.5),
                                         ("three quarters", 0.75), ("one point two five", 1.25), ("1/2", 0.5), ("2 dozen", 24), ("a couple", 2), ("no", 0)])
def test_numbers_are_read_however_they_are_said(said, value):
    tokens = parser._tokens(said)
    assert parser.read_number(tokens, 0) == (pytest.approx(value), len(tokens))


def test_an_unclear_item_is_asked_about_not_guessed_and_an_alias_settles_it():
    unclear = parser.parse("kale four cases", ITEMS, {}, AREAS, SUPPLIERS)
    assert unclear.lines == [] and [(u["said"], u["quantity"]) for u in unclear.unmatched] == [("kale", 4)]
    learned = parser.parse("mozz twelve", ITEMS, {"mozz": "moz"}, AREAS, SUPPLIERS)
    assert [(l.inventory_item_id, l.quantity) for l in learned.lines] == [("moz", 12)]
    assert parser.parse("um hello there", ITEMS, {}, AREAS, SUPPLIERS).intent == "unknown"


# --- the routes ---------------------------------------------------------------------------------


def _kitchen(client, db):
    restaurant = _set_up(client, db)
    rid = restaurant["id"]
    for flag in (flags.V3_VOICE, flags.V3_STOCK, flags.V3_RECEIVING):
        flags.set_flag(db, rid, flag, True)
    sysco = Supplier(restaurant_id=rid, name="Sysco Foods", lead_time_days=2)
    db.add_all([sysco, StorageArea(restaurant_id=rid, name="Walk-in")])
    db.flush()
    items = {}
    for row in ITEMS[:6]:
        item = InventoryItem(restaurant_id=rid, name=row["name"], unit=row["unit"], quantity_on_hand=50, unit_cost=2.0, pack_size=row["pack_size"], pack_unit=row["pack_unit"])
        db.add(item)
        db.flush()
        items[row["name"]] = item
    db.commit()
    return rid, items, sysco


def test_a_parse_writes_only_its_preview_and_a_confirm_writes_once(client, db):
    rid, items, _ = _kitchen(client, db)

    preview = client.post(f"{API}/parse", json={"transcript": "walk in mozzarella sixteen pounds basil three bunches"}).json()

    assert (preview["intent"], preview["area"], preview["status"]) == ("count", "Walk-in", "previewed")
    assert preview["speak"] == "Count in Walk-in: 16 lb of Mozzarella, 3 bunch of Basil."
    # I15: nothing but the preview exists yet.
    db.expire_all()
    assert items["Mozzarella"].quantity_on_hand == 50 and db.query(StockCount).count() == 0
    assert db.query(InventoryAdjustment).filter(InventoryAdjustment.reason == "count").count() == 0

    done = client.post(f"{API}/commands/{preview['id']}/confirm").json()
    again = client.post(f"{API}/commands/{preview['id']}/confirm").json()

    db.expire_all()
    assert done["done"] == again["done"] == "Counted 2 items."
    assert (items["Mozzarella"].quantity_on_hand, items["Basil"].quantity_on_hand) == (16, 3)
    assert db.query(StockCount).count() == 1 and db.query(InventoryAdjustment).filter(InventoryAdjustment.reason == "count").count() == 2


def test_waste_by_voice_takes_the_corrected_amount_and_a_discarded_preview_cannot_be_confirmed(client, db):
    rid, items, _ = _kitchen(client, db)

    preview = client.post(f"{API}/parse", json={"transcript": "waste two pounds of chicken, dropped"}).json()
    assert (preview["intent"], preview["reason"]) == ("waste", "dropped")
    chicken = items["Chicken Breast"]
    done = client.post(f"{API}/commands/{preview['id']}/confirm", json={"lines": [{"inventory_item_id": chicken.id, "quantity": 3}]}).json()
    db.expire_all()
    assert done["done"] == "Logged 1 waste entry, about $6.00." and chicken.quantity_on_hand == 47
    waste = db.query(InventoryAdjustment).filter_by(reason="waste").one()
    assert waste.note == "Dropped or spilled: by voice" and waste.source_key == f"voice:{preview['id']}:{chicken.id}"

    dropped = client.post(f"{API}/parse", json={"transcript": "waste ten pounds of flour spoiled"}).json()
    assert client.post(f"{API}/commands/{dropped['id']}/discard").json()["status"] == "discarded"
    assert client.post(f"{API}/commands/{dropped['id']}/confirm").status_code == 409
    db.expire_all()
    assert items["Flour"].quantity_on_hand == 50


def test_a_delivery_by_voice_checks_the_order_in_with_what_was_short(client, db):
    rid, items, sysco = _kitchen(client, db)
    order = orders.draft_with_lines(db, rid, sysco, [
        {"inventory_item_id": items["Romaine Lettuce"].id, "description": "Romaine, 24 ct case", "qty": 3, "unit": "case", "units": 72, "unit_price_expected": 26.0},
        {"inventory_item_id": items["Flour"].id, "description": "Flour, 50 lb bag", "qty": 2, "unit": "bag", "units": 100, "unit_price_expected": 18.9}], today=date.today())
    orders.mark_sent(db, order, sent_by="owner@example.com")
    db.commit()

    preview = client.post(f"{API}/parse", json={"transcript": "Sysco delivery: everything arrived except one case of romaine."}).json()
    assert (preview["intent"], preview["supplier"], preview["order_id"], preview["blocked"]) == ("receive", "Sysco Foods", order.id, None)
    assert preview["speak"] == "Sysco Foods delivery: everything arrived except 24 head of Romaine Lettuce."
    done = client.post(f"{API}/commands/{preview['id']}/confirm").json()

    db.expire_all()
    assert "1 line short" in done["done"] and order.status == "received"
    assert (items["Romaine Lettuce"].quantity_on_hand, items["Flour"].quantity_on_hand) == (50 + 48, 50 + 100)
    # With nothing on its way, a delivery has nowhere to go, and says so before anything is tried.
    nothing = client.post(f"{API}/parse", json={"transcript": "sysco delivery everything arrived"}).json()
    assert "no order from Sysco Foods" in nothing["blocked"] and client.post(f"{API}/commands/{nothing['id']}/confirm").status_code == 409


def test_a_question_or_a_page_is_previewed_and_confirming_writes_nothing(client, db):
    rid, items, _ = _kitchen(client, db)
    asked = client.post(f"{API}/parse", json={"transcript": "What did I pay for tomatoes last month?"}).json()
    go = client.post(f"{API}/parse", json={"transcript": "show me what's low"}).json()
    assert (asked["intent"], asked["question"]) == ("ask", "What did I pay for tomatoes last month?")
    assert (go["intent"], go["route"], go["route_label"]) == ("navigate", "inventory", "Inventory")
    before = db.query(InventoryAdjustment).count()
    assert client.post(f"{API}/commands/{asked['id']}/confirm").json()["done"] == "Okay."
    assert db.query(InventoryAdjustment).count() == before


def test_an_alias_is_learned_per_restaurant_and_the_budget_blocks_transcription(client, db, monkeypatch):
    rid, items, _ = _kitchen(client, db)
    unclear = client.post(f"{API}/parse", json={"transcript": "mozz twelve"}).json()
    assert unclear["lines"] == [] or unclear["lines"][0]["name"] == "Mozzarella"  # "mozz" may already be close enough
    client.post(f"{API}/aliases", json={"phrase": "The Fresh Stuff", "inventory_item_id": items["Mozzarella"].id})
    learned = client.post(f"{API}/parse", json={"transcript": "the fresh stuff nine pounds"}).json()
    assert [(l["name"], l["quantity"]) for l in learned["lines"]] == [("Mozzarella", 9)]

    # I16: with the month's budget spent, transcription says so; with room, it says no service is connected.
    assert client.post(f"{API}/transcribe", json={"audio": "AAAA", "seconds": 4}).status_code == 503
    monkeypatch.setitem(notify.MONTHLY_BUDGETS, "voice_seconds", 0.0)
    assert client.post(f"{API}/transcribe", json={"audio": "AAAA", "seconds": 4}).status_code == 402

    other, b = _second_restaurant(db)
    assert b.post(f"{API}/parse", json={"transcript": "mozzarella sixteen"}).status_code == 404  # the flag is off there
    flags.set_flag(db, other.id, flags.V3_VOICE, True)
    flags.set_flag(db, other.id, flags.V3_STOCK, True)
    db.commit()
    theirs = b.post(f"{API}/parse", json={"transcript": "the fresh stuff nine pounds"}).json()
    assert theirs["lines"] == []  # another restaurant's items and aliases are not theirs to speak
    assert b.post(f"{API}/commands/{learned['id']}/confirm").status_code == 404
    assert db.query(VoiceCommand).filter_by(restaurant_id=other.id).count() == 1
