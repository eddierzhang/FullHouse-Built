"""Price intelligence: invoices are read as printed, the agent asks rather than guesses,
the digest says what changed and what to do, and nothing is ever sent or ordered."""

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.agents import crud as agent_crud
from app.agents.definitions import AGENT_KEY
from app.agents.models import AgentDefinition
from app.agents.runner import execute_run
from app.audit.models import ChangeLog
from app.auth.models import BOOKKEEPER
from app.prices import engine
from app.prices.demo import SUPPLIERS, seed as seed_invoices
from app.config import settings
from app.prices.models import AgentQuestion, Invoice, InvoiceLine, ItemAlias, OutreachDraft, PriceDigest, PriceItem, ThreadMessage
from app.prices.routes import QUESTION_PREFIX
from app.restaurant.models import Restaurant, Supplier
from conftest import TEST_USER_EMAIL
from test_restaurant_details import _member
from test_restaurant_setup import _set_up

API = "/api/v1/prices"
TODAY = date.today()


def _restaurant(client, db) -> Restaurant:
    _set_up(client, db)
    return db.query(Restaurant).one()


def _suppliers(db, restaurant) -> dict[str, str]:
    out = {}
    for name in SUPPLIERS:
        supplier = Supplier(restaurant_id=restaurant.id, name=name)
        db.add(supplier)
        db.flush()
        out[name] = supplier.id
    return out


def _seeded(client, db):
    restaurant = _restaurant(client, db)
    seed_invoices(db, restaurant.id, _suppliers(db, restaurant), TODAY)
    db.commit()
    return restaurant


def _lines(*rows):
    return [{"description": d, "qty": q, "pack_text": p, "unit_price": u} for d, q, p, u in rows]


# --- reading invoices ------------------------------------------------------------------


def test_the_first_invoice_brings_one_question_about_its_biggest_line(client, db):
    _restaurant(client, db)
    saved = client.post(f"{API}/invoices", json={
        "supplier_name": "Chef's Warehouse", "invoice_date": "2026-10-21", "source": "photo",
        "lines": _lines(("CHIX BRST BNLS 4/10#", 6, "CS", 94.20), ("CHIX THIGH BNLS 4/10#", 12, "CS", 87.60),
                        ("CHIX WING JUMBO 40#", 4, "CS", 112.00)),
    })
    assert saved.status_code == 201, saved.text
    body = saved.json()
    assert (body["line_count"], body["matched"], body["questions_asked"]) == (3, 0, 3)
    first = body["first_question"]
    # The biggest line, by dollars, is what it asks about -- and it proposes a name, in plain words.
    assert first["dollar_impact"] == 1051.2
    assert first["text"] == "Is this the same as Chicken thigh boneless, 40 lb?"
    assert [o["key"] for o in first["options"]] == ["yes", "no"]
    assert first["has_image"] and [c["hit"] for c in first["crop"]] == [False, True, False]

    status = client.get(f"{API}/status").json()
    assert status["invoice_count"] == 1 and status["questions_open"] == 3
    assert status["progress"] == "1 invoice from Chef's Warehouse. Digest is ready." or status["progress"].startswith("1 invoice from Chef's Warehouse.")
    # A new supplier was made from the name on the invoice.
    assert [s["name"] for s in status["suppliers"]] == ["Chef's Warehouse"]


def test_lines_are_kept_as_printed_and_a_yes_teaches_the_alias(client, db):
    _restaurant(client, db)
    first = client.post(f"{API}/invoices", json={
        "supplier_name": "Worldwide", "invoice_date": "2026-10-14",
        "lines": _lines(("KALE GRN 24CT", 10, "CS", 31.20)),
    }).json()["first_question"]
    answered = client.post(f"{API}/questions/{first['id']}/answer", json={"answer": "yes"}).json()
    assert answered["status"] == "answered"
    item = db.query(PriceItem).one()
    assert (item.name, item.pack_size, item.unit) == ("Green kale, 24 ct", 24, "ct")

    # Same printed line, same supplier, a week later: matched without asking.
    again = client.post(f"{API}/invoices", json={
        "supplier_name": "Worldwide", "invoice_date": "2026-10-21", "lines": _lines(("KALE GRN 24CT", 10, "CS", 31.20)),
    }).json()
    assert (again["matched"], again["questions_asked"]) == (1, 0)
    detail = client.get(f"{API}/items/{item.id}").json()
    assert [l["description"] for l in detail["lines"]] == ["KALE GRN 24CT", "KALE GRN 24CT"]
    assert detail["pack_sentence"] == "Reading this as 24 ct per case."
    assert detail["lines"][0]["per_unit"] == 1.3
    # The thread records the question and the match, with its sources.
    thread = client.get(f"{API}/thread").json()
    assert [m["role"] for m in thread] == ["agent", "agent"]
    assert thread[0]["question"]["status"] == "answered"
    assert thread[1]["text"].startswith("Matched.") and thread[1]["sources"][0]["label"] == "Green kale, 24 ct"


def test_a_no_makes_a_different_item_and_something_else_names_it(client, db):
    _restaurant(client, db)
    client.post(f"{API}/invoices", json={"supplier_name": "Worldwide", "invoice_date": "2026-10-14",
                                        "lines": _lines(("KALE GRN 24CT", 10, "CS", 31.20))})
    client.post(f"{API}/questions/{client.get(f'{API}/questions').json()[0]['id']}/answer", json={"answer": "yes"})
    # A similar line from another supplier: the agent proposes the existing item.
    client.post(f"{API}/invoices", json={"supplier_name": "Chef's Warehouse", "invoice_date": "2026-10-17",
                                        "lines": _lines(("KALE, GREEN CS", 2, "CS", 27.60))})
    (question,) = client.get(f"{API}/questions").json()
    assert question["text"] == "Is this the same as Green kale, 24 ct?"
    assert client.post(f"{API}/questions/{question['id']}/answer", json={"answer": "other"}).status_code == 422
    client.post(f"{API}/questions/{question['id']}/answer", json={"answer": "other", "text": "Kale, baby, 3 lb"})
    assert sorted(i.name for i in db.query(PriceItem)) == ["Green kale, 24 ct", "Kale, baby, 3 lb"]
    assert client.get(f"{API}/questions").json() == []


def test_five_questions_a_week_and_the_rest_wait_in_dollar_order(client, db):
    _restaurant(client, db)
    lines = _lines(*[(f"ITEM NUMBER {n} 10CT", 1, "CS", float(n)) for n in range(1, 9)])
    saved = client.post(f"{API}/invoices", json={"supplier_name": "Worldwide", "invoice_date": "2026-10-21", "lines": lines}).json()
    assert saved["questions_asked"] == 5
    asked = client.get(f"{API}/questions").json()
    assert [q["dollar_impact"] for q in asked] == [8, 7, 6, 5, 4]
    assert db.query(AgentQuestion).filter(AgentQuestion.status == "held").count() == 3
    # The thread only shows what was asked.
    assert len([m for m in client.get(f"{API}/thread").json() if m["question"]]) == 5
    # Next week, the biggest held question comes through.
    engine.promote_held(db, asked[0] and db.query(Restaurant).one().id, date(2026, 11, 2))
    db.commit()
    assert sorted(q.dollar_impact for q in db.query(AgentQuestion).filter(AgentQuestion.status == "open")) == [1, 2, 3, 4, 5, 6, 7, 8]


