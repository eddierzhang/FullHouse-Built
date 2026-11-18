"""Phase B: documents, agreed prices, recipes and menu margin.

A price list becomes agreed-price proposals; an invoice billed above the agreed price opens an
"Owed to you" card citing both; the top sellers get recipe proposals with lines from the invoices;
a confirmed recipe gets a plate cost citing price points; a cost rise opens a menu-margin card and
a reprice suggestion that writes to no POS. The agent cannot write recipes directly.
"""

import base64
import importlib.util
import pathlib
from datetime import date, timedelta

import pytest

from app.agents.models import AgentAction
from app.config import settings
from app.documents.models import Document
from app.platform import flags, jobs
from app.prices import engine
from app.prices.models import AgreedPrice, Invoice, PriceItem
from app.recipes import engine as recipes
from app.recipes.models import PlateCost, Recipe
from app.restaurant.models import Supplier
from app.sales.models import SalesItem
from test_restaurant_setup import _set_up

API = "/api/v1/documents"
TODAY = date.today()
_spec = importlib.util.spec_from_file_location("fixture_pdf", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "make_fixture_invoices.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
pdf_bytes = _mod.pdf_bytes


def _on(client, db, tmp_path, monkeypatch) -> dict:
    restaurant = _set_up(client, db)
    flags.set_flag(db, restaurant["id"], flags.V2_DOCUMENTS, True)
    flags.set_flag(db, restaurant["id"], flags.V2_CONNECT, True)
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    db.commit()
    return restaurant


def _upload(client, name: str, data: bytes, media="application/pdf", doc_type=None) -> dict:
    made = client.post(API, json={"filename": name, "media_type": media, "content": base64.b64encode(data).decode(), "doc_type": doc_type})
    assert made.status_code == 201, made.text
    return made.json()


def _invoice(client, supplier: str, day: date, lines: list[tuple]) -> dict:
    saved = client.post("/api/v1/prices/invoices", json={
        "supplier_name": supplier, "invoice_date": day.isoformat(), "source": "typed",
        "lines": [{"description": d, "qty": q, "pack_text": p, "unit_price": u} for d, q, p, u in lines],
    })
    assert saved.status_code == 201, saved.text
    return saved.json()


def _answer_all(client):
    for q in client.get("/api/v1/prices/questions").json():
        if q["status"] == "open":
            client.post(f"/api/v1/prices/questions/{q['id']}/answer", json={"answer": "yes"})


def _pending(client, kind=None):
    rows = client.get("/api/v1/agents/actions?status=pending").json()
    return [a for a in rows if kind is None or a["action_type"] == kind]


PRICE_LIST = [
    "Worldwide Produce", "Price list effective 2026-10-01", "",
    "KALE GREEN 24 CT    CS   31.20",
    "BROCCOLI CROWNS CS 12   CS   28.50",
    "OLIVE OIL EV 4/1 GAL   CS   68.00",
]


def test_documents_are_hidden_until_the_flag_is_on(client, db):
    _set_up(client, db)
    assert client.get(API).status_code == 404


def test_acceptance_price_list_to_agreed_price_to_credit_owed(client, db, tmp_path, monkeypatch):
    """Upload a price list; an agreed_price proposal exists; approve as owner; an invoice billing kale above it;
    a credit_owed card with the right dollar impact citing the invoice line and the price-list document."""
    restaurant = _on(client, db, tmp_path, monkeypatch)
    # The restaurant already buys kale from Worldwide: one invoice, matched, so the price list has an item to land on.
    first = _invoice(client, "Worldwide Produce", TODAY - timedelta(days=10), [("KALE GREEN 24 CT", 10, "CS", 31.20)])
    client.post(f"/api/v1/prices/questions/{first['first_question']['id']}/answer", json={"answer": "yes"})
    kale = db.query(PriceItem).one()
    assert kale.pack_size == 24 and kale.unit == "ct"

    doc = _upload(client, "worldwide-price-list.pdf", pdf_bytes(PRICE_LIST))
    assert doc["status"] == "received"
    assert jobs.drain(db) >= 1
    db.expire_all()
    doc = client.get(f"{API}/{doc['id']}").json()
    assert (doc["doc_type"], doc["type_confidence"] >= 0.9, doc["extracted_summary"]["supplier"]) == ("price_list", True, "Worldwide Produce")
    assert doc["extracted_summary"]["proposed"] == 1 and "BROCCOLI CROWNS" in doc["extracted_summary"]["unmatched"][0]
    (proposal,) = _pending(client, "agreed_price")
    assert proposal["payload"]["item_name"] == kale.name and proposal["payload"]["unit_price"] == 1.3
    assert proposal["payload"]["effective_from"] == "2026-10-01" and proposal["payload"]["source_document_id"] == doc["id"]

    approved = client.post(f"/api/v1/agents/actions/{proposal['id']}/approve")
    assert approved.status_code == 200, approved.text
    agreed = db.query(AgreedPrice).one()
    assert (agreed.unit_price, agreed.base_unit, agreed.effective_from.isoformat(), agreed.source_document_id) == (1.3, "ct", "2026-10-01", doc["id"])
    detail = client.get(f"/api/v1/prices/items/{kale.id}").json()
    assert detail["agreed"]["unit_price"] == 1.3 and detail["agreed"]["supplier"]["name"] == "Worldwide Produce"

    # Billed above the agreed price: $1.45 a bunch against $1.30, on 8 cases of 24.
    over = _invoice(client, "Worldwide Produce", TODAY - timedelta(days=1), [("KALE GREEN 24 CT", 8, "CS", 34.80)])
    assert over["matched"] == 1
    digest = client.get("/api/v1/prices/digest").json()
    owed = next(s for s in digest["sections"] if s["key"] == "owed")
    (card,) = owed["cards"]
    assert card["item_id"] == kale.id and card["impact"] == round((34.80 / 24 - 1.3) * 8 * 24, 2) == 28.8
    kinds = {(s["kind"], s["id"]) for s in card["sources"]}
    assert ("invoice", over["id"]) in kinds and ("document", doc["id"]) in kinds
    assert "against the agreed $31.20" in card["sentence"]
    # Within tolerance is not an overcharge, and a cheaper line never is.
    _invoice(client, "Worldwide Produce", TODAY, [("KALE GREEN 24 CT", 2, "CS", 31.40)])
    digest = client.get("/api/v1/prices/digest").json()
    assert next(s for s in digest["sections"] if s["key"] == "owed")["cards"][0]["impact"] == 28.8


def test_the_owner_sets_an_agreed_price_from_the_item_page_as_a_proposal(client, db, tmp_path, monkeypatch):
    _on(client, db, tmp_path, monkeypatch)
    first = _invoice(client, "Chef's Warehouse", TODAY - timedelta(days=3), [("KALE, GREEN 24CT", 4, "CS", 27.60)])
    client.post(f"/api/v1/prices/questions/{first['first_question']['id']}/answer", json={"answer": "yes"})
    kale = db.query(PriceItem).one()
    supplier = db.query(Supplier).one()
    made = client.post(f"/api/v1/prices/items/{kale.id}/agreed-price", json={"supplier_id": supplier.id, "per_pack": 26.40, "cited": "Quote by phone"})
    assert made.status_code == 200 and made.json() == {"proposed": True}
    (proposal,) = _pending(client, "agreed_price")
    assert proposal["payload"]["unit_price"] == 1.1 and "Quote by phone" in proposal["payload"]["note"]
    # The same again is not proposed twice; eleven different ones hit the weekly cap of ten.
    assert client.post(f"/api/v1/prices/items/{kale.id}/agreed-price", json={"supplier_id": supplier.id, "per_pack": 26.40}).json() == {"proposed": False}
    for n in range(12):
        client.post(f"/api/v1/prices/items/{kale.id}/agreed-price", json={"supplier_id": supplier.id, "per_pack": 20 + n})
    assert len(_pending(client, "agreed_price")) == 10


def test_acceptance_recipe_onboarding_plate_cost_menu_margin_and_reprice(client, db, tmp_path, monkeypatch):
    """Square CSV with "Kale Caesar"; onboarding proposes a recipe with kale and olive oil lines; approve; plate cost
    cites price points; kale up 10%; a menu_margin finding; a reprice proposal with before/after and no POS write."""
    restaurant = _on(client, db, tmp_path, monkeypatch)
    rid = restaurant["id"]
    day = TODAY - timedelta(days=12)
    # What the kitchen buys, matched: kale and olive oil (the spec's Kale Caesar), nothing else yet.
    saved = _invoice(client, "Worldwide Produce", day, [("KALE GREEN 24 CT", 10, "CS", 31.20), ("OLIVE OIL EV 4/1 GAL", 2, "CS", 68.00)])
    _answer_all(client)
    # A second week so there is history, same prices.
    _invoice(client, "Worldwide Produce", day + timedelta(days=7), [("KALE GREEN 24 CT", 10, "CS", 31.20), ("OLIVE OIL EV 4/1 GAL", 2, "CS", 68.00)])
    _answer_all(client)
    items = {i.name: i for i in db.query(PriceItem)}
    assert any("kale" in n.lower() for n in items) and any("olive oil" in n.lower() for n in items)

    # Four weeks of Square sales: Kale Caesar sells 20 a week at $14.
    rows = ["Date,Time,Category,Item,Qty,Price Point Name,SKU,Gross Sales,Discounts,Net Sales,Tax"]
    for back in range(1, 29):
        d = TODAY - timedelta(days=back)
        rows.append(f"{d.strftime('%m/%d/%Y')},12:00:00,Food,Kale Caesar,3,Regular,KC,42.00,0.00,42.00,3.36")
        rows.append(f"{d.strftime('%m/%d/%Y')},12:00:00,Food,Tiramisu,2,Regular,TI,16.00,0.00,16.00,1.28")
    assert client.post("/api/v1/connect/csv/import", json={"text": "\n".join(rows) + "\n"}).status_code == 200
    jobs.drain(db)

    made = client.post("/api/v1/recipes/onboarding/run").json()
    assert made["proposed"] == 2
    proposal = next(a for a in _pending(client, "recipe") if a["payload"]["name"] == "Kale Caesar")
    lines = proposal["payload"]["lines"]
    matched = {l["item_name"].lower().split(",")[0] for l in lines if l["matched"]}
    assert any("kale" in m for m in matched) and any("olive oil" in m for m in matched), matched
    assert proposal["payload"]["pos_item_id"] == "KC" and "Is this right for Kale Caesar?" in proposal["payload"]["question"]
    assert "lemon" in proposal["payload"]["unknown"]  # not on the invoices: asked, not guessed
    # The same onboarding again proposes nothing new.
    assert client.post("/api/v1/recipes/onboarding/run").json() == {"proposed": 0}

    approved = client.post(f"/api/v1/agents/actions/{proposal['id']}/approve")
    assert approved.status_code == 200, approved.text
    recipe = db.query(Recipe).filter_by(name="Kale Caesar").one()
    assert recipe.status == "confirmed" and recipe.pos_item_id == "KC" and len(recipe.lines) >= 2
    assert all(l.item_id for l in recipe.lines)  # unknown ingredients were left out, not guessed

    costed = client.post(f"/api/v1/recipes/{recipe.id}/cost").json()
    assert costed["plate_cost"] and costed["plate_cost"] > 0
    cited = {b["line_id"] for b in costed["basis"] if b.get("line_id")}
    assert cited and cited <= {l.id for l in db.query(engine.InvoiceLine)}
    kale_line = next(b for b in costed["basis"] if "kale" in b["item_name"].lower())
    assert kale_line["per_unit"] == 1.3 and kale_line["cost"] == round(kale_line["qty"] * 1.3, 4)
    # 27 of the 28 days fall inside the four-week window: 81 plates over 4 weeks.
    assert costed["sold_a_week"] == 20.25 and costed["price_a_plate"] == 14.0
    weekly = costed["sold_a_week"]
    item_detail = client.get(f"/api/v1/prices/items/{kale_line['item_id']}").json()
    assert [r["name"] for r in item_detail["used_in"]] == ["Kale Caesar"]

    # Kale up 10%: the plate cost rises, the digest opens a menu-margin card, and a reprice is suggested.
    before_cost = costed["plate_cost"]
    _invoice(client, "Worldwide Produce", TODAY, [("KALE GREEN 24 CT", 10, "CS", 34.32)])
    week_before = engine.week_start(TODAY) - timedelta(days=7)
    db.query(PlateCost).filter_by(recipe_id=recipe.id).update({"week_of": week_before})
    db.commit()
    recipes.compute_plate_costs(db, rid)
    db.commit()
    after = db.query(PlateCost).filter_by(recipe_id=recipe.id, week_of=engine.week_start(TODAY)).one()
    assert after.cost > before_cost
    digest = client.post("/api/v1/prices/digest/rebuild").json()
    margin = next(s for s in digest["sections"] if s["key"] == "margin")
    (card,) = margin["cards"]
    assert card["recipe_id"] == recipe.id and card["action"]["kind"] == "reprice" and card["impact"] == round((after.cost - before_cost) * weekly, 2)
    assert "Plate cost" in card["sentence"] and "up" in card["sentence"]

    from app.restaurant.models import MenuItem

    menu_prices_before = {m.name: m.price for m in db.query(MenuItem)}
    suggested = client.post(f"/api/v1/recipes/{recipe.id}/reprice").json()
    (reprice,) = _pending(client, "menu_reprice")
    assert reprice["id"] == suggested["proposal_id"]
    p = reprice["payload"]
    assert p["plate_cost_before"] == round(before_cost, 4) and p["plate_cost_after"] == round(after.cost, 4) and p["current_price"] == 14.0
    assert p["suggested_delta"] > 0 and set(p["cited_line_ids"]) <= {l.id for l in db.query(engine.InvoiceLine)}
    assert client.post(f"/api/v1/agents/actions/{reprice['id']}/approve").json()["applied_result"].startswith("Noted:")
    menu_prices_after = {m.name: m.price for m in db.query(MenuItem)}
    assert menu_prices_after == menu_prices_before  # no POS or menu write
    assert db.query(SalesItem).filter_by(pos_item_id="KC").count() == 28


def test_a_recipe_card_pdf_becomes_a_recipe_proposal_and_the_agent_cannot_write_recipes_directly(client, db, tmp_path, monkeypatch):
    restaurant = _on(client, db, tmp_path, monkeypatch)
    _invoice(client, "Worldwide Produce", TODAY - timedelta(days=2), [("KALE GREEN 24 CT", 10, "CS", 31.20), ("OLIVE OIL EV 4/1 GAL", 2, "CS", 68.00)])
    _answer_all(client)
    card = pdf_bytes(["Kale Caesar", "Recipe", "Yield 4 plates", "Ingredients", "1 lb kale", "2 tbsp olive oil", "1 each lemon"])
    doc = _upload(client, "kale-caesar.pdf", card)
    jobs.drain(db)
    db.expire_all()
    doc = client.get(f"{API}/{doc['id']}").json()
    assert doc["doc_type"] == "recipe" and doc["status"] == "complete" and doc["result_table"] == "agent_actions"
    (proposal,) = _pending(client, "recipe")
    payload = proposal["payload"]
    assert payload["name"] == "Kale Caesar" and payload["yield_qty"] == 4 and payload["source"] == "document"
    assert [(l["ingredient_text"], l["matched"]) for l in payload["lines"]] == [("kale", True), ("olive oil", True), ("lemon", False)]
    assert payload["unknown"] == ["lemon"]

    # Through a tool, the agent proposes; it cannot write the recipe itself.
    from app.agents.guard import ForbiddenAgentWrite, tool_scope

    with tool_scope("propose_recipe"):
        db.add(Recipe(restaurant_id=restaurant["id"], name="Sneaky", status="confirmed"))
        with pytest.raises(ForbiddenAgentWrite):
            db.flush()
    db.rollback()


def test_an_unsure_classification_waits_for_the_reviewer_who_picks_the_type(client, db, tmp_path, monkeypatch):
    _on(client, db, tmp_path, monkeypatch)
    doc = _upload(client, "mystery.pdf", pdf_bytes(["Thanks for your business", "See you next week"]))
    jobs.drain(db)
    db.expire_all()
    doc = client.get(f"{API}/{doc['id']}").json()
    assert doc["status"] == "needs_review" and doc["doc_type"] == "other" and doc["type_confidence"] < 0.9
    picked = client.post(f"{API}/{doc['id']}/type", json={"doc_type": "recipe"})
    assert picked.status_code == 200 and picked.json()["doc_type"] == "recipe"
    jobs.drain(db)
    db.expire_all()
    doc = client.get(f"{API}/{doc['id']}").json()
    assert doc["status"] == "needs_review" and "couldn't be read" in doc["note"].lower()
    assert client.post(f"{API}/{doc['id']}/type", json={"doc_type": "poem"}).status_code == 422
    rows = client.get(API).json()
    assert [r["id"] for r in rows] == [doc["id"]] and rows[0]["file_url"].startswith("/api/v1/platform/files/")


def test_a_permit_is_read_with_its_dates_and_kept_for_the_calendar(client, db, tmp_path, monkeypatch):
    _on(client, db, tmp_path, monkeypatch)
    permit = pdf_bytes(["City of Austin Health Department", "Food Service Permit", "Issued: 2026-01-15", "Expires: 2026-11-16", "Permit No 44-1021"])
    doc = _upload(client, "permit.pdf", permit)
    jobs.drain(db)
    db.expire_all()
    doc = client.get(f"{API}/{doc['id']}").json()
    assert doc["doc_type"] == "permit" and doc["status"] in ("extracted", "complete")
    assert doc["extracted_summary"]["expires_on"] == "2026-11-16" and doc["extracted_summary"]["issued_on"] == "2026-01-15"


def test_recipe_edits_are_proposals_and_lines_are_one_item_or_one_sub_recipe(client, db, tmp_path, monkeypatch):
    restaurant = _on(client, db, tmp_path, monkeypatch)
    _invoice(client, "Worldwide Produce", TODAY - timedelta(days=2), [("KALE GREEN 24 CT", 10, "CS", 31.20)])
    _answer_all(client)
    kale = db.query(PriceItem).one()
    recipe = recipes.write_recipe(db, restaurant["id"], {"name": "Kale side", "lines": [{"item_id": kale.id, "qty": 0.5, "unit": "lb"}]})
    db.commit()
    bad = client.post(f"/api/v1/recipes/{recipe.id}/edit", json={"lines": [{"item_id": kale.id, "sub_recipe_id": recipe.id, "qty": 1, "unit": "lb"}]})
    assert bad.status_code == 422
    edited = client.post(f"/api/v1/recipes/{recipe.id}/edit", json={"lines": [{"item_id": kale.id, "qty": 0.75, "unit": "lb"}]})
    assert edited.status_code == 200
    db.expire_all()
    assert db.get(Recipe, recipe.id).lines[0].qty == 0.5  # unchanged until approved
    assert client.post(f"/api/v1/agents/actions/{edited.json()['proposal_id']}/approve").status_code == 200
    db.expire_all()
    assert db.get(Recipe, recipe.id).lines[0].qty == 0.75
    page = client.get(f"/api/v1/recipes/{recipe.id}").json()
    assert page["lines"][0]["item_name"] == kale.name and page["status"] == "confirmed"
