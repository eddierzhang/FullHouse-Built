"""Customer service requests: staff write them down, owners read them in their
original words, and the Assistant Manager summarises them into action items."""

import json

from fastapi.testclient import TestClient

from app.agents import crud as agent_crud
from app.agents.definitions import AGENT_DEF, AGENT_KEY
from app.agents.models import AgentDefinition, AgentRun
from app.agents.providers.scripted_provider import ScriptedProvider
from app.agents.registry import resolve_tools
from app.agents.runner import execute_run
from app.audit.models import ChangeLog
from app.customers.models import CustomerActionItem, CustomerRequest, CustomerServiceReview
from app.customers.routes import REVIEW_TASK
from conftest import TEST_USER_EMAIL
from test_restaurant_details import _member, _second_restaurant
from test_restaurant_setup import _set_up

API = "/api/v1/customer-requests"
COLD_SOUP = {
    "message": "  The soup was cold.\n\nAnd   the waiter shrugged when I said so!  ", "channel": "phone",
    "kind": "complaint", "customer_name": " Rosa Diaz ", "customer_contact": "rosa@example.com",
}


def _log(client, **fields):
    response = client.post(API, json={"message": "Do you have high chairs?", **fields})
    assert response.status_code == 201, response.text
    return response.json()


def test_a_request_is_kept_exactly_as_it_was_taken_down(client):
    logged = client.post(API, json=COLD_SOUP).json()

    (row,) = client.get(API).json()
    assert row["id"] == logged["id"]
    # Trimmed at the ends; every word, space and line break between is the customer's.
    assert row["message"] == "The soup was cold.\n\nAnd   the waiter shrugged when I said so!"
    assert (row["customer_name"], row["customer_contact"]) == ("Rosa Diaz", "rosa@example.com")
    assert (row["channel"], row["kind"], row["status"], row["logged_by"]) == ("phone", "complaint", "open", TEST_USER_EMAIL)
    assert client.get(f"{API}/{row['id']}").json() == row


def test_bad_requests_are_refused(client):
    assert client.post(API, json={"message": "   "}).status_code == 422
    assert client.post(API, json={"message": "Hi", "kind": "rant"}).status_code == 422
    assert client.post(API, json={"message": "Hi", "channel": "pigeon"}).status_code == 422
    assert client.post(API, json={"message": "Hi", "received_at": "2999-01-01T00:00:00Z"}).status_code == 422


def test_resolving_adds_a_note_beside_the_original_and_nothing_can_rewrite_it(client):
    row = client.post(API, json=COLD_SOUP).json()
    original = client.get(f"{API}/{row['id']}").json()["message"]

    resolved = client.post(f"{API}/{row['id']}/resolve", json={"note": " Refunded the soup and apologised. "}).json()
    assert (resolved["status"], resolved["resolution_note"], resolved["resolved_by"]) == (
        "resolved", "Refunded the soup and apologised.", TEST_USER_EMAIL)
    assert resolved["message"] == original
    assert [r["id"] for r in client.get(API, params={"status": "open"}).json()] == []
    assert [r["id"] for r in client.get(API, params={"status": "resolved"}).json()] == [row["id"]]

    reopened = client.post(f"{API}/{row['id']}/reopen").json()
    assert (reopened["status"], reopened["resolved_at"], reopened["message"]) == ("open", None, original)

    # No route edits or deletes a request.
    for method in ("put", "patch", "delete"):
        assert getattr(client, method)(f"{API}/{row['id']}").status_code == 405


def test_staff_write_requests_down_but_only_owners_and_admins_read_them(client, db):
    _set_up(client, db)
    member = _member(client, db)

    logged = member.post(API, json=COLD_SOUP)
    assert logged.status_code == 201
    assert set(logged.json()) == {"id", "received_at"}  # saved, without handing back anyone's details
    request_id = logged.json()["id"]

    for response in (
        member.get(API), member.get(f"{API}/{request_id}"), member.get(f"{API}/reviews"),
        member.post(f"{API}/reviews"), member.post(f"{API}/{request_id}/resolve", json={}),
        member.post(f"{API}/{request_id}/reopen"), member.patch(f"{API}/action-items/x", json={"done": True}),
    ):
        assert response.status_code == 403
    assert client.get(f"{API}/{request_id}").json()["logged_by"] == "mo@example.com"


def test_customers_words_stay_out_of_the_history_the_whole_team_can_read(client, db):
    row = _log(client, customer_name="Rosa Diaz")
    client.post(f"{API}/{row['id']}/resolve", json={"note": "Sorted."})
    assert db.query(ChangeLog).count() == 0


def test_another_restaurant_never_sees_or_touches_these(client, db):
    _set_up(client, db)
    mine = _log(client)
    _, other = _second_restaurant(db)

    assert other.get(API).json() == []
    assert other.get(f"{API}/{mine['id']}").status_code == 404
    assert other.post(f"{API}/{mine['id']}/resolve", json={}).status_code == 404
    assert other.post(f"{API}/reviews").status_code == 409  # nothing of its own to summarise
    assert client.get(API).json()[0]["status"] == "open"


# --- the Assistant Manager ----------------------------------------------------------


def _agent(db) -> AgentDefinition:
    defn = AgentDefinition(role="main", model="m", enabled=True, **AGENT_DEF)
    db.add(defn)
    db.commit()
    return defn