def test_pasted_text_becomes_lines_and_nothing_is_saved(client, db):
    _restaurant(client, db)
    parsed = client.post(f"{API}/invoices/parse", json={"text": (
        "Green Valley Farms\nInvoice 10/17/2026\n\n"
        "TOMATO ROMA 25#        6  CS   31.20   187.20\n"
        "something the parser can't read\n"
        "ROMAINE 24CT           2  CS   28.10    56.20\n"
    )}).json()
    assert [l["description"] for l in parsed["lines"]] == ["TOMATO ROMA 25#", "ROMAINE 24CT"]
    assert parsed["lines"][0] == {"description": "TOMATO ROMA 25#", "qty": 6, "pack_text": "CS", "unit_price": 31.2,
                                  "extended": 187.2, "delivered_qty": None, "confidence": None}
    assert (parsed["supplier_guess"], parsed["date_guess"], parsed["skipped"]) == ("Green Valley Farms", "2026-10-17", [5])
    assert client.get(f"{API}/invoices").json() == []


# --- the digest ------------------------------------------------------------------------


def test_the_digest_says_what_changed_what_it_cost_and_what_to_do(client, db):
    _seeded(client, db)
    digest = client.get(f"{API}/digest").json()
    sections = {s["key"]: s for s in digest["sections"]}
    assert [s["label"] for s in digest["sections"]] == ["Switch supplier", "Price went up", "Owed to you"]

    switch = {c["item_name"]: c for c in sections["switch"]["cards"]}
    tomatoes = switch["Tomatoes, 25 lb case"]
    assert tomatoes["sentence"].startswith("You paid $31.20 at Green Valley Farms on ")
    assert "Sysco Foods charged $27.60 on " in tomatoes["sentence"]
    assert tomatoes["action"] == {"kind": "move", "label": "Move tomatoes", "draft_kind": None,
                                  "supplier_id": db.query(Supplier).filter_by(name="Sysco Foods").one().id}
    assert tomatoes["impact"] == 25.2  # $3.60 a case, 7 cases a week across both suppliers
    assert [s["kind"] for s in tomatoes["sources"]] == ["invoice", "invoice"]

    up = {c["item_name"]: c for c in sections["up"]["cards"]}
    assert up["Chicken breast, 40 lb case"]["impact"] == 36.0 and "16% in" in up["Chicken breast, 40 lb case"]["sentence"]
    assert up["Chicken breast, 40 lb case"]["action"]["draft_kind"] == "price_challenge"
    # Wine only trends: no switch card, whatever another distributor might charge.
    assert "Chianti, 750 ml, 12 ct" in up and "Chianti, 750 ml, 12 ct" not in switch

    (owed,) = sections["owed"]["cards"]
    assert owed["item_name"] == "Mozzarella, 6 lb case" and owed["impact"] == 31.9
    assert owed["sentence"].startswith("Harbor Dairy Co. billed 5 cases on ") and "delivered 4. No credit on the" in owed["sentence"]

    # Cards are ranked by dollars, the headline adds them up, and nothing is below the bar.
    impacts = [c["impact"] for s in digest["sections"] for c in s["cards"]]
    assert digest["total"] == round(sum(impacts), 2)
    assert digest["item_count"] == len({c["item_id"] for s in digest["sections"] for c in s["cards"]}) == 6 and len(impacts) == 7
    assert all(c["impact"] >= 0.5 for s in digest["sections"] for c in s["cards"])
    assert digest["questions_open"] == 3 and digest["hidden_count"] == 0
    assert digest["supplier_names"] == ["Empire Wine & Spirits", "Green Valley Farms", "Harbor Dairy Co.", "Sysco Foods"]


def test_thin_evidence_is_withheld_not_hedged(client, db):
    _seeded(client, db)
    rows = {r["name"]: r for r in client.get(f"{API}/items").json()}
    basil = rows["Basil, 1 lb"]
    assert basil["state"] == "insufficient" and basil["change_8w"] is None
    detail = client.get(f"{API}/items/{basil['id']}").json()
    assert detail["note"] == "Not enough matched invoices to compare yet." and detail["card"] is None
    assert rows["Butter, unsalted, 36 lb case"]["state"] == "awaiting"
    assert client.get(f"{API}/items/{rows['Butter, unsalted, 36 lb case']['id']}").json()["note"] == "Answer 1 question to compare."
    # No confidence number anywhere in what the page gets.
    assert not any(key in detail for key in ("confidence", "score", "probability"))


def test_not_useful_hides_an_item_for_four_weeks(client, db):
    _seeded(client, db)
    tomatoes = next(r for r in client.get(f"{API}/items").json() if r["name"].startswith("Tomatoes"))
    hidden = client.post(f"{API}/items/{tomatoes['id']}/hide").json()
    assert hidden["state"] == "hidden" and date.fromisoformat(hidden["hidden_until"]) == date.today() + timedelta(weeks=4)
    digest = client.get(f"{API}/digest").json()
    assert "Tomatoes, 25 lb case" not in [c["item_name"] for s in digest["sections"] for c in s["cards"]]
    assert client.post(f"{API}/items/{tomatoes['id']}/unhide").json()["state"] == "ok"
    assert "Tomatoes, 25 lb case" in [c["item_name"] for s in client.get(f"{API}/digest").json()["sections"] for c in s["cards"]]


def test_moving_an_item_is_recorded_and_orders_nothing(client, db):
    _seeded(client, db)
    digest = client.get(f"{API}/digest").json()
    card = next(c for s in digest["sections"] for c in s["cards"] if c["item_name"].startswith("Tomatoes"))
    moved = client.post(f"{API}/items/{card['item_id']}/move", json={"supplier_id": card["action"]["supplier_id"]}).json()
    assert moved["id"] == card["item_id"]
    assert client.get(f"{API}/items/{card['item_id']}").json()["moved_to"]["name"] == "Sysco Foods"
    assert card["item_name"] not in [c["item_name"] for s in client.get(f"{API}/digest").json()["sections"] for c in s["cards"] if s["key"] == "switch"]
    assert client.get(f"{API}/thread").json()[-1]["text"] == "Tomatoes, 25 lb case is moving to Sysco Foods. The next digest checks the invoice."
    # Nothing was proposed, ordered or changed anywhere else.
    assert client.get("/api/v1/agents/actions?status=pending").json() == []


def test_the_pack_size_can_be_corrected_in_place(client, db):
    _seeded(client, db)
    tomatoes = next(r for r in client.get(f"{API}/items").json() if r["name"].startswith("Tomatoes"))
    fixed = client.patch(f"{API}/items/{tomatoes['id']}", json={"pack_size": 20}).json()
    assert fixed["pack_sentence"] == "Reading this as 20 lb per case."
    assert all(l["pack_size"] == 20 for l in fixed["lines"])


def test_the_sparkline_has_one_line_per_supplier_over_eight_weeks(client, db):
    _seeded(client, db)
    tomatoes = next(r for r in client.get(f"{API}/items").json() if r["name"].startswith("Tomatoes"))
    series = client.get(f"{API}/items/{tomatoes['id']}").json()["series"]
    assert [(s["supplier"]["name"], s["current"], len(s["points"])) for s in series] == [
        ("Green Valley Farms", True, 8), ("Sysco Foods", False, 8)]
    assert [p["price"] for p in series[0]["points"]] == [28.8, 28.8, 29.4, 29.4, 30.0, 30.6, 31.2, 31.2]
    assert [p["price"] for p in series[1]["points"]] == [27.6, 27.6, None, None, None, None, 27.6, 27.6]


