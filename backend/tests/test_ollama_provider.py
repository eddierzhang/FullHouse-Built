"""The Ollama loop, exercised over real HTTP.

A stub server rather than a mocked client: the thing most likely to be
wrong here is the wire format -- how tools are declared, and how results
are fed back -- and only real serialisation tests that.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from anthropic import beta_tool

from app.agents.providers.base import ProviderError, to_function_schema
from app.agents.providers.ollama_provider import OllamaProvider


@pytest.fixture
def ollama():
    """Stub Ollama. `scripted` is a queue of /api/chat responses."""
    state = {"scripted": [], "requests": [], "status": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            state["requests"].append(json.loads(body))
            if state["status"] != 200:
                self.send_response(state["status"])
                self.end_headers()
                self.wfile.write(b"boom")
                return
            payload = (
                state["scripted"].pop(0)
                if state["scripted"]
                else {"message": {"role": "assistant", "content": "done"}}
            )
            encoded = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()


@pytest.fixture
def tools():
    """Two tools shaped like the real ones, plus a record of what ran."""
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
        calls.append(
            ("propose_reorder", {"inventory_item_id": inventory_item_id, "quantity": quantity})
        )
        return "Proposal queued"

    return [list_low_stock_items, propose_reorder], calls


def _provider(ollama, **kwargs):
    return OllamaProvider(base_url=ollama["url"], model="test-model", timeout=10, **kwargs)


def _events():
    seen = []
    return seen, lambda event_type, payload: seen.append((event_type, payload))


def test_schema_conversion_matches_the_openai_function_shape(tools):
    tool_list, _ = tools
    propose_reorder = tool_list[1]

    schema = to_function_schema(propose_reorder)

    assert schema["type"] == "function"
    assert schema["function"]["name"] == "propose_reorder"
    assert schema["function"]["description"] == "Propose a reorder."
    params = schema["function"]["parameters"]
    assert params["type"] == "object"
    assert set(params["properties"]) == {"inventory_item_id", "quantity"}
    assert params["required"] == ["inventory_item_id", "quantity"]


def test_plain_answer_returns_text(ollama, tools):
    tool_list, _ = tools
    ollama["scripted"] = [{"message": {"role": "assistant", "content": "Nothing to do."}}]
    seen, emit = _events()

    result = _provider(ollama).run_agent_loop(
        model="test-model", system="sys", tools=tool_list, user_message="go", emit=emit
    )

    assert result.text == "Nothing to do."
    assert ("log", {"text": "Nothing to do."}) in seen


def test_tools_are_declared_to_the_model(ollama, tools):
    tool_list, _ = tools
    _, emit = _events()

    _provider(ollama).run_agent_loop(
        model="test-model", system="you are an agent", tools=tool_list, user_message="go", emit=emit
    )

    request = ollama["requests"][0]
    assert request["model"] == "test-model"
    assert request["stream"] is False
    assert request["messages"][0] == {"role": "system", "content": "you are an agent"}
    assert request["messages"][1] == {"role": "user", "content": "go"}
    assert {t["function"]["name"] for t in request["tools"]} == {
        "list_low_stock_items",
        "propose_reorder",
    }


def test_context_size_and_keep_alive_are_pinned_on_every_request(ollama, tools):
    """Leaving either to Ollama's defaults makes it reload the model between calls."""
    tool_list, _ = tools
    ollama["scripted"] = [
        {
            "message": {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "list_low_stock_items", "arguments": {}}}],
            }
        },
        {"message": {"role": "assistant", "content": "done"}},
    ]
    _, emit = _events()

    _provider(ollama, num_ctx=4096, keep_alive="1h").run_agent_loop(
        model="test-model", system="s", tools=tool_list, user_message="go", emit=emit
    )

    assert len(ollama["requests"]) == 2
    for request in ollama["requests"]:
        assert request["options"] == {"num_ctx": 4096}
        assert request["keep_alive"] == "1h"


def _two_calls_to(name_a, name_b):
    return {
        "message": {
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": name_a, "arguments": {"task": "a"}}},
                {"function": {"name": name_b, "arguments": {"task": "b"}}},
            ],
        }
    }


