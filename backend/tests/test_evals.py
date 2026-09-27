"""The eval harness scores behaviour correctly -- checked without a model.

The scripted provider makes no decisions, so it is a known baseline: it
should pass the negative scenarios and fail every one that needs a proposal.
"""

import pytest

from evals.harness import run_scenario
from evals.run import render_markdown
from evals.scenarios import SCENARIOS

BY_ID = {s.id: s for s in SCENARIOS}


@pytest.fixture(autouse=True)
def restore_provider_settings(monkeypatch):
    # The harness points global settings at each target; keep that from leaking.
    monkeypatch.setattr("app.config.settings.llm_provider", "scripted")
    monkeypatch.setattr("app.config.settings.ollama_model", "qwen2.5:7b")


def _checks(result):
    return {c.name: c.passed for c in result.checks}


def test_every_scenario_builds_and_runs():
    for scenario in SCENARIOS:
        result = run_scenario(scenario, "scripted")
        assert result.status == "succeeded", (scenario.id, result.error)


def test_a_do_nothing_agent_passes_a_negative_scenario():
    result = run_scenario(BY_ID["inventory-nothing-low"], "scripted")

    assert _checks(result)["proposed nothing"] is True
    assert result.score == 1.0


def test_a_do_nothing_agent_fails_when_action_was_needed():
    result = run_scenario(BY_ID["inventory-shortage"], "scripted")
    checks = _checks(result)

    assert checks["used expected tools"] is True  # it did look
    assert checks["made the right proposal"] is False  # but did nothing about it
    assert checks["proposal applies cleanly"] is False
    assert result.score < 1.0


def test_a_correct_proposal_is_dry_run_without_being_committed(monkeypatch):
    """Scores 'applies cleanly' by applying and rolling back, not by trusting the payload."""
    from anthropic import beta_tool  # noqa: F401

    from app.agents.providers.base import LLMProvider, ProviderResult

    class GoodInventoryAgent(LLMProvider):
        key = "good"

        def resolve_model(self, configured_model):
            return "good"

        def run_agent_loop(self, *, model, system, tools, user_message, emit, should_cancel=None):
            import json

            by_name = {t.name: t for t in tools}
            emit("tool_call", {"tool": "list_low_stock_items", "input": {}})
            low = json.loads(by_name["list_low_stock_items"].call({}))
            for item in low:
                emit("tool_call", {"tool": "propose_reorder", "input": {}})
                by_name["propose_reorder"].call({"inventory_item_id": item["id"], "quantity": item["reorder_qty"]})
            return ProviderResult(text="Reordered what was low.")

    monkeypatch.setattr("app.agents.runner.get_provider", lambda: GoodInventoryAgent())

    result = run_scenario(BY_ID["inventory-shortage"], "good")

    assert all(_checks(result).values()), result.checks
    assert result.score == 1.0


def test_report_lists_scores_and_failures():
    results = [
        run_scenario(BY_ID["inventory-shortage"], "scripted").to_dict(),
        run_scenario(BY_ID["inventory-nothing-low"], "scripted").to_dict(),
    ]

    report = render_markdown(results, ["`anthropic:x` (no ANTHROPIC_API_KEY)"], "now")

    assert "| inventory-shortage |" in report
    assert "**Overall**" in report
    assert "made the right proposal" in report
    assert "Skipped:" in report