def test_talking_points_are_five_lines_in_the_owners_words(client, db):
    _seeded(client, db)
    points = client.get(f"{API}/digest/talking-points").json()
    assert 1 <= len(points["lines"]) <= 5
    assert points["lines"][0] == "Chicken breast went up at Sysco Foods, $36.00 a week more. I'm asking the rep."
    assert "Tomatoes moves to Sysco Foods this week, $25.20 a week less." in points["lines"]
    assert points["sources"][0]["kind"] == "digest"
    # The same thing, asked for in the thread, is answered at once without a model.
    reply = client.post(f"{API}/thread", json={"text": "talking points for tomorrow's meeting"}).json()
    assert [m["role"] for m in reply] == ["you", "agent"]
    assert reply[1]["status"] == "ok" and reply[1]["text"].startswith("1. ")


# --- drafts: written by the agent, sent only by a person ------------------------------------


def test_a_draft_is_three_sentences_with_the_lines_behind_it_and_never_sends_itself(client, db):
    _seeded(client, db)
    digest = client.get(f"{API}/digest").json()
    owed = next(c for s in digest["sections"] for c in s["cards"] if s["key"] == "owed")
    draft = client.post(f"{API}/drafts", json={"item_id": owed["item_id"], "kind": "credit_claim",
                                               "supplier_id": owed["action"]["supplier_id"]}).json()
    assert draft["status"] == "draft" and draft["to_name"] is None and draft["remembered_from"] is None
    assert draft["subject"].startswith("Credit for mozzarella, ")
    body = draft["body"]
    assert body.startswith("Hello,\n\n") and body.endswith("\n\nThanks")
    assert "bills 5 cases of Mozzarella, 6 lb case at $31.90 each" in body and "delivery sheet shows 4 received" in body
    assert "Please issue a credit of $31.90" in body
    assert len(draft["lines"]) == 1 and "MOZZ FRESH 6/1#" in draft["lines"][0]["label"]
    assert body.count(". ") + body.count(".\n") <= 4  # three sentences and a sign-off, no pleasantries

    # Sending needs a rep, and only marks it sent: the mail leaves from the owner's own mail app.
    assert client.post(f"{API}/drafts/{draft['id']}/send").status_code == 422
    client.patch(f"{API}/drafts/{draft['id']}", json={"to_name": "Dana Ruiz", "to_email": "dana@harbordairy.example.com"})
    sent = client.post(f"{API}/drafts/{draft['id']}/send").json()
    assert sent["status"] == "sent" and sent["sent_at"]
    assert client.patch(f"{API}/drafts/{draft['id']}", json={"subject": "x"}).status_code == 409

    # The rep is remembered for the next message to that supplier.
    again = client.post(f"{API}/drafts", json={"item_id": owed["item_id"], "supplier_id": owed["action"]["supplier_id"]}).json()
    assert (again["to_name"], again["to_email"], again["remembered_from"]) == ("Dana Ruiz", "dana@harbordairy.example.com", date.today().isoformat())
    assert again["body"].startswith("Hi Dana,")
    assert client.post(f"{API}/drafts/{again['id']}/discard").json()["status"] == "discarded"
    # Drafts show in the thread.
    assert [m["draft"]["status"] for m in client.get(f"{API}/thread").json() if m["draft"]] == ["sent", "discarded"]


def test_a_price_challenge_cites_what_was_paid_and_who_charged_less(client, db):
    _seeded(client, db)
    tomatoes = next(r for r in client.get(f"{API}/items").json() if r["name"].startswith("Tomatoes"))
    draft = client.post(f"{API}/drafts", json={"item_id": tomatoes["id"]}).json()
    assert draft["kind"] == "price_challenge" and draft["supplier"]["name"] == "Green Valley Farms"
    assert "bills Tomatoes, 25 lb case at $31.20 a case" in draft["body"]
    assert "Another supplier charged $27.60 for the same case the same month" in draft["body"]
    assert "market" not in draft["body"].lower() and "wrong" not in draft["body"].lower()


# --- the thread: ask, and the agent answers from the invoices ----------------------------------


def test_asking_a_question_runs_the_agent_and_the_answer_cites_its_invoices(client, db):
    _seeded(client, db)
    reply = client.post(f"{API}/thread", json={"text": "what did I pay for tomatoes last week"})
    assert reply.status_code == 201, reply.text
    yours, pending = reply.json()
    assert yours["text"] == "what did I pay for tomatoes last week" and pending["status"] == "pending"

    run = db.query(ThreadMessage).get(pending["id"]).run_id
    assert run and run == agent_crud.get_run(db, run).id
    assert agent_crud.get_run(db, run).input == f"{QUESTION_PREFIX}what did I pay for tomatoes last week"
    execute_run(run, db)  # the scripted provider stands in for the model

    thread = client.get(f"{API}/thread").json()
    answer = next(m for m in thread if m["id"] == pending["id"])
    assert answer["status"] == "ok"
    assert answer["text"].startswith("You paid $31.20 a case for Tomatoes, 25 lb case at Green Valley Farms on ")
    assert "Sysco Foods charged $27.60 on " in answer["text"] and answer["text"].endswith("Move tomatoes.")
    labels = [s["label"] for s in answer["sources"]]
    assert answer["sources"][0]["kind"] == "item" and labels[0] == "Tomatoes, 25 lb case"
    assert any(l.endswith("Green Valley Farms") for l in labels) and any(l.endswith("Sysco Foods") for l in labels)


def test_asking_about_short_deliveries_and_about_an_item_in_context(client, db):
    _seeded(client, db)
    _, pending = client.post(f"{API}/thread", json={"text": "how much did Harbor Dairy short me this quarter"}).json()
    execute_run(db.query(ThreadMessage).get(pending["id"]).run_id, db)
    answer = next(m for m in client.get(f"{API}/thread").json() if m["id"] == pending["id"])
    assert answer["text"].startswith("Harbor Dairy Co. billed $31.90 more than it delivered across 1 invoice")
    assert answer["text"].endswith("Draft a credit claim.") and answer["sources"][0]["kind"] == "invoice"

    chicken = next(r for r in client.get(f"{API}/items").json() if r["name"].startswith("Chicken"))
    yours, pending = client.post(f"{API}/thread", json={"text": "why is this up", "item_id": chicken["id"]}).json()
    assert yours["text"] == "Chicken breast, 40 lb case: why is this up"
    execute_run(db.query(ThreadMessage).get(pending["id"]).run_id, db)
    answer = next(m for m in client.get(f"{API}/thread").json() if m["id"] == pending["id"])
    assert answer["text"].startswith("You paid $87.60 a case for Chicken breast, 40 lb case at Sysco Foods on ")
    assert "it was $75.60" in answer["text"]


def test_an_answer_that_never_comes_is_marked_failed_not_left_pending(client, db):
    _seeded(client, db)
    _, pending = client.post(f"{API}/thread", json={"text": "hello"}).json()
    run = agent_crud.get_run(db, db.query(ThreadMessage).get(pending["id"]).run_id)
    agent_crud.set_run_status(db, run, "failed")
    db.commit()
    answer = next(m for m in client.get(f"{API}/thread").json() if m["id"] == pending["id"])
    assert answer["status"] == "failed" and answer["text"].startswith("Couldn't answer that.")