def test_parallel_safe_tools_from_one_turn_run_concurrently(ollama):
    # Each tool waits for the other to start; run one at a time, they would
    # time out instead.
    both_started = threading.Barrier(2, timeout=5)

    def make(name):
        @beta_tool(name=name, description="Slow delegation.")
        def slow(task: str) -> str:
            """
            Args:
                task: What to do.
            """
            both_started.wait()
            return f"{name} finished {task}"

        slow.parallel_safe = True
        return slow

    ollama["scripted"] = [_two_calls_to("first", "second"), {"message": {"role": "assistant", "content": "ok"}}]
    seen, emit = _events()

    _provider(ollama).run_agent_loop(
        model="test-model", system="s", tools=[make("first"), make("second")], user_message="go", emit=emit
    )

    tool_messages = [m for m in ollama["requests"][1]["messages"] if m["role"] == "tool"]
    # Results go back in the order the model asked, whatever order they finished in.
    assert [m["content"] for m in tool_messages] == ["first finished a", "second finished b"]
    assert [e for e, _ in seen] == ["tool_call", "tool_call", "tool_result", "tool_result", "log"]


def test_ordinary_tools_still_run_one_at_a_time(ollama):
    running = threading.Lock()
    overlapped = []

    def make(name):
        @beta_tool(name=name, description="Writes through the run's session.")
        def write(task: str) -> str:
            """
            Args:
                task: What to do.
            """
            if not running.acquire(blocking=False):
                overlapped.append(name)
                return "overlap"
            try:
                threading.Event().wait(0.05)
                return "ok"
            finally:
                running.release()

        return write

    ollama["scripted"] = [_two_calls_to("first", "second"), {"message": {"role": "assistant", "content": "ok"}}]
    seen, emit = _events()

    _provider(ollama).run_agent_loop(
        model="test-model", system="s", tools=[make("first"), make("second")], user_message="go", emit=emit
    )

    assert overlapped == []
    assert [e for e, _ in seen][:4] == ["tool_call", "tool_result", "tool_call", "tool_result"]


def test_tool_call_is_executed_and_fed_back(ollama, tools):
    tool_list, calls = tools
    ollama["scripted"] = [
        {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "list_low_stock_items", "arguments": {}}}],
            }
        },
        {"message": {"role": "assistant", "content": "One item is low."}},
    ]
    seen, emit = _events()

    result = _provider(ollama).run_agent_loop(
        model="test-model", system="sys", tools=tool_list, user_message="check", emit=emit
    )

    assert calls == [("list_low_stock_items", {})]
    assert result.text == "One item is low."

    # The second request must carry the tool's output back to the model.
    follow_up = ollama["requests"][1]["messages"]
    assert follow_up[-1]["role"] == "tool"
    assert follow_up[-1]["tool_name"] == "list_low_stock_items"
    assert "Tomatoes" in follow_up[-1]["content"]

    kinds = [event for event, _ in seen]
    assert "tool_call" in kinds and "tool_result" in kinds


def test_arguments_are_passed_through_typed(ollama, tools):
    tool_list, calls = tools
    ollama["scripted"] = [
        {
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "name": "propose_reorder",
                            "arguments": {"inventory_item_id": "abc", "quantity": 25},
                        }
                    }
                ],
            }
        },
        {"message": {"role": "assistant", "content": "Queued."}},
    ]
    _, emit = _events()

    _provider(ollama).run_agent_loop(
        model="test-model", system="s", tools=tool_list, user_message="go", emit=emit
    )

    assert calls == [("propose_reorder", {"inventory_item_id": "abc", "quantity": 25.0})]


def test_json_string_arguments_are_accepted(ollama, tools):
    """OpenAI-compatible paths serialise arguments as a string, not an object."""
    tool_list, calls = tools
    ollama["scripted"] = [
        {
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "name": "propose_reorder",
                            "arguments": '{"inventory_item_id": "xyz", "quantity": 3}',
                        }
                    }
                ],
            }
        },
        {"message": {"role": "assistant", "content": "ok"}},
    ]
    _, emit = _events()

    _provider(ollama).run_agent_loop(
        model="test-model", system="s", tools=tool_list, user_message="go", emit=emit
    )

    assert calls == [("propose_reorder", {"inventory_item_id": "xyz", "quantity": 3.0})]


