"""Background execution, live streaming and cancellation."""

import json
import threading
import time

import pytest

from app.agents import executor
from app.agents.broker import TERMINAL, EventBroker
from app.agents.models import AgentDefinition, AgentRun


@pytest.fixture
def definition(db):
    defn = AgentDefinition(
        key="inventory", name="Inventory", description="d", role="subagent", system_prompt="s"
    )
    db.add(defn)
    db.commit()
    return defn


@pytest.fixture(autouse=True)
def no_real_runs(monkeypatch):
    """Keep `submit` from actually driving a model during these tests."""
    submitted: list[str] = []
    monkeypatch.setattr(executor, "submit", submitted.append)
    monkeypatch.setattr("app.agents.routes.executor.submit", submitted.append)
    return submitted


def test_triggering_a_run_returns_immediately_as_queued(client, definition, no_real_runs):
    response = client.post("/api/v1/agents/runs", json={"agent_key": "inventory", "input": "go"})

    assert response.status_code == 202  # accepted, not completed
    body = response.json()
    assert body["status"] == "queued"
    assert body["output_summary"] is None
    assert no_real_runs == [body["id"]]  # handed to the worker pool


def test_an_unknown_agent_is_still_rejected_up_front(client, definition):
    assert client.post("/api/v1/agents/runs", json={"agent_key": "ghost"}).status_code == 404


def test_a_finished_run_streams_its_history_then_ends(client, db, definition):
    """A listener arriving after the fact still sees the whole run."""
    from app.agents import crud

    run = crud.create_run(db, definition.id, input="go")
    crud.add_event(db, run.id, "log", {"text": "first"})
    crud.add_event(db, run.id, "tool_call", {"tool": "list_low_stock_items"})
    crud.set_run_status(db, run, "succeeded")

    with client.stream("GET", f"/api/v1/agents/runs/{run.id}/stream") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [
            json.loads(line[len("data: "):])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    assert [e["type"] for e in events] == ["log", "tool_call", TERMINAL]
    assert events[0]["payload"]["text"] == "first"


def test_streaming_an_unknown_run_is_404(client):
    assert client.get("/api/v1/agents/runs/ghost/stream").status_code == 404


def test_cancel_marks_the_run_for_the_worker_to_notice(client, db, definition):
    from app.agents import crud

    run = crud.create_run(db, definition.id)
    crud.set_run_status(db, run, "running")

    response = client.post(f"/api/v1/agents/runs/{run.id}/cancel")

    assert response.status_code == 200
    assert executor.is_cancelled(run.id)
    executor.clear_cancel(run.id)


def test_a_finished_run_cannot_be_cancelled(client, db, definition):
    from app.agents import crud

    run = crud.create_run(db, definition.id)
    crud.set_run_status(db, run, "succeeded")

    response = client.post(f"/api/v1/agents/runs/{run.id}/cancel")

    assert response.status_code == 400
    assert "already succeeded" in response.json()["detail"]


def test_a_cancelled_run_stops_and_is_recorded_as_cancelled(db, definition, monkeypatch):
    """End to end through the runner, with a provider that would loop forever."""
    from app.agents import crud
    from app.agents.providers.base import LLMProvider, ProviderResult, RunCancelledError
    from app.agents.runner import execute_run

    run = crud.create_run(db, definition.id)

    class Endless(LLMProvider):
        key = "endless"

        def resolve_model(self, configured_model):
            return "endless"

        def run_agent_loop(self, *, model, system, tools, user_message, emit, should_cancel=None):
            for _ in range(1000):
                if should_cancel and should_cancel():
                    raise RunCancelledError("stopped")
                emit("log", {"text": "still going"})
            return ProviderResult(text="never gets here")

    monkeypatch.setattr("app.agents.runner.get_provider", lambda: Endless())
    executor.request_cancel(run.id)
    try:
        execute_run(run.id, db=db)
    finally:
        executor.clear_cancel(run.id)

    db.refresh(run)
    assert run.status == "cancelled"
    assert run.finished_at is not None


def test_broker_delivers_to_every_listener_and_drops_them_cleanly():
    broker = EventBroker()
    first = broker.subscribe("run-1")
    second = broker.subscribe("run-1")
    other = broker.subscribe("run-2")

    broker.publish("run-1", {"type": "log"})

    assert first.get_nowait() == {"type": "log"}
    assert second.get_nowait() == {"type": "log"}
    assert other.empty()

    broker.unsubscribe("run-1", first)
    broker.unsubscribe("run-1", second)
    assert broker.listener_count("run-1") == 0


def test_publishing_with_no_listeners_is_harmless():
    EventBroker().publish("nobody-listening", {"type": "log"})


def test_publishing_from_a_worker_thread_reaches_the_subscriber():
    """The thread-to-loop hop is the whole point of the broker."""
    broker = EventBroker()
    queue = broker.subscribe("run-1")

    done = threading.Event()

    def worker():
        broker.publish("run-1", {"type": "log", "payload": {"text": "from a thread"}})
        done.set()

    threading.Thread(target=worker).start()
    assert done.wait(timeout=2)
    # No loop bound, so delivery is inline -- still exactly once.
    assert queue.get_nowait()["payload"]["text"] == "from a thread"


def test_orphaned_runs_are_failed_at_startup(db, definition):
    """A run cannot survive the process that was executing it."""
    from app.main import _recover_orphaned_runs

    stuck = AgentRun(agent_definition_id=definition.id, status="running")
    queued = AgentRun(agent_definition_id=definition.id, status="queued")
    done = AgentRun(agent_definition_id=definition.id, status="succeeded")
    db.add_all([stuck, queued, done])
    db.commit()

    recovered = _recover_orphaned_runs(db)

    assert recovered == 2
    db.expire_all()
    assert db.get(AgentRun, stuck.id).status == "failed"
    assert "restarted" in db.get(AgentRun, stuck.id).error
    assert db.get(AgentRun, queued.id).status == "failed"
    assert db.get(AgentRun, done.id).status == "succeeded"  # untouched


def test_startup_recovery_is_a_no_op_when_nothing_is_stuck(db, definition):
    from app.main import _recover_orphaned_runs

    assert _recover_orphaned_runs(db) == 0


def test_slow_listener_does_not_block_the_publisher():
    broker = EventBroker()
    queue = broker.subscribe("run-1")

    start = time.monotonic()
    for i in range(500):
        broker.publish("run-1", {"type": "log", "seq": i})
    elapsed = time.monotonic() - start

    assert elapsed < 1.0  # publishing never waits on a consumer
    assert queue.qsize() == 500