# --- who may do what ---------------------------------------------------------------------


def test_members_read_and_answer_but_do_not_write_invoices_or_drafts(client, db):
    _seeded(client, db)
    member = _member(client, db)
    assert member.get(f"{API}/digest").status_code == 200
    question = member.get(f"{API}/questions").json()[0]
    assert member.post(f"{API}/questions/{question['id']}/answer", json={"answer": "yes"}).status_code == 200
    assert member.post(f"{API}/invoices", json={"supplier_name": "X", "invoice_date": "2026-10-01", "lines": _lines(("A", 1, "CS", 1))}).status_code == 403
    tomatoes = next(r for r in member.get(f"{API}/items").json() if r["name"].startswith("Tomatoes"))
    assert member.post(f"{API}/drafts", json={"item_id": tomatoes["id"]}).status_code == 403
    assert member.post(f"{API}/items/{tomatoes['id']}/hide").status_code == 403


def test_a_bookkeeper_answers_for_clients_and_the_owner_sees_it_resolved(client, db):
    from app.main import app

    restaurant = _seeded(client, db)
    # The owner invites their accountant as a bookkeeper.
    token = client.post("/api/v1/auth/invites", json={"role": BOOKKEEPER, "email": "books@example.com"}).json()["token"]
    books = TestClient(app)
    joined = books.post(f"/api/v1/auth/invite/{token}", json={"name": "Pat Books", "email": "books@example.com", "password": "a-long-password"})
    assert joined.status_code == 201, joined.text
    assert (joined.json()["role"], joined.json()["restaurant_id"]) == (BOOKKEEPER, None)

    # Bookkeepers see a clients list, not a restaurant.
    assert books.get(f"{API}/digest").status_code == 403
    assert client.get(f"{API}/bookkeeper/clients").status_code == 403
    (row,) = books.get(f"{API}/bookkeeper/clients").json()
    assert row["name"] == restaurant.name and row["questions_open"] == 3 and row["flagged_month"] > 0
    assert row["last_digest"] == engine.week_start(date.today()).isoformat()

    questions = books.get(f"{API}/bookkeeper/clients/{restaurant.id}/questions").json()
    assert [q["dollar_impact"] for q in questions] == sorted((q["dollar_impact"] for q in questions), reverse=True)
    answered = books.post(f"{API}/bookkeeper/clients/{restaurant.id}/questions/{questions[0]['id']}/answer", json={"answer": "yes"})
    assert answered.status_code == 200, answered.text
    # The owner sees it resolved, and nothing says who by.
    assert len(client.get(f"{API}/questions").json()) == 2
    resolved = next(m for m in client.get(f"{API}/thread").json() if m["question"] and m["question"]["id"] == questions[0]["id"])
    assert resolved["question"]["status"] == "answered"
    assert "books@example.com" not in str(client.get(f"{API}/thread").json())

    # A second restaurant's invite adds a client to the same account.
    other = Restaurant(name="Other Place", setup_completed_at=None)
    db.add(other)
    db.flush()
    from app.auth.models import Invite
    from app.auth.security import new_session_token, token_id
    from datetime import datetime, timezone

    plain = new_session_token()
    db.add(Invite(token_hash=token_id(plain), restaurant_id=other.id, role=BOOKKEEPER, email="books@example.com",
                  expires_at=datetime.now(timezone.utc) + timedelta(days=7)))
    db.commit()
    second = TestClient(app).post(f"/api/v1/auth/invite/{plain}", json={"name": "Pat Books", "email": "books@example.com", "password": "a-long-password"})
    assert second.status_code == 201, second.text
    assert [c["name"] for c in books.get(f"{API}/bookkeeper/clients").json()] == [restaurant.name, "Other Place"]
    assert books.get(f"{API}/bookkeeper/clients/{other.id}/questions").json() == []


def test_another_restaurant_sees_none_of_it(client, db):
    from test_restaurant_details import _second_restaurant

    restaurant = _seeded(client, db)
    _, other_owner = _second_restaurant(db)
    assert other_owner.get(f"{API}/digest").json()["total"] == 0
    assert other_owner.get(f"{API}/items").json() == [] and other_owner.get(f"{API}/questions").json() == []
    tomatoes = next(r for r in client.get(f"{API}/items").json() if r["name"].startswith("Tomatoes"))
    assert other_owner.get(f"{API}/items/{tomatoes['id']}").status_code == 404
    question = client.get(f"{API}/questions").json()[0]
    assert other_owner.post(f"{API}/questions/{question['id']}/answer", json={"answer": "yes"}).status_code == 404


def test_the_history_keeps_the_agents_questions_as_the_agents_and_the_answers_as_yours(client, db):
    _restaurant(client, db)
    saved = client.post(f"{API}/invoices", json={"supplier_name": "Worldwide", "invoice_date": "2026-10-14",
                                                "lines": _lines(("KALE GRN 24CT", 10, "CS", 31.20))}).json()
    client.post(f"{API}/questions/{saved['first_question']['id']}/answer", json={"answer": "yes"})
    rows = db.query(ChangeLog).all()
    by_table = {}
    for row in rows:
        by_table.setdefault(row.entity_type, []).append(row)
    # The invoice came from a person; the question it raised is the agent's; the answer is the person's.
    assert {r.actor_type for r in by_table["supplier_invoices"]} == {"human"}
    asked = [r for r in by_table["agent_questions"] if r.operation == "insert"]
    answered = [r for r in by_table["agent_questions"] if r.operation == "update"]
    assert asked and {r.actor_type for r in asked} == {"agent"} and asked[0].actor_id == AGENT_KEY
    assert answered and {r.actor_type for r in answered} == {"human"} and answered[0].actor_id
    assert "price_items" in by_table and "price_item_aliases" in by_table
    # Lines, digests and the thread are not history: one invoice is one event, and the digest is rebuilt constantly.
    assert not set(by_table) & {"supplier_invoice_lines", "price_digests", "agent_thread_messages"}
    # The History page shows them with words, not table names.
    from app.audit.labels import labels_for
    labels = labels_for(db, rows)
    assert any(label.startswith("question: Is this the same as") for label in labels.values())


def test_the_agent_has_no_tool_that_sends_orders_or_pays(client, db):
    from app.agents.models import AgentRun
    from app.agents.registry import resolve_tools

    restaurant = _seeded(client, db)
    defn = db.query(AgentDefinition).filter_by(key=AGENT_KEY).one()
    run = AgentRun(restaurant_id=restaurant.id, agent_definition_id=defn.id, status="queued", trigger_type="manual", input="x")
    db.add(run)
    db.flush()
    names = {t.name for t in resolve_tools(db, run, defn) if getattr(t, "area", "") == "prices"}
    assert names == {"list_price_items", "item_price_history", "supplier_price_summary", "short_deliveries", "answer_price_question"}
    assert not any(word in n for n in names for word in ("send", "order", "pay", "edit", "delete"))


# --- reading photos and PDFs -------------------------------------------------------------

FIXTURES = __import__("pathlib").Path(__file__).resolve().parent.parent / "fixtures" / "invoices"


def _pdf_body(name: str) -> dict:
    import base64

    return {"filename": name, "media_type": "application/pdf",
            "data_base64": base64.b64encode((FIXTURES / name).read_bytes()).decode()}


