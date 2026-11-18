"""The v2 platform: flags per restaurant, the event log, the job queue and its worker, the webhook
inbox, and agent runs that the web process queues and a worker executes."""

from datetime import datetime, timedelta, timezone

import pytest

from app.agents import crud as agent_crud
from app.agents.definitions import AGENT_KEY
from app.agents.models import AgentDefinition, AgentRun
from app.config import settings
from app.platform import events, flags, jobs
from app.platform.models import DONE, FAILED, QUEUED, Event, Job, WebhookInbox
from app.prices.models import Invoice
from test_prices import _restaurant, _suppliers
from test_restaurant_setup import _set_up

FLAGS = "/api/v1/platform/flags"


# --- flags ------------------------------------------------------------------------------------


def test_flags_are_off_until_the_owner_turns_one_on_and_unknown_flags_are_refused(client, db):
    restaurant = _set_up(client, db)
    assert client.get(FLAGS).json()["flags"] == {f: False for f in flags.FLAGS}
    on = client.put(FLAGS, json={"flag": flags.V2_CONNECT, "enabled": True})
    assert on.status_code == 200 and on.json()["flags"][flags.V2_CONNECT] is True
    assert flags.enabled(db, restaurant["id"], flags.V2_CONNECT) is True
    assert client.put(FLAGS, json={"flag": "v9_teleport", "enabled": True}).status_code == 422
    # What the CLI does, by the owner's email.
    assert flags._restaurant_id(db, "eddie@example.com") == restaurant["id"]
    flags.set_flag(db, restaurant["id"], flags.V2_CONNECT, False)
    db.commit()
    assert client.get(FLAGS).json()["flags"][flags.V2_CONNECT] is False


def test_only_the_owner_switches_flags(client, db):
    from test_restaurant_details import _member

    _set_up(client, db)
    member = _member(client, db)
    assert member.put(FLAGS, json={"flag": flags.V2_CONNECT, "enabled": True}).status_code == 403
    assert member.get(FLAGS).status_code == 200


# --- events -----------------------------------------------------------------------------------


def test_emit_appends_and_calls_subscribers_in_order_and_a_broken_one_does_not_undo_the_write(db):
    seen = []

    @events.subscribe("test.ping")
    def first(db_, event):
        seen.append(("first", event.payload["n"]))

    @events.subscribe("test.ping")
    def broken(db_, event):
        raise RuntimeError("boom")

    event = events.emit(db, None, "test.ping", "things", "1", {"n": 1})
    db.commit()
    assert seen == [("first", 1)]
    assert db.query(Event).filter_by(type="test.ping").one().seq == event.seq


# --- the queue --------------------------------------------------------------------------------


def test_the_same_key_collapses_while_a_job_is_open_and_runs_again_once_it_is_done(db):
    ran = []

    @jobs.handler("digest")
    def fake_digest(db_, payload):
        ran.append(payload["n"])

    a = jobs.enqueue(db, "digest", "digest:r1:2026-W44", {"n": 1})
    b = jobs.enqueue(db, "digest", "digest:r1:2026-W44", {"n": 2})
    db.commit()
    assert a.id == b.id and db.query(Job).count() == 1
    assert jobs.drain(db) == 1 and ran == [1]
    assert db.get(Job, a.id).status == DONE
    # Done: a new request under the same key is a new job.
    c = jobs.enqueue(db, "digest", "digest:r1:2026-W44", {"n": 3})
    db.commit()
    assert c.id != a.id and jobs.drain(db) == 1 and ran == [1, 3]
    import app.platform.handlers  # noqa: F401  restore the real handler for later tests
    from app.platform.handlers import build_digest

    jobs.handler("digest")(build_digest)


def test_a_failing_job_retries_with_backoff_then_stays_failed_with_its_error(db, monkeypatch):
    calls = []

    @jobs.handler("forecast")
    def flaky(db_, payload):
        calls.append(1)
        raise ValueError("no sales yet")

    job = jobs.enqueue(db, "forecast", "forecast:r1", {}, max_attempts=3)
    db.commit()
    assert jobs.run_once(db) is True
    db.expire_all()
    job = db.get(Job, job.id)
    assert job.status == QUEUED and job.attempts == 1 and "no sales yet" in job.error
    assert job.run_after > datetime.now(timezone.utc) + timedelta(seconds=1)  # backing off
    assert jobs.run_once(db) is False  # not due yet
    # Bring it forward twice more: the third failure is final.
    for expected in (2, 3):
        job.run_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        jobs.run_once(db)
        db.expire_all()
        job = db.get(Job, job.id)
        assert job.attempts == expected
    assert job.status == FAILED and len(calls) == 3
    assert any(q["failed"] == 1 for q in jobs.queue_stats(db) if q["queue"] == "forecast")


def test_a_lease_left_by_a_dead_worker_is_recovered_and_the_job_runs_once_more(db):
    done = []

    @jobs.handler("detect")
    def detect(db_, payload):
        done.append(payload)

    job = jobs.enqueue(db, "detect", "detect:r1", {"k": 1})
    db.commit()
    # A worker claimed it and died: active, lease in the past, nothing done.
    claimed = jobs.claim(db, "worker-that-died", lease_seconds=1)
    assert claimed.id == job.id
    claimed.leased_until = datetime.now(timezone.utc) - timedelta(seconds=5)
    db.commit()
    assert jobs.run_once(db) is False or done  # the recovery step re-queues it, then it may run in this call
    if not done:
        assert jobs.run_once(db) is True
    db.expire_all()
    assert done == [{"k": 1}] and db.get(Job, job.id).status == DONE and db.get(Job, job.id).attempts == 2


