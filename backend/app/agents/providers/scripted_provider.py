"""A deterministic stand-in for a model, for browser tests, CI and demos.

End-to-end tests need an agent run to stream real events -- tool calls,
results, a summary -- but CI has no GPU, no Ollama, and should not spend
API credits. This provider exercises the whole pipeline (tools really run,
events really persist and stream) with a fixed, model-free script.

A specialist:

1. logs what it is about to do;
2. calls its first tool that needs no arguments, if it has one;
3. finishes with a one-line summary of what the tool returned.

Maestro, whose only tools are delegations, instead hands the task to the
specialists whose domain the task mentions -- all of them if it mentions
none -- in parallel, exactly as a real model's parallel tool calls would.

It makes no decisions, so it never proposes anything -- the approval flow
is exercised with seeded proposals instead. Selected with LLM_PROVIDER=scripted.
"""

import inspect
import json
import threading
from collections.abc import Sequence
from typing import Any

from app.agents.providers.base import (
    CancelCheck,
    EventEmitter,
    LLMProvider,
    ProviderResult,
    RunCancelledError,
    is_parallel_safe,
    run_tool_calls,
)
from app.config import settings

DELEGATE_PREFIX = "delegate_to_"

#: Words that route a task to a specialist, keyed by agent key.
ROUTING = {
    "inventory": ("stock", "inventory", "ingredient", "low", "reorder"),
    "supply_chain": ("supplier", "supply", "purchase", "delivery", "lead time"),
    "employee_management": ("staff", "shift", "schedule", "rota", "employee", "team", "cover"),
    "profit": ("profit", "margin", "revenue", "sales", "money", "cost", "forecast"),
    "marketing": ("menu", "promotion", "promo", "dish", "marketing", "seller", "price"),
}


def describe(result: str, limit: int = 160) -> str:
    """A one-line reading of a tool result, in place of raw JSON.

    Tools answer in JSON for the model's benefit; quoted as-is it makes a
    demo summary look like a stack trace.
    """
    try:
        data = json.loads(result)
    except (TypeError, ValueError):
        data = None

    if isinstance(data, list) and data and all(isinstance(row, dict) and "name" in row for row in data):
        names = [str(row["name"]) for row in data]
        shown = ", ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")
        text = f"{len(names)} found: {shown}"
    elif isinstance(data, list) and not data:
        text = "nothing found"
    elif isinstance(data, dict):
        scalars = [(k, v) for k, v in data.items() if isinstance(v, (str, int, float)) and k != "id"]
        text = ", ".join(f"{k.replace('_', ' ')} {v:g}" if isinstance(v, float) else f"{k.replace('_', ' ')} {v}"
                         for k, v in scalars[:4]) or result
    else:
        text = result
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _needs_no_arguments(tool: Any) -> bool:
    return not (tool.input_schema or {}).get("required")


def _is_delegation(tool: Any) -> bool:
    return tool.name.startswith(DELEGATE_PREFIX) and is_parallel_safe(tool)


def route(task: str, available: Sequence[str]) -> list[str]:
    """The agent keys a task is about, in the order they were offered."""
    text = task.lower()
    chosen = [key for key in available if any(word in text for word in ROUTING.get(key, ()))]
    return chosen or list(available)


class ScriptedProvider(LLMProvider):
    key = "scripted"

    def __init__(self, step_delay: float | None = None):
        self.step_delay = settings.scripted_step_delay_seconds if step_delay is None else step_delay

    def resolve_model(self, configured_model: str) -> str:
        return "scripted"

    def _pause(self, should_cancel: CancelCheck | None) -> None:
        if self.step_delay > 0:
            threading.Event().wait(self.step_delay)
        if should_cancel and should_cancel():
            raise RunCancelledError("Run cancelled during a scripted step")

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
        emit("log", {"text": f"Scripted run: {user_message}"})

        if tools and all(_is_delegation(t) for t in tools):
            return self._delegate(tools, user_message, emit, should_cancel)

        tool = next((t for t in tools if _needs_no_arguments(t)), None)
        if tool is None:
            return ProviderResult(text="Scripted run finished; this agent has no argument-free tool.")

        self._pause(should_cancel)
        emit("tool_call", {"tool": tool.name, "input": {}})
        result = tool.call({})
        if inspect.isawaitable(result):  # defensive: all current tools are sync
            raise TypeError(f"{tool.name} returned an awaitable")
        result = str(result)
        self._pause(should_cancel)
        emit("tool_result", {"tool": tool.name, "result": result[:2000]})

        return ProviderResult(text=f"Checked {tool.name.replace('_', ' ')}: {describe(result)}.", tokens_used=0)

    def _delegate(self, tools, user_message, emit, should_cancel) -> ProviderResult:
        tools_by_name = {t.name: t for t in tools}
        keys = route(user_message, [t.name.removeprefix(DELEGATE_PREFIX) for t in tools])
        emit("log", {"text": f"Handing this to {len(keys)} specialist(s) at once: {', '.join(keys)}."})
        self._pause(should_cancel)

        requested = [(DELEGATE_PREFIX + key, {"task": user_message}) for key in keys]
        findings = []
        for name, result in run_tool_calls(requested, tools_by_name, emit, max_parallel=len(requested)):
            emit("tool_result", {"tool": name, "result": result[:2000]})
            findings.append(f"- {name.removeprefix(DELEGATE_PREFIX).replace('_', ' ')}: {describe(result, 240)}")

        return ProviderResult(text="Findings from the team:\n" + "\n".join(findings), tokens_used=0)