def test_a_pdf_is_read_into_lines_with_the_header_and_nothing_is_saved(client, db):
    _restaurant(client, db)
    assert client.get(f"{API}/status").json()["reader"] == "text"
    read = client.post(f"{API}/invoices/read", json=_pdf_body("worldwide-produce-2026-10-21.pdf"))
    assert read.status_code == 200, read.text
    body = read.json()
    assert (body["supplier_guess"], body["date_guess"], body["reference"], body["total"]) == ("Worldwide Produce", "2026-10-21", "WP-10021", 753.1)
    assert [l["description"] for l in body["lines"]][:2] == ["KALE GREEN 24 CT", "BROCCOLI CROWNS CS 12"]
    assert all(l["confidence"] == 1.0 for l in body["lines"])
    assert (body["needs_review"], body["reasons"], body["reader"]) == (False, [], "text")
    assert client.get(f"{API}/invoices").json() == []


def test_a_total_that_does_not_add_up_needs_a_check_and_stays_out_of_prices_until_confirmed(client, db):
    _restaurant(client, db)
    read = client.post(f"{API}/invoices/read", json=_pdf_body("chefs-warehouse-2026-10-17.pdf")).json()
    assert read["needs_review"] and read["reasons"] == ["Lines add to $515.20; the invoice says $542.10."]

    # Saved as read, total and all: kept, questions asked, but not counted.
    saved = client.post(f"{API}/invoices", json={
        "supplier_name": read["supplier_guess"], "invoice_date": read["date_guess"], "reference": read["reference"],
        "source": "pdf", "total": read["total"], "lines": read["lines"],
    })
    assert saved.status_code == 201, saved.text
    assert saved.json()["questions_asked"] == 4
    row = client.get(f"{API}/invoices").json()[0]
    assert (row["status"], row["printed_total"], row["total"]) == ("needs_review", 542.1, 515.2)
    assert row["review_reasons"] == ["Lines add to $515.20; the invoice says $542.10."]
    client.post(f"{API}/questions/{saved.json()['first_question']['id']}/answer", json={"answer": "yes"})
    assert client.get(f"{API}/items").json()[0]["state"] == "insufficient"

    # A member can't confirm; a manager can, and the lines count from then on.
    member = _member(client, db)
    assert member.post(f"{API}/invoices/{row['id']}/confirm").status_code == 403
    confirmed = client.post(f"{API}/invoices/{row['id']}/confirm").json()
    assert (confirmed["status"], confirmed["review_reasons"]) == ("complete", [])
    assert client.get(f"{API}/items").json()[0]["last_price"] is not None


def test_a_line_whose_arithmetic_is_off_or_that_was_read_with_doubt_needs_a_check(client, db):
    from app.prices import extract, schemas

    lines = [schemas.LineIn(description="KALE GREEN 24 CT", qty=10, unit_price=31.20, extended=310.00),
             schemas.LineIn(description="BR0CC0LI CR0WNS", qty=6, unit_price=28.50, extended=171.00, confidence=0.55)]
    assert extract.check(lines, 481.00) == [
        "Line 1: 10 × $31.20 is $312.00; the invoice says $310.00.",
        "Line 2 was hard to read: “BR0CC0LI CR0WNS”. Check it against the invoice.",
    ]
    assert extract.check(lines[:1], 310.00) == ["Line 1: 10 × $31.20 is $312.00; the invoice says $310.00."]
    assert extract.check([schemas.LineIn(description="X", qty=2, unit_price=1.005, extended=2.01)], 2.01) == []

    # The scripted reader won't guess at a photo; it says to type the lines.
    _restaurant(client, db)
    import base64
    photo = client.post(f"{API}/invoices/read", json={"media_type": "image/jpeg", "data_base64": base64.b64encode(b"\xff\xd8\xff").decode()})
    assert photo.status_code == 422 and "Type the lines" in photo.json()["detail"]


def test_claudes_reading_is_checked_the_same_way(client, db):
    from app.prices import extract

    out = extract.from_recorded({
        "supplier_name": "Chef's Warehouse", "supplier_confidence": 0.95, "invoice_date": "2026-10-17",
        "date_confidence": 0.6, "reference": "CW-55017", "total": 515.20, "total_confidence": 0.99,
        "lines": [
            {"description": "KALE, GREEN 24CT", "quantity": 8, "pack_text": "CS", "unit_price": 27.60, "line_total": 220.80, "confidence": 0.97},
            {"description": "EVOO 4/1GAL", "quantity": 1, "pack_text": "CS", "unit_price": 64.00, "line_total": 64.00, "confidence": 0.9},
            {"description": "BROCCOLI CROWN 12 CT CS", "quantity": 6, "unit_price": 30.10, "line_total": 180.60, "confidence": 0.92},
            {"description": "CONTAINER DELI 16OZ 500CT", "quantity": 1, "unit_price": 49.80, "line_total": 49.80, "confidence": 0.7},
        ],
    })
    assert out.reader == "claude" and out.total == 515.2 and len(out.lines) == 4
    assert out.reasons == [
        "Line 4 was hard to read: “CONTAINER DELI 16OZ 500CT”. Check it against the invoice.",
        "The invoice date was hard to read. Check it.",
    ]


def test_all_five_fixture_invoices_read_and_the_two_suppliers_overlap(client, db):
    _restaurant(client, db)
    for name in sorted(p.name for p in FIXTURES.glob("*.pdf")):
        read = client.post(f"{API}/invoices/read", json=_pdf_body(name)).json()
        saved = client.post(f"{API}/invoices", json={
            "supplier_name": read["supplier_guess"], "invoice_date": read["date_guess"], "reference": read["reference"],
            "source": "pdf", "total": read["total"], "lines": read["lines"],
        })
        assert saved.status_code == 201, saved.text
    status = client.get(f"{API}/status").json()
    assert status["invoice_count"] == 5 and {s["name"] for s in status["suppliers"]} == {"Worldwide Produce", "Chef's Warehouse"}
    # Kale from both suppliers reads as the same proposed item.
    texts = [q["text"] for q in client.get(f"{API}/questions").json()]
    assert any("kale" in t.lower() for t in texts)


# --- pack changes ------------------------------------------------------------------------


def test_a_changed_pack_is_asked_about_not_assumed(client, db):
    _restaurant(client, db)
    first = client.post(f"{API}/invoices", json={"supplier_name": "Worldwide", "invoice_date": "2026-10-07",
                                                "lines": _lines(("KALE GRN", 10, "24 CT", 31.20))}).json()
    client.post(f"{API}/questions/{first['first_question']['id']}/answer", json={"answer": "yes"})
    # A pack printed in the description is part of how the line reads: "KALE GRN 12CT" is a new line to match.
    again = client.post(f"{API}/invoices", json={"supplier_name": "Worldwide", "invoice_date": "2026-10-14",
                                                "lines": _lines(("KALE GRN 12CT", 10, "CS", 31.20))}).json()
    assert again["questions_asked"] == 1 and again["first_question"] is None
    # The same printed line with a different pack column is the pack-change case: no silent 2x per-head price.
    third = client.post(f"{API}/invoices", json={"supplier_name": "Worldwide", "invoice_date": "2026-10-21",
                                                "lines": _lines(("KALE GRN", 10, "12 CT", 31.20))}).json()
    assert third["matched"] == 0 and third["questions_asked"] == 1
    question = next(q for q in client.get(f"{API}/questions").json() if q["kind"] == "repack")
    assert question["text"] == "Pack changed? This says 12 ct per case; you had 24 ct."
    assert [o["label"] for o in question["options"]] == ["Yes, 12 ct now", "No, still 24 ct"]
    answered = client.post(f"{API}/questions/{question['id']}/answer", json={"answer": "no"}).json()
    assert answered["status"] == "answered"
    line = db.query(InvoiceLine).filter(InvoiceLine.pack_text == "12 CT").one()
    assert line.pack_size == 24
    thread = client.get(f"{API}/thread").json()
    assert thread[-1]["text"] == "Noted. Green kale, 24 ct stays 24 ct per case."


