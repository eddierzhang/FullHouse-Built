"""The Anthropic path, which the provider refactor rewrote.

There is no API key in this environment, so the SDK client is stubbed.
What is under test is the adapter -- event emission, text extraction and
token accounting -- not the SDK itself.
"""

from types import SimpleNamespace

import anthropic
import pytest

from app.agents.providers import get_provider
from app.agents.providers.anthropic_provider import AnthropicProvider
from app.agents.providers.base import ProviderError


def _text(value):
    return SimpleNamespace(type="text", text=value)


def _tool_use(name, tool_input):
    return SimpleNamespace(type="tool_use", name=name, input=tool_input)


def _message(blocks, input_tokens=0, output_tokens=0):
    return SimpleNamespace(
        content=blocks,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


@pytest.fixture
def stub_client(monkeypatch):
    """Replaces anthropic.Anthropic with one returning scripted messages."""
    captured = {}

    def install(messages, error=None):
        def tool_runner(**kwargs):
            captured.update(kwargs)
            if error is not None:
                raise error
            return iter(messages)

        client = SimpleNamespace(
            beta=SimpleNamespace(messages=SimpleNamespace(tool_runner=tool_runner))
        )
        monkeypatch.setattr(
            "app.agents.providers.anthropic_provider.anthropic.Anthropic",
            lambda **kwargs: client,
        )
        return captured

    return install


def _events():
    seen = []
    return seen, lambda event_type, payload: seen.append((event_type, payload))


def test_final_text_is_returned_and_logged(stub_client):
    stub_client([_message([_text("Stock looks fine.")])])
    seen, emit = _events()

    result = AnthropicProvider(api_key="test").run_agent_loop(
        model="claude-opus-5", system="sys", tools=[], user_message="go", emit=emit
    )

    assert result.text == "Stock looks fine."
    assert ("log", {"text": "Stock looks fine."}) in seen


def test_tool_use_blocks_emit_tool_call_events(stub_client):
    stub_client(
        [
            _message([_tool_use("propose_reorder", {"quantity": 5})]),
            _message([_text("Proposed.")]),
        ]
    )
    seen, emit = _events()

    result = AnthropicProvider(api_key="test").run_agent_loop(
        model="claude-opus-5", system="sys", tools=[], user_message="go", emit=emit
    )

    assert ("tool_call", {"tool": "propose_reorder", "input": {"quantity": 5}}) in seen
    # Only the last message's text becomes the summary.
    assert result.text == "Proposed."


def test_tokens_are_summed_across_messages(stub_client):
    stub_client(
        [
            _message([_tool_use("x", {})], input_tokens=100, output_tokens=10),
            _message([_text("done")], input_tokens=200, output_tokens=20),
        ]
    )
    _, emit = _events()

    result = AnthropicProvider(api_key="test").run_agent_loop(
        model="claude-opus-5", system="sys", tools=[], user_message="go", emit=emit
    )

    assert result.tokens_used == 330


def test_request_carries_the_definition_model_and_system_prompt(stub_client):
    captured = stub_client([_message([_text("ok")])])
    _, emit = _events()

    AnthropicProvider(api_key="test").run_agent_loop(
        model="claude-opus-5", system="you are the boss", tools=[], user_message="run now", emit=emit
    )

    assert captured["model"] == "claude-opus-5"
    assert captured["system"] == "you are the boss"
    assert captured["messages"] == [{"role": "user", "content": "run now"}]


def test_sdk_errors_become_provider_errors(stub_client):
    stub_client([], error=anthropic.APIConnectionError(request=None))
    _, emit = _events()

    with pytest.raises(ProviderError, match="Anthropic request failed"):
        AnthropicProvider(api_key="test").run_agent_loop(
            model="claude-opus-5", system="s", tools=[], user_message="go", emit=emit
        )


def test_anthropic_honours_the_definitions_model(monkeypatch):
    monkeypatch.setattr("app.config.settings.llm_provider", "anthropic")

    provider = get_provider()

    assert provider.key == "anthropic"
    assert provider.resolve_model("claude-opus-5") == "claude-opus-5"


def test_ollama_overrides_the_definitions_model(monkeypatch):
    """Definitions are seeded with Claude ids, which Ollama cannot serve."""
    monkeypatch.setattr("app.config.settings.llm_provider", "ollama")
    monkeypatch.setattr("app.config.settings.ollama_model", "qwen2.5:7b")

    provider = get_provider()

    assert provider.key == "ollama"
    assert provider.resolve_model("claude-opus-5") == "qwen2.5:7b"


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setattr("app.config.settings.llm_provider", "gpt-at-home")

    with pytest.raises(ProviderError, match="Unknown LLM_PROVIDER"):
        get_provider()