def _tools(db, restaurant_id=None):
    defn = db.query(AgentDefinition).filter_by(key=AGENT_KEY).first() or _agent(db)
    run = agent_crud.create_run(db, defn.id, input="review", restaurant_id=restaurant_id)
    return run, {t.name: t for t in resolve_tools(db, run, defn) if t.area == "customers"}


def test_the_agent_reads_what_was_said_but_not_who_said_it(client, db):
    client.post(API, json=COLD_SOUP)
    resolved = _log(client)
    client.post(f"{API}/{resolved['id']}/resolve", json={})
    _, tools = _tools(db)

    (seen,) = json.loads(tools["list_customer_requests"].call({}))  # open ones, by default
    assert seen["message"] == "The soup was cold.\n\nAnd   the waiter shrugged when I said so!"
    assert (seen["kind"], seen["channel"]) == ("complaint", "phone")
    assert "Rosa" not in json.dumps(seen) and "rosa@example.com" not in json.dumps(seen)
    assert len(json.loads(tools["list_customer_requests"].call({"status": "all"}))) == 2


def test_the_agents_summary_and_action_items_point_back_at_the_requests(client, db):
    soup = client.post(API, json=COLD_SOUP).json()["id"]
    chairs = _log(client)["id"]
    run, tools = _tools(db)

    assert "Action item added" in tools["add_customer_action_item"].call({
        "title": "Call back about the cold soup", "details": "Apologise and offer a refund.",
        "request_ids": [soup], "priority": "high",
    })
    assert "covering 2" in tools["save_customer_service_summary"].call({
        "summary": "One complaint about cold soup; one question about high chairs.", "request_ids": [soup, chairs],
    })
    # Nothing can be pinned on a request that isn't this restaurant's.
    assert tools["add_customer_action_item"].call(
        {"title": "x", "details": "y", "request_ids": ["nope"]}).startswith("Error")
    assert tools["add_customer_action_item"].call({"title": "x", "details": "y", "request_ids": []}).startswith("Error")
    assert tools["save_customer_service_summary"].call({"summary": "s", "request_ids": ["nope"]}).startswith("Error")

    (review,) = client.get(f"{API}/reviews").json()
    assert review["run_id"] == run.id
    assert review["summary"] == "One complaint about cold soup; one question about high chairs."
    assert review["request_ids"] == [soup, chairs]
    (item,) = review["action_items"]
    assert (item["title"], item["priority"], item["request_ids"], item["status"]) == (
        "Call back about the cold soup", "high", [soup], "open")
    # The requests themselves are untouched by being summarised.
    assert {r["status"] for r in client.get(API).json()} == {"open"}

    done = client.patch(f"{API}/action-items/{item['id']}", json={"done": True}).json()
    assert (done["status"], done["done_by"]) == ("done", TEST_USER_EMAIL)
    undone = client.patch(f"{API}/action-items/{item['id']}", json={"done": False}).json()
    assert (undone["status"], undone["done_at"], undone["done_by"]) == ("open", None, None)


def test_asking_for_a_summary_runs_the_agent_end_to_end(client, db, monkeypatch):
    _agent(db)
    assert client.post(f"{API}/reviews").status_code == 409  # nothing to summarise yet
    soup = client.post(API, json=COLD_SOUP).json()["id"]
    _log(client)
    booking = _log(client, kind="request", message="Table for 12 on Saturday, please call me back.")["id"]

    submitted = []
    monkeypatch.setattr("app.agents.executor.submit", submitted.append)
    started = client.post(f"{API}/reviews")
    assert started.status_code == 202
    run_id = started.json()["id"]
    assert submitted == [run_id] and db.get(AgentRun, run_id).input == REVIEW_TASK
    assert client.post(f"{API}/reviews").status_code == 409  # one at a time

    monkeypatch.setattr("app.agents.runner.get_provider", lambda: ScriptedProvider(step_delay=0))
    execute_run(run_id, db)
    run = db.get(AgentRun, run_id)
    assert run.status == "succeeded", run.error
    assert "3 open customer requests" in run.output_summary

    (review,) = client.get(f"{API}/reviews").json()
    assert review["run_id"] == run_id
    assert review["summary"] == "3 open customer requests: 1 complaint, 1 request, 1 other."
    assert set(review["request_ids"]) == {r["id"] for r in client.get(API).json()}
    by_request = {item["request_ids"][0]: item for item in review["action_items"]}
    assert set(by_request) == {soup, booking}  # the complaint and the request; a question needs no chasing
    assert by_request[soup]["priority"] == "high"
    assert "The soup was cold." in by_request[soup]["details"]


def test_a_paused_agent_writes_no_summaries(client, db):
    _set_up(client, db)
    _log(client)
    client.patch(f"/api/v1/agents/definitions/{AGENT_KEY}", json={"enabled": False})
    refused = client.post(f"{API}/reviews")
    assert refused.status_code == 409 and "switched off" in refused.json()["detail"]


def test_the_export_and_the_demo_include_customer_requests(client, db):
    from app.demo import CUSTOMER_REQUESTS, reset_demo_data
    from app.support.routes import export_files

    _log(client, customer_name="Rosa Diaz")
    files = export_files(db, None)
    assert "Rosa Diaz" in files["customer_requests.csv"]
    assert {"customer_service_summaries.csv", "customer_action_items.csv"} <= set(files)

    reset_demo_data(db)
    assert db.query(CustomerRequest).count() == len(CUSTOMER_REQUESTS)
    assert db.query(CustomerServiceReview).count() == db.query(CustomerActionItem).count() == 0