def test_a_pack_change_said_yes_to_updates_the_match_for_next_time(client, db):
    _restaurant(client, db)
    first = client.post(f"{API}/invoices", json={"supplier_name": "Worldwide", "invoice_date": "2026-10-07",
                                                "lines": _lines(("KALE GRN", 10, "24 CT", 31.20))}).json()
    client.post(f"{API}/questions/{first['first_question']['id']}/answer", json={"answer": "yes"})
    client.post(f"{API}/invoices", json={"supplier_name": "Worldwide", "invoice_date": "2026-10-14",
                                        "lines": _lines(("KALE GRN", 10, "12 CT", 31.20))})
    question = next(q for q in client.get(f"{API}/questions").json() if q["kind"] == "repack")
    client.post(f"{API}/questions/{question['id']}/answer", json={"answer": "yes"})
    alias = db.query(ItemAlias).one()
    assert alias.pack_size == 12
    # The next one with the same printing and pack goes straight through.
    saved = client.post(f"{API}/invoices", json={"supplier_name": "Worldwide", "invoice_date": "2026-10-21",
                                                "lines": _lines(("KALE GRN", 10, "12 CT", 31.20))}).json()
    assert (saved["matched"], saved["questions_asked"]) == (1, 0)


# --- the digest as mail ------------------------------------------------------------------


def test_the_digest_mails_as_html_and_text_with_the_same_three_sections(client, db, monkeypatch):
    from app.prices import mail

    monkeypatch.setattr(settings, "resend_api_key", None)
    monkeypatch.setattr(settings, "public_url", "https://fullhouse.example")
    logged: list[str] = []
    monkeypatch.setattr(mail.logger, "info", lambda msg, *args, **kw: logged.append(msg % args if args else msg))
    _seeded(client, db)
    sent = client.post(f"{API}/digest/email")
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["sent_to"] == [TEST_USER_EMAIL] and body["delivered"] is False
    assert body["subject"].startswith("$") and body["subject"].endswith(" at stake this week")
    # Without a mail key, the text version went to the log, with its links.
    log = "\n".join(logged)
    assert "SWITCH SUPPLIER" in log and "PRICE WENT UP" in log and "OWED TO YOU" in log
    assert "https://fullhouse.example/#digest" in log and "/api/v1/prices/digest/not-useful/" in log

    page = client.get(f"{API}/digest/email/preview")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    html_text = page.text
    for label in ("Switch supplier", "Price went up", "Owed to you", "Not useful", "Open the digest"):
        assert label in html_text
    # Red-orange only on the "Price went up" figures and header, green only on "Owed to you".
    assert html_text.index(mail.UP) > html_text.index("Price went up") - 400
    assert mail.OWED in html_text
    digest = db.query(PriceDigest).one()
    assert digest.emailed_at is not None


def test_a_delivered_mail_goes_through_resend(client, db, monkeypatch):
    import httpx

    from app.prices import mail

    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers, json))
        return httpx.Response(200, json={"id": "re_1"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(settings, "resend_api_key", "re_test")
    monkeypatch.setattr(mail.httpx, "post", fake_post)
    _seeded(client, db)
    body = client.post(f"{API}/digest/email").json()
    assert body["delivered"] is True
    url, headers, payload = calls[0]
    assert url == mail.RESEND_URL and headers["Authorization"] == "Bearer re_test"
    assert payload["to"] == [TEST_USER_EMAIL] and payload["subject"] == body["subject"] and "<html" in payload["html"]


def test_a_mailbox_over_smtp_delivers_the_mail_with_no_domain(client, db, monkeypatch):
    from app.prices import mail

    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            self.host, self.port, self.tls, self.auth = host, port, False, None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            self.tls = True

        def login(self, user, password):
            self.auth = (user, password)

        def send_message(self, message):
            sent.append((self, message))

    class FakeSSL(FakeSMTP):
        ssl = True

    monkeypatch.setattr(settings, "resend_api_key", None)
    monkeypatch.setattr(settings, "smtp_host", "smtp.gmail.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_user", "owner@gmail.com")
    monkeypatch.setattr(settings, "smtp_password", "abcd efgh ijkl mnop")
    monkeypatch.setattr(settings, "mail_from", "FullHouse <owner@gmail.com>")
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", FakeSSL)
    assert settings.smtp_configured and settings.mail_configured
    _seeded(client, db)
    body = client.post(f"{API}/digest/email").json()
    assert body["delivered"] is True
    server, message = sent[0]
    assert (server.host, server.port, server.tls, server.auth) == ("smtp.gmail.com", 587, True, ("owner@gmail.com", "abcd efgh ijkl mnop"))
    assert message["From"] == "FullHouse <owner@gmail.com>" and message["To"] == TEST_USER_EMAIL and message["Subject"] == body["subject"]
    parts = {p.get_content_type(): p.get_content() for p in message.iter_parts()}
    assert "<html" in parts["text/html"] and "at stake this week" in parts["text/plain"]

    # Port 465 is TLS from the first byte, with no STARTTLS.
    monkeypatch.setattr(settings, "smtp_port", 465)
    client.post(f"{API}/digest/email")
    server, _ = sent[-1]
    assert getattr(server, "ssl", False) and not server.tls and server.auth[0] == "owner@gmail.com"

    # A password alone isn't a mailbox: nothing is sent, the log gets it.
    monkeypatch.setattr(settings, "smtp_user", None)
    assert not settings.smtp_configured
    assert client.post(f"{API}/digest/email").json()["delivered"] is False and len(sent) == 2


def test_the_not_useful_link_in_the_mail_hides_the_item_for_four_weeks(client, db):
    from app.prices import mail

    restaurant = _seeded(client, db)
    digest = client.get(f"{API}/digest").json()
    card = digest["sections"][0]["cards"][0]
    token = mail.not_useful_token(restaurant.id, card["item_id"], date.fromisoformat(digest["week_start"]))
    page = client.get(f"{API}/digest/not-useful/{token}")
    assert page.status_code == 200 and f"{card['item_name']} hidden." in page.text
    item = db.get(PriceItem, card["item_id"])
    assert item.hidden_until == TODAY + timedelta(weeks=4)
    after = client.get(f"{API}/digest").json()
    assert card["item_id"] not in {c["item_id"] for s in after["sections"] for c in s["cards"]}
    # A forged or stale token does nothing.
    assert client.get(f"{API}/digest/not-useful/{token[:-4]}xxxx").status_code == 410
    old = mail.not_useful_token(restaurant.id, card["item_id"], TODAY - timedelta(weeks=20))
    assert client.get(f"{API}/digest/not-useful/{old}").status_code == 410


def test_the_weekly_mail_goes_once_to_owners_and_admins_and_the_cron_route_needs_the_secret(client, db, monkeypatch):
    from app.prices import mail

    monkeypatch.setattr(settings, "resend_api_key", None)
    monkeypatch.setattr(settings, "cron_secret", None)
    _seeded(client, db)
    _member(client, db)
    assert client.post(f"{API}/digest/cron").status_code == 403
    monkeypatch.setattr(settings, "cron_secret", "s3cret")
    assert client.post(f"{API}/digest/cron", headers={"X-Cron-Secret": "wrong"}).status_code == 403
    first = client.post(f"{API}/digest/cron", headers={"X-Cron-Secret": "s3cret"}).json()
    assert first == {"sent": 1, "skipped": 0}
    again = client.post(f"{API}/digest/cron", headers={"X-Cron-Secret": "s3cret"}).json()
    assert again == {"sent": 0, "skipped": 1}
    # Members don't get it.
    restaurant = db.query(Restaurant).one()
    assert mail.recipients(db, restaurant.id) == [TEST_USER_EMAIL]


def test_the_scheduler_mails_each_restaurant_on_its_own_monday(client, db, monkeypatch):
    from app.agents import scheduler

    monkeypatch.setattr(settings, "resend_api_key", None)
    restaurant = _seeded(client, db)
    db.commit()
    assert scheduler.run_digest_mail(restaurant.id, db=db) is True
    assert scheduler.run_digest_mail(restaurant.id, db=db) is False
    assert scheduler.parse_cron(settings.digest_cron).fields  # "0 6 * * 1" is a valid schedule


# --- Bill.com ----------------------------------------------------------------------------


def _billcom(monkeypatch, bills: list[dict]):
    import httpx

    from app.prices import billcom

    for key, value in (("billcom_username", "u"), ("billcom_password", "p"), ("billcom_org_id", "o"), ("billcom_dev_key", "k")):
        monkeypatch.setattr(settings, key, value)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.headers)))
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"sessionId": "sess-1"})
        if request.headers.get("sessionid") != "sess-1":
            return httpx.Response(401, json={"error": "no session"})
        if request.url.path.endswith("/bills"):
            return httpx.Response(200, json={"results": bills, "nextPage": None})
        if "/vendors/" in request.url.path:
            vendor = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"id": vendor, "name": {"v1": "Worldwide Produce", "v2": "Chef's Warehouse"}[vendor]})
        return httpx.Response(404)

    client = billcom.BillcomClient(transport=httpx.MockTransport(handler))
    return client, seen


