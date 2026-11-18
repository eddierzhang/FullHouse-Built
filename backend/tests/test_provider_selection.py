"""The model provider follows the keys; with none, the rule-based assistant runs, and LLM_PROVIDER=none turns it off."""

import pytest

from app.agents.providers import NO_MODEL, ProviderError, get_provider
from app.config import provider_from_env


def test_the_provider_follows_whichever_key_is_set():
    # No key: the rule-based assistant. The app needs no key to run.
    assert provider_from_env({}) == "scripted"
    assert provider_from_env({"ANTHROPIC_API_KEY": "sk-ant"}) == "anthropic"
    assert provider_from_env({"LLM_API_KEY": "gemini"}) == "openai"
    # Both: Anthropic, the provider the definitions are written for.
    assert provider_from_env({"ANTHROPIC_API_KEY": "sk-ant", "LLM_API_KEY": "gemini"}) == "anthropic"
    # A blank key is no key; an explicit LLM_PROVIDER wins over every key.
    assert provider_from_env({"ANTHROPIC_API_KEY": "  ", "LLM_API_KEY": ""}) == "scripted"
    assert provider_from_env({"LLM_PROVIDER": " Scripted ", "ANTHROPIC_API_KEY": "sk-ant"}) == "scripted"


def test_no_model_is_a_clear_message_not_a_crash(monkeypatch):
    monkeypatch.setattr("app.config.settings.llm_provider", "none")
    with pytest.raises(ProviderError, match="No model is connected") as caught:
        get_provider()
    assert str(caught.value) == NO_MODEL
    assert "LLM_API_KEY" in NO_MODEL and "ANTHROPIC_API_KEY" in NO_MODEL


def test_the_site_runs_with_no_model_and_the_runtime_says_why(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.llm_provider", "none")
    client.post("/api/v1/auth/signup", json={"name": "Owner", "email": "owner@example.com", "password": "a-long-password"})
    runtime = client.get("/api/v1/agents/runtime").json()
    assert runtime["provider"] == "unavailable" and runtime["ai_problem"] == NO_MODEL
    # The price product itself is untouched: status and digest answer as usual.
    assert client.get("/api/v1/prices/status").status_code == 200
    assert client.get("/api/v1/prices/status").json()["reader"] == "text"
    assert client.get("/health").status_code == 200


def test_a_question_in_the_thread_without_a_model_is_answered_with_why(client, db, monkeypatch):
    from test_prices import API, _seeded

    _seeded(client, db)
    monkeypatch.setattr("app.config.settings.llm_provider", "none")
    _yours, pending = client.post(f"{API}/thread", json={"text": "what did I pay for tomatoes last week"}).json()
    assert pending["status"] == "pending"
    from app.agents.runner import execute_run
    from app.prices.models import ThreadMessage

    # The runner records the failure on the run and re-raises for its caller (the executor logs it).
    with pytest.raises(ProviderError, match="No model is connected"):
        execute_run(db.get(ThreadMessage, pending["id"]).run_id, db)
    answer = next(m for m in client.get(f"{API}/thread").json() if m["id"] == pending["id"])
    assert answer["status"] == "failed" and "No model is connected" in answer["text"]
