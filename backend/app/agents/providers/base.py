"""Provider-agnostic agent loop.

The platform's tools are declared with Anthropic's `@beta_tool`, which
is convenient but not Anthropic-specific in what it exposes: every tool
object carries a `name`, a `description`, a real JSON Schema in
`input_schema`, and a `call(dict)` entry point. That is exactly the
shape every tool-calling API wants, so the tool modules need no changes
to run against a different backend -- only the loop that drives them
does.
"""

import contextvars
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

#: Records a run event: (event_type, payload). Types match AgentEvent.type --
#: "log", "tool_call", "tool_result".
EventEmitter = Callable[[str, dict], None]

#: Checked between tool rounds so a run can be stopped without killing the
#: worker thread mid-write. Cooperative: a round already in flight finishes.
CancelCheck = Callable[[], bool]


class RunCancelledError(RuntimeError):
    """The operator stopped this run."""


class ProviderError(RuntimeError):
    """The model backend could not complete the run."""


@dataclass
class ProviderResult:
    text: str
    tokens_used: int | None = None


def to_function_schema(tool: Any) -> dict:
    """Render a `@beta_tool` as an OpenAI-style function declaration.

    Ollama, and everything else modelled on the OpenAI schema, expects
    `{"type": "function", "function": {name, description, parameters}}`
    where `parameters` is the JSON Schema for the arguments.
    """
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.input_schema,
        },
    }


def is_parallel_safe(tool: Any) -> bool:
    """Tools opt in by setting `parallel_safe = True`.

    Most tools write through the run's own database session, which must not
    be shared across threads, so concurrency is never the default.
    """
    return bool(getattr(tool, "parallel_safe", False))


def call_tool(name: str, arguments: dict, tools_by_name: dict) -> str:
    """Run one tool, turning every failure into text the model can read."""
    tool = tools_by_name.get(name)
    if tool is None:
        return f"Error: no tool named {name!r}. Available: {', '.join(sorted(tools_by_name))}"
    try:
        return str(tool.call(arguments))
    except Exception as exc:  # surfaced to the model, not raised
        return f"Error running {name}: {exc}"


def run_tool_calls(
    requested: list[tuple[str, dict]],
    tools_by_name: dict,
    emit: EventEmitter,
    max_parallel: int = 1,
) -> Iterator[tuple[str, str]]:
    """Yield (name, result) for each requested call, in request order.

    When a model asks for several parallel-safe tools in one turn -- a lead
    agent delegating to three specialists -- they run concurrently, so the
    turn takes as long as the slowest rather than the sum. Anything else
    runs one at a time. Events are only ever emitted from the calling
    thread, since `emit` writes through the run's session.
    """
    concurrent = (
        len(requested) > 1
        and max_parallel > 1
        and all(is_parallel_safe(tools_by_name.get(name)) for name, _ in requested)
    )
    if not concurrent:
        for name, arguments in requested:
            emit("tool_call", {"tool": name, "input": arguments})
            yield name, call_tool(name, arguments, tools_by_name)
        return

    for name, arguments in requested:
        emit("tool_call", {"tool": name, "input": arguments})
    with ThreadPoolExecutor(max_workers=min(max_parallel, len(requested)), thread_name_prefix="agent-tool") as pool:
        # Each call gets its own copy of the caller's context, so actor
        # attribution follows the work into the worker thread.
        futures = [
            pool.submit(contextvars.copy_context().run, call_tool, name, arguments, tools_by_name)
            for name, arguments in requested
        ]
        results = [future.result() for future in futures]
    yield from zip((name for name, _ in requested), results)


class LLMProvider(ABC):
    """Drives one agent run to completion against a model backend."""

    key: str

    @abstractmethod
    def resolve_model(self, configured_model: str) -> str:
        """The model to actually call.

        An `AgentDefinition` stores a model name chosen for one backend;
        pointed at another, that name is meaningless, so each provider
        decides what it can honour.
        """

    @abstractmethod
    def run_agent_loop(
        self,
        *,
        model: str,
        system: str,
        tools: Sequence[Any],
        user_message: str,
        emit: EventEmitter,
        should_cancel: CancelCheck | None = None,
    ) -> ProviderResult:
        """Run the model, execute any tools it calls, and return its final text."""