BILLS = [
    {"id": "bill-1", "vendorId": "v1", "amount": 312.00, "invoice": {"invoiceNumber": "WP-10021", "invoiceDate": "2026-10-21"},
     "billLineItems": [{"description": "KALE GREEN 24 CT", "quantity": 10, "price": 31.20, "amount": 312.00}]},
    {"id": "bill-2", "vendorId": "v2", "amount": 220.80, "invoice": {"invoiceNumber": "CW-55017", "invoiceDate": "2026-10-17"},
     "billLineItems": [{"description": "KALE, GREEN 24CT", "quantity": 8, "price": 27.60, "amount": 220.80}]},
    {"id": "bill-3", "vendorId": "v2", "amount": 0, "invoice": {"invoiceNumber": "CW-0", "invoiceDate": "2026-10-18"}, "billLineItems": []},
]


def test_bills_pulled_from_billcom_become_invoices_once(client, db, monkeypatch):
    from app.prices import billcom

    restaurant = _restaurant(client, db)
    bc, seen = _billcom(monkeypatch, BILLS)
    out = billcom.sync(db, restaurant.id, client=bc, today=TODAY)
    assert (out.pulled, out.added, out.skipped, out.questions_asked) == (3, 2, 1, 2)
    rows = client.get(f"{API}/invoices").json()
    assert [(r["supplier"]["name"], r["reference"], r["source"], r["status"]) for r in rows] == [
        ("Worldwide Produce", "WP-10021", "billcom", "complete"), ("Chef's Warehouse", "CW-55017", "billcom", "complete"),
    ]
    assert {i.external_id for i in db.query(Invoice)} == {"bill-1", "bill-2"}
    # Signed in once, read only: no POST but the login.
    assert [m for m, path, _ in seen if m == "POST"] == ["POST"] and all(p.endswith("/login") for m, p, _ in seen if m == "POST")
    # The same bills again, by sync or by webhook, add nothing.
    again = billcom.sync(db, restaurant.id, client=bc, today=TODAY)
    assert (again.added, again.skipped) == (0, 3)
    assert db.query(Invoice).count() == 2


def test_billcom_routes_say_when_it_is_not_connected_and_the_webhook_needs_its_secret(client, db, monkeypatch):
    _restaurant(client, db)
    for key in ("billcom_username", "billcom_password", "billcom_org_id", "billcom_dev_key"):
        monkeypatch.setattr(settings, key, None)
    assert client.get(f"{API}/status").json()["billcom_configured"] is False
    status = client.get(f"{API}/billcom/status").json()
    assert (status["configured"], status["environment"], status["webhook_path"]) == (False, None, "/api/v1/prices/billcom/webhook")
    assert client.post(f"{API}/billcom/sync").status_code == 503
    monkeypatch.setattr(settings, "billcom_webhook_secret", "hook")
    assert client.post(f"{API}/billcom/webhook").status_code == 403
    assert client.post(f"{API}/billcom/webhook", headers={"X-Billcom-Secret": "hook"}).status_code == 503


# --- what the agent and the aliases can never hold ------------------------------------------


def test_an_alias_never_stores_a_price(client, db):
    """The match between a printed line and an item is a fact about the item, not about money."""
    from sqlalchemy import inspect

    columns = {c.key for c in inspect(ItemAlias).columns}
    assert not any(word in c for c in columns for word in ("price", "cost", "amount", "total"))


def test_the_agent_cannot_write_invoices_items_or_matches_through_its_tools(client, db):
    from app.agents.models import AgentRun
    from app.agents.registry import resolve_tools

    _seeded(client, db)
    defn = db.query(AgentDefinition).filter(AgentDefinition.key == AGENT_KEY).one()
    run = agent_crud.create_run(db, defn.id, trigger_type="manual", input="look")
    db.flush()
    tools = {t.name: t for t in resolve_tools(db, run, defn) if getattr(t, "area", None) == "prices"}
    assert set(tools) == {"list_price_items", "item_price_history", "supplier_price_summary", "short_deliveries", "answer_price_question"}
    before = tuple(db.query(m).count() for m in (Invoice, InvoiceLine, PriceItem, ItemAlias, AgentQuestion, OutreachDraft))
    item = db.query(PriceItem).first()
    for name, args in (("list_price_items", {}), ("item_price_history", {"item_id": item.id}),
                       ("supplier_price_summary", {}), ("short_deliveries", {"days": 60})):
        tools[name].call(args)
    after = tuple(db.query(m).count() for m in (Invoice, InvoiceLine, PriceItem, ItemAlias, AgentQuestion, OutreachDraft))
    assert before == after
    assert db.query(ThreadMessage).filter(ThreadMessage.role == "agent", ThreadMessage.status == "pending").count() == 0


# --- reading without an Anthropic key -----------------------------------------------------------


