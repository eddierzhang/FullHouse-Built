"""The model-free provider that browser tests and CI run agents with."""

from anthropic import beta_tool

from app.agents.providers import get_provider
from app.agents.providers.scripted_provider import ScriptedProvider


def _events():
    seen = []
    return seen, lambda event_type, payload: seen.append((event_type, payload))


def test_it_is_selectable_by_name(monkeypatch):
    monkeypatch.setattr("app.config.settings.llm_provider", "scripted")
    assert get_provider().key == "scripted"


def test_it_really_runs_the_first_argument_free_tool():
    calls = []

    @beta_tool
    def needs_args(item_id: str) -> str:
        """Needs an argument."""
        calls.append("needs_args")
        return "no"

    @beta_tool
    def list_things() -> str:
        """Lists things."""
        calls.append("list_things")
        return "[1, 2, 3]"

    seen, emit = _events()
    result = ScriptedProvider().run_agent_loop(
        model="scripted", system="s", tools=[needs_args, list_things], user_message="go", emit=emit
    )

    assert calls == ["list_things"]
    assert [kind for kind, _ in seen] == ["log", "tool_call", "tool_result"]
    assert "[1, 2, 3]" in result.text


def test_an_agent_with_no_usable_tool_still_finishes():
    seen, emit = _events()

    result = ScriptedProvider().run_agent_loop(
        model="scripted", system="s", tools=[], user_message="go", emit=emit
    )

    assert "no argument-free tool" in result.text


def test_a_full_run_through_the_runner_streams_and_succeeds(db, monkeypatch):
    """The whole pipeline, minus the model: tools, persistence, status."""
    from app.agents import crud
    from app.agents.models import AgentDefinition
    from app.agents.runner import execute_run
    from app.restaurant.models import InventoryItem

    monkeypatch.setattr("app.config.settings.llm_provider", "scripted")
    defn = AgentDefinition(key="inventory", name="Inventory", description="d", role="subagent", system_prompt="s")
    db.add_all([defn, InventoryItem(name="Tomatoes", unit="lb", quantity_on_hand=2, reorder_threshold=10)])
    db.commit()
    run = crud.create_run(db, defn.id, input="check stock")

    execute_run(run.id, db=db)

    db.refresh(run)
    assert run.status == "succeeded"
    kinds = [e.type for e in crud.list_events(db, run.id)]
    assert "tool_call" in kinds and "tool_result" in kinds
    assert "Tomatoes" in run.output_summary