def test_several_tool_calls_in_one_turn(ollama, tools):
    tool_list, calls = tools
    ollama["scripted"] = [
        {
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "list_low_stock_items", "arguments": {}}},
                    {
                        "function": {
                            "name": "propose_reorder",
                            "arguments": {"inventory_item_id": "abc", "quantity": 5},
                        }
                    },
                ],
            }
        },
        {"message": {"role": "assistant", "content": "Both done."}},
    ]
    _, emit = _events()

    _provider(ollama).run_agent_loop(
        model="test-model", system="s", tools=tool_list, user_message="go", emit=emit
    )

    assert [name for name, _ in calls] == ["list_low_stock_items", "propose_reorder"]
    assert sum(1 for m in ollama["requests"][1]["messages"] if m["role"] == "tool") == 2


def test_unknown_tool_is_reported_to_the_model_not_raised(ollama, tools):
    tool_list, _ = tools
    ollama["scripted"] = [
        {
            "message": {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "delete_everything", "arguments": {}}}],
            }
        },
        {"message": {"role": "assistant", "content": "I will stop."}},
    ]
    _, emit = _events()

    result = _provider(ollama).run_agent_loop(
        model="test-model", system="s", tools=tool_list, user_message="go", emit=emit
    )

    assert result.text == "I will stop."
    assert "no tool named" in ollama["requests"][1]["messages"][-1]["content"]


def test_a_failing_tool_is_reported_to_the_model(ollama):
    @beta_tool
    def explode() -> str:
        """Always fails."""
        raise ValueError("kaboom")

    ollama["scripted"] = [
        {
            "message": {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "explode", "arguments": {}}}],
            }
        },
        {"message": {"role": "assistant", "content": "Recovered."}},
    ]
    _, emit = _events()

    result = _provider(ollama).run_agent_loop(
        model="test-model", system="s", tools=[explode], user_message="go", emit=emit
    )

    assert result.text == "Recovered."
    assert "kaboom" in ollama["requests"][1]["messages"][-1]["content"]


def test_tokens_are_summed_across_rounds(ollama, tools):
    tool_list, _ = tools
    ollama["scripted"] = [
        {
            "message": {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "list_low_stock_items", "arguments": {}}}],
            },
            "prompt_eval_count": 100,
            "eval_count": 20,
        },
        {
            "message": {"role": "assistant", "content": "done"},
            "prompt_eval_count": 150,
            "eval_count": 30,
        },
    ]
    _, emit = _events()

    result = _provider(ollama).run_agent_loop(
        model="test-model", system="s", tools=tool_list, user_message="go", emit=emit
    )

    assert result.tokens_used == 300


def test_a_looping_model_hits_the_round_cap(ollama, tools):
    tool_list, _ = tools
    ollama["scripted"] = [
        {
            "message": {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "list_low_stock_items", "arguments": {}}}],
            }
        }
    ] * 10
    _, emit = _events()

    with pytest.raises(ProviderError, match="did not finish within 3 tool rounds"):
        _provider(ollama, max_rounds=3).run_agent_loop(
            model="test-model", system="s", tools=tool_list, user_message="go", emit=emit
        )


def test_unreachable_ollama_gives_an_actionable_error(tools):
    tool_list, _ = tools
    _, emit = _events()
    provider = OllamaProvider(base_url="http://127.0.0.1:1", model="m", timeout=2)

    with pytest.raises(ProviderError, match="ollama serve"):
        provider.run_agent_loop(
            model="m", system="s", tools=tool_list, user_message="go", emit=emit
        )


def test_missing_model_says_how_to_pull_it(ollama, tools):
    tool_list, _ = tools
    ollama["status"] = 404
    _, emit = _events()

    with pytest.raises(ProviderError, match="ollama pull missing-model"):
        _provider(ollama).run_agent_loop(
            model="missing-model", system="s", tools=tool_list, user_message="go", emit=emit
        )