def _compressed(path) -> bytes:
    """The fixture PDF as a supplier's system would write it: content streams deflated, no plain strings."""
    import io

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(str(path)).pages:
        writer.add_page(page)
    for page in writer.pages:
        page.compress_content_streams()
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_with_no_model_a_pdf_that_carries_text_is_read_by_rule_and_a_photo_is_typed(client, db, monkeypatch):
    import base64

    from app.prices import extract

    monkeypatch.setattr(settings, "llm_provider", "none")
    _restaurant(client, db)
    assert client.get(f"{API}/status").json()["reader"] == "text"

    data = _compressed(FIXTURES / "worldwide-produce-2026-10-21.pdf")
    assert extract._TJ_RE.search(data) is None  # nothing for the demo's reader; pypdf has to do it
    read = client.post(f"{API}/invoices/read", json={"filename": "wp.pdf", "media_type": "application/pdf",
                                                      "data_base64": base64.b64encode(data).decode()})
    assert read.status_code == 200, read.text
    body = read.json()
    assert (body["supplier_guess"], body["date_guess"], body["reference"], body["total"]) == ("Worldwide Produce", "2026-10-21", "WP-10021", 753.1)
    assert [l["description"] for l in body["lines"]][:2] == ["KALE GREEN 24 CT", "BROCCOLI CROWNS CS 12"]
    assert (body["needs_review"], body["reader"]) == (False, "text")

    photo = client.post(f"{API}/invoices/read", json={"media_type": "image/jpeg", "data_base64": base64.b64encode(b"\xff\xd8\xff").decode()})
    assert photo.status_code == 422 and photo.json()["detail"] == "This site reads PDFs only. Type the lines from the photo."
    # A PDF with nothing but a picture in it is told apart from one with no lines.
    blank = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>"
    scan = client.post(f"{API}/invoices/read", json={"media_type": "application/pdf", "data_base64": base64.b64encode(blank).decode()})
    assert scan.status_code == 422 and "no text in it" in scan.json()["detail"]


RECORDED = {
    "supplier_name": "Worldwide Produce", "supplier_confidence": 0.99, "invoice_date": "2026-10-21", "date_confidence": 0.99,
    "reference": "WP-10021", "total": 448.00, "total_confidence": 0.99,
    "lines": [
        {"description": "KALE GREEN 24 CT", "quantity": 10, "pack_text": "CS", "unit_price": 31.20, "line_total": 312.00, "confidence": 0.98},
        {"description": "OLIVE OIL EV 4/1 GAL", "quantity": 2, "pack_text": "CS", "unit_price": 68.00, "line_total": 136.00, "confidence": 0.95},
    ],
}


def test_a_model_on_the_openai_api_reads_a_photo_as_an_image_and_a_pdf_as_its_text(client, db, monkeypatch):
    import base64
    import json

    import httpx

    from app.prices import extract

    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_api_key", "gemini-key")
    monkeypatch.setattr(settings, "llm_model", "gemini-test")
    monkeypatch.setattr(settings, "llm_base_url", "https://example.test/v1")
    monkeypatch.setattr(settings, "invoice_reader_model", None)
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "record_invoice", "arguments": json.dumps(RECORDED)}},
        ]}}]})

    monkeypatch.setattr(extract, "_post", fake_post)
    _restaurant(client, db)
    assert client.get(f"{API}/status").json()["reader"] == "openai"

    photo = client.post(f"{API}/invoices/read", json={"media_type": "image/jpeg", "data_base64": base64.b64encode(b"\xff\xd8\xff").decode()})
    assert photo.status_code == 200, photo.text
    assert (photo.json()["reader"], photo.json()["total"], len(photo.json()["lines"]), photo.json()["needs_review"]) == ("openai", 448.0, 2, False)
    url, kwargs = calls[-1]
    assert url == "https://example.test/v1/chat/completions" and kwargs["headers"]["Authorization"] == "Bearer gemini-key"
    sent = kwargs["json"]
    assert sent["model"] == "gemini-test" and sent["tool_choice"] == {"type": "function", "function": {"name": "record_invoice"}}
    content = sent["messages"][0]["content"]
    assert content[0]["type"] == "image_url" and content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,/9j/")
    assert content[1]["text"] == extract.READ_PROMPT
    # The schema goes as a function, with no lists of types in it: Gemini refuses those.
    parameters = sent["tools"][0]["function"]["parameters"]
    assert parameters["properties"]["supplier_name"]["type"] == "string"
    assert parameters["properties"]["lines"]["items"]["properties"]["line_total"]["type"] == "number"
    assert parameters["required"] == ["lines", "supplier_confidence", "date_confidence", "total_confidence"]
    assert "[" not in json.dumps(parameters).replace('["lines"', "").replace("[", "", 0) or all(
        not isinstance(v, list) for v in _types(parameters))

    pdf = client.post(f"{API}/invoices/read", json=_pdf_body("worldwide-produce-2026-10-21.pdf"))
    assert pdf.status_code == 200, pdf.text
    content = calls[-1][1]["json"]["messages"][0]["content"]
    assert len(content) == 1 and content[0]["type"] == "text"
    assert content[0]["text"].startswith(extract.READ_PROMPT) and "KALE GREEN 24 CT" in content[0]["text"]

    # INVOICE_READER_MODEL picks a different model for reading than for the assistant.
    monkeypatch.setattr(settings, "invoice_reader_model", "gemini-vision-test")
    client.post(f"{API}/invoices/read", json=_pdf_body("worldwide-produce-2026-10-21.pdf"))
    assert calls[-1][1]["json"]["model"] == "gemini-vision-test"


def _types(schema):
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "type":
                yield value
            else:
                yield from _types(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _types(value)


def test_the_openai_reader_copes_with_a_refusal_and_with_a_model_that_writes_json_instead(client, db, monkeypatch):
    import base64
    import json

    import httpx

    from app.prices import extract

    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_api_key", "k")
    monkeypatch.setattr(settings, "llm_model", "text-only-model")
    monkeypatch.setattr(settings, "invoice_reader_model", None)
    _restaurant(client, db)
    body = {"media_type": "image/png", "data_base64": base64.b64encode(b"\x89PNG").decode()}

    monkeypatch.setattr(extract, "_post", lambda url, **kw: httpx.Response(400, json={"error": {"message": "images not supported"}}))
    refused = client.post(f"{API}/invoices/read", json=body)
    assert refused.status_code == 422 and refused.json()["detail"].startswith("The reader (text-only-model) couldn't take that file (400).")

    monkeypatch.setattr(extract, "_post", lambda url, **kw: httpx.Response(200, json={"choices": [{"message": {
        "content": "Here you go:\n```json\n" + json.dumps(RECORDED) + "\n```"}}]}))
    written = client.post(f"{API}/invoices/read", json=body)
    assert written.status_code == 200 and written.json()["total"] == 448.0

    monkeypatch.setattr(extract, "_post", lambda url, **kw: httpx.Response(200, json={"choices": [{"message": {"content": "I can't read this."}}]}))
    nothing = client.post(f"{API}/invoices/read", json=body)
    assert nothing.status_code == 422 and "Couldn't make out any invoice lines" in nothing.json()["detail"]

    def down(url, **kw):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(extract, "_post", down)
    assert client.post(f"{API}/invoices/read", json=body).json()["detail"] == "Couldn't reach the reader just now. Try again, or type the lines."

    # With the openai key or model missing, PDFs still read by rule.
    monkeypatch.setattr(settings, "llm_model", "")
    assert extract.available() == "text"