def test_a_run_queued_by_the_api_is_executed_by_a_worker_once(client, db):
    _set_up(client, db)
    agent = db.query(AgentDefinition).filter_by(key=AGENT_KEY).one()
    queued = client.post("/api/v1/agents/runs", json={"agent_key": agent.key, "input": "Check stock"})
    assert queued.status_code == 202, queued.text
    run_id = queued.json()["id"]
    (job,) = db.query(Job).filter(Job.queue == "agent").all()
    assert job.singleton_key == f"agent:{run_id}" and job.status == QUEUED
    assert db.get(AgentRun, run_id).status == "queued"

    # The worker runs it (the scripted provider needs no key). A second drain finds nothing.
    assert jobs.drain(db) == 1
    db.expire_all()
    assert db.get(AgentRun, run_id).status == "succeeded"
    assert db.get(Job, job.id).status == DONE
    assert db.get(AgentRun, run_id).cost_usd == 0.0
    assert jobs.drain(db) == 0
    # A run a dead worker left mid-flight is not started over: that would double its proposals.
    again = agent_crud.create_run(db, agent.id, input="again", restaurant_id=db.get(AgentRun, run_id).restaurant_id)
    agent_crud.set_run_status(db, again, "running")
    db.commit()
    jobs.enqueue(db, "agent", f"agent:{again.id}", {"run_id": again.id})
    db.commit()
    assert jobs.drain(db) == 1
    db.expire_all()
    assert db.get(AgentRun, again.id).status == "running"


def test_at_most_two_agent_runs_per_restaurant_are_in_flight(db, client):
    restaurant = _set_up(client, db)
    agent = db.query(AgentDefinition).filter_by(key=AGENT_KEY).one()
    ids = []
    for i in range(3):
        run = agent_crud.create_run(db, agent.id, input=f"run {i}", restaurant_id=restaurant["id"])
        jobs.enqueue(db, "agent", f"agent:{run.id}", {"run_id": run.id}, restaurant_id=restaurant["id"])
        ids.append(run.id)
    db.commit()
    first = jobs.claim(db, "w1")
    second = jobs.claim(db, "w2")
    assert first is not None and second is not None
    assert jobs.claim(db, "w3") is None  # the third waits for a slot
    first.status = DONE
    first.status_group = first.id
    db.commit()
    assert jobs.claim(db, "w3") is not None


# --- the webhook inbox ------------------------------------------------------------------------


def test_the_same_billcom_webhook_twice_yields_one_pull_and_one_invoice(client, db, monkeypatch):
    from app.prices import billcom
    from test_prices import BILLS, _billcom

    restaurant = _restaurant(client, db)
    _suppliers(db, restaurant)
    db.commit()
    bc, _seen = _billcom(monkeypatch, BILLS)
    monkeypatch.setattr(settings, "billcom_webhook_secret", "hook")
    monkeypatch.setattr(billcom, "BillcomClient", lambda *args, **kwargs: bc)

    body = {"eventId": "evt-1", "type": "bill.updated"}
    first = client.post("/api/v1/prices/billcom/webhook", json=body, headers={"X-Billcom-Secret": "hook"})
    second = client.post("/api/v1/prices/billcom/webhook", json=body, headers={"X-Billcom-Secret": "hook"})
    assert first.json()["queued"] is True, first.text
    assert second.json().get("duplicate") is True
    assert db.query(WebhookInbox).count() == 1
    assert db.query(Job).filter(Job.queue == "sync").count() == 1
    # A different event while the sync is still queued collapses into the same job.
    client.post("/api/v1/prices/billcom/webhook", json={"eventId": "evt-2"}, headers={"X-Billcom-Secret": "hook"})
    assert db.query(WebhookInbox).count() == 2 and db.query(Job).filter(Job.queue == "sync").count() == 1

    assert db.query(Invoice).count() == 0
    assert jobs.drain(db) == 1
    db.expire_all()
    assert db.query(Invoice).count() == 2  # bill-3 has no lines
    assert db.query(Event).filter_by(type="connector.synced").count() == 1
    assert db.get(Job, db.query(Job).filter(Job.queue == "sync").one().id).status == DONE
    # And once more does nothing new: bills are kept once by their Bill.com id.
    jobs.enqueue(db, "sync", f"sync:billcom:{restaurant.id}", {"provider": "billcom", "restaurant_id": restaurant.id})
    db.commit()
    assert jobs.drain(db) == 1
    assert db.query(Invoice).count() == 2


# --- storage ----------------------------------------------------------------------------------


def test_files_are_stored_under_the_restaurant_and_read_through_a_signed_link_that_expires(client, db, tmp_path, monkeypatch):
    from app import storage

    restaurant = _set_up(client, db)
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    key = storage.new_key(restaurant["id"], "invoice", "bill.pdf")
    assert key.startswith(f"{restaurant['id']}/invoice/") and key.endswith(".pdf")
    storage.put(key, b"%PDF-1.4 test", "application/pdf")
    assert storage.get(key) == b"%PDF-1.4 test"
    url = storage.signed_url(key)
    good = client.get(url)
    assert good.status_code == 200 and good.content == b"%PDF-1.4 test" and good.headers["content-type"] == "application/pdf"
    expires, sig = storage.sign(key, seconds=-1)
    assert client.get(f"/api/v1/platform/files/{key}?expires={expires}&sig={sig}").status_code == 410
    assert client.get(f"/api/v1/platform/files/{key}?expires={expires + 9999}&sig={sig}").status_code == 410
    with pytest.raises(ValueError):
        storage.put("../../etc/passwd", b"x")
