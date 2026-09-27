"""Maestro's subagents run side by side, each in its own session."""

import threading

import pytest

from app.agents import crud, executor
from app.agents.models import AgentDefinition, AgentRun
from app.agents.providers.base import LLMProvider, ProviderResult, RunCancelledError, run_tool_calls
from app.agents.runner import execute_run


@pytest.fixture
def boss(threaded_db):
    for key in ("inventory", "profit"):
        threaded_db.add(
            AgentDefinition(key=key, name=key.title(), description=key, role="subagent", system_prompt=key)
        )
    defn = AgentDefinition(
        key="boss", name="Boss", description="b", role="boss", system_prompt="boss",
        tool_allowlist=["inventory", "profit"],
    )
    threaded_db.add(defn)
    threaded_db.commit()
    return defn


class FakeProvider(LLMProvider):
    """The Boss asks for both specialists in one turn; each specialist
    blocks until the other has started, so a sequential run would hang."""

    key = "fake"

    def __init__(self, both_started: threading.Barrier):
        self.both_started = both_started

    def resolve_model(self, configured_model):
        return "fake"

    def run_agent_loop(self, *, model, system, tools, user_message, emit, should_cancel=None):
        if system == "boss":
            tools_by_name = {tool.name: tool for tool in tools}
            requested = [
                ("delegate_to_inventory", {"task": "check stock"}),
                ("delegate_to_profit", {"task": "check margin"}),
            ]
            results = [
                result for _, result in run_tool_calls(requested, tools_by_name, emit, max_parallel=4)
            ]
            return ProviderResult(text=" | ".join(results))

        self.both_started.wait()
        if should_cancel and should_cancel():
            raise RunCancelledError("stopped")
        return ProviderResult(text=f"{system} report for: {user_message}")


def test_delegations_from_one_turn_run_concurrently(threaded_db, boss, monkeypatch):
    provider = FakeProvider(threading.Barrier(2, timeout=5))
    monkeypatch.setattr("app.agents.runner.get_provider", lambda: provider)
    run = crud.create_run(threaded_db, boss.id, input="daily review")

    summary = execute_run(run.id, db=threaded_db)

    assert summary == "inventory report for: check stock | profit report for: check margin"
    threaded_db.expire_all()
    children = threaded_db.query(AgentRun).filter(AgentRun.parent_run_id == run.id).all()
    assert sorted((c.input, c.status, c.depth, c.trigger_type) for c in children) == [
        ("check margin", "succeeded", 1, "delegated"),
        ("check stock", "succeeded", 1, "delegated"),
    ]

    events = crud.list_events(threaded_db, run.id)
    seqs = [e.seq for e in events]
    assert seqs == list(range(1, len(seqs) + 1))  # no collisions from the concurrent writers
    assert sorted(e.payload["agent_key"] for e in events if e.type == "delegation") == ["inventory", "profit"]


def test_cancelling_the_boss_stops_its_subagents(threaded_db, boss, monkeypatch):
    provider = FakeProvider(threading.Barrier(2, timeout=5))
    monkeypatch.setattr("app.agents.runner.get_provider", lambda: provider)
    run = crud.create_run(threaded_db, boss.id, input="daily review")
    executor.request_cancel(run.id)
    try:
        execute_run(run.id, db=threaded_db)
    finally:
        executor.clear_cancel(run.id)

    threaded_db.expire_all()
    children = threaded_db.query(AgentRun).filter(AgentRun.parent_run_id == run.id).all()
    assert [c.status for c in children] == ["cancelled", "cancelled"]
