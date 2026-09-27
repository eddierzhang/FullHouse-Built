"""The OpenAI-compatible loop (Gemini, Groq, OpenRouter...), over real HTTP.

A stub server for the same reason as the Ollama tests: the risk is the
wire format -- call ids, string arguments, the auth header -- and only
real serialisation tests that.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from anthropic import beta_tool

from app.agents.providers import get_provider
from app.agents.providers.base import ProviderError
from app.agents.providers.openai_provider import OpenAICompatibleProvider


@pytest.fixture
def api():
    """Stub API. `scripted` is a queue of (status, body, headers) replies."""
    state = {"scripted": [], "requests": [], "headers": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            state["requests"].append(json.loads(body))
            state["headers"].append(dict(self.headers))
            state["path"] = self.path
            status, payload, headers = (
                state["scripted"].pop(0) if state["scripted"] else (200, _answer("done"), {})
            )
            encoded = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{server.server_address[1]}/v1"
    yield state
    server.shutdown()


@pytest.fixture
def tools():
    calls = []

    @beta_tool
    def list_low_stock_items() -> str:
        """List items below their reorder threshold."""
        calls.append(("list_low_stock_items", {}))
        return '[{"id": "abc", "name": "Tomatoes"}]'

    @beta_tool
    def propose_reorder(inventory_item_id: str, quantity: float) -> str:
        """Propose a reorder.

        Args:
            inventory_item_id: Which item.
            quantity: How much.
        """
        calls.append(("propose_reorder", {"inventory_item_id": inventory_item_id, "quantity": quantity}))
        return "Proposal queued"

    return [list_low_stock_items, propose_reorder], calls


def _answer(text, tokens=10):
    return {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"total_tokens": tokens},
    }


def _calls(*calls):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
                        for call_id, name, args in calls
                    ],
                }
            }
        ],
        "usage": {"total_tokens": 20},
    }


def _provider(api, **kwargs):
    kwargs.setdefault("api_key", "test-key")
    kwargs.setdefault("sleep", lambda seconds: api.setdefault("slept", []).append(seconds))
    return OpenAICompatibleProvider(base_url=api["url"], model="test-model", timeout=10, **kwargs)


def _run(provider, tool_list, emit=lambda *_: None):
    return provider.run_agent_loop(model="test-model", system="sys", tools=tool_list, user_message="go", emit=emit)


def test_plain_answer_returns_text_and_tokens(api, tools):
    tool_list, _ = tools
    api["scripted"] = [(200, _answer("Nothing to do.", tokens=42), {})]
    seen = []

    result = _run(_provider(api), tool_list, lambda t, p: seen.append((t, p)))

    assert result.text == "Nothing to do."
    assert result.tokens_used == 42
    assert ("log", {"text": "Nothing to do."}) in seen


def test_request_shape(api, tools):
    tool_list, _ = tools

    _run(_provider(api), tool_list)

    assert api["path"] == "/v1/chat/completions"
    assert api["headers"][0]["Authorization"] == "Bearer test-key"
    request = api["requests"][0]
    assert request["model"] == "test-model"
    assert request["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
    assert {t["function"]["name"] for t in request["tools"]} == {"list_low_stock_items", "propose_reorder"}


def test_tool_calls_run_and_results_answer_their_call_ids(api, tools):
    tool_list, calls = tools
    api["scripted"] = [
        (200, _calls(("call_1", "list_low_stock_items", {}),
                     ("call_2", "propose_reorder", {"inventory_item_id": "abc", "quantity": 5})), {}),
        (200, _answer("Reordered tomatoes."), {}),
    ]

    result = _run(_provider(api), tool_list)

    assert result.text == "Reordered tomatoes."
    assert calls == [("list_low_stock_items", {}), ("propose_reorder", {"inventory_item_id": "abc", "quantity": 5})]
    followup = api["requests"][1]["messages"]
    assert followup[2]["role"] == "assistant"
    assert [c["id"] for c in followup[2]["tool_calls"]] == ["call_1", "call_2"]
    assert followup[3] == {"role": "tool", "tool_call_id": "call_1", "content": '[{"id": "abc", "name": "Tomatoes"}]'}
    assert followup[4] == {"role": "tool", "tool_call_id": "call_2", "content": "Proposal queued"}


def test_rate_limit_is_waited_out(api, tools):
    tool_list, _ = tools
    api["scripted"] = [(429, {"error": "slow down"}, {"Retry-After": "3"}), (200, _answer("ok"), {})]

    result = _run(_provider(api), tool_list)

    assert result.text == "ok"
    assert api["slept"] == [3.0]


def test_persistent_rate_limit_fails_with_advice(api, tools):
    tool_list, _ = tools
    api["scripted"] = [(429, {"error": "slow down"}, {})] * 10

    with pytest.raises(ProviderError, match="rate limit"):
        _run(_provider(api), tool_list)


def test_bad_key_is_named(api, tools):
    tool_list, _ = tools
    api["scripted"] = [(401, {"error": "invalid key"}, {})]

    with pytest.raises(ProviderError, match="LLM_API_KEY"):
        _run(_provider(api), tool_list)


def test_missing_key_fails_before_calling(api, tools):
    tool_list, _ = tools

    with pytest.raises(ProviderError, match="LLM_API_KEY is not set"):
        _run(_provider(api, api_key=""), tool_list)
    assert api["requests"] == []


def test_model_is_always_the_configured_one(api):
    assert _provider(api).resolve_model("claude-opus-5") == "test-model"


def test_selected_by_llm_provider():
    assert isinstance(get_provider("openai"), OpenAICompatibleProvider)
