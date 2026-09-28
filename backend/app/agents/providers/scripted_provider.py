"""A deterministic stand-in for a model, for browser tests, CI and demos.

End-to-end tests need an agent run to stream real events -- tool calls,
results, a summary -- but CI has no GPU, no Ollama, and should not spend
API credits. This provider exercises the whole pipeline (tools really run,
events really persist and stream) with a fixed, model-free script:

1. log what it is about to do;
2. pick the areas of the restaurant the task mentions -- all of them if
   it mentions none -- and in each, call the first tool that needs no
   arguments;
3. finish with a one-line finding per area.

Asked to reorder, it makes the one decision a script can make honestly:
it lists low stock and proposes a reorder of each low item the task names
-- or, naming none, the one furthest below its reorder point -- for that
item's usual reorder quantity. That is what lets a test (or a demo
visitor) go from a stock change to a proposal to an approval without a
model. Selected with LLM_PROVIDER=scripted.
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
)
from app.config import settings

#: Words that route a task to an area of the restaurant (see registry.TOOL_AREAS).
ROUTING = {
    "inventory": ("stock", "inventory", "ingredient", "low", "reorder"),
    "suppliers": ("supplier", "supply", "purchase", "delivery", "lead time"),
    "staff": ("staff", "shift", "schedule", "rota", "employee", "team", "cover"),
    "menu": ("menu", "promotion", "promo", "dish", "marketing", "seller", "price"),
    "profit": ("profit", "margin", "revenue", "sales", "money", "cost", "forecast"),
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


REORDER_WORDS = ("reorder", "restock", "order more", "top up")


def route(task: str, available: Sequence[str]) -> list[str]:
    """The areas a task is about, in the order they were offered."""
    text = task.lower()
    chosen = [area for area in available if any(word in text for word in ROUTING.get(area, ()))]
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

        # One argument-free tool per area; untagged tools count as one area.
        first_by_area: dict[str, Any] = {}
        for tool in tools:
            if _needs_no_arguments(tool):
                first_by_area.setdefault(getattr(tool, "area", "general"), tool)
        if not first_by_area:
            return ProviderResult(text="Scripted run finished; there is no argument-free tool to call.")

        areas = route(user_message, list(first_by_area))
        emit("log", {"text": f"Looking at {', '.join(areas)}."})

        findings = []
        for area in areas:
            tool = first_by_area[area]
            self._pause(should_cancel)
            emit("tool_call", {"tool": tool.name, "input": {}})
            result = tool.call({})
            if inspect.isawaitable(result):  # defensive: all current tools are sync
                raise TypeError(f"{tool.name} returned an awaitable")
            result = str(result)
            emit("tool_result", {"tool": tool.name, "result": result[:2000]})
            findings.append((area, tool.name, result))

        proposed = self._maybe_reorder(tools, user_message, emit, should_cancel)
        if proposed:
            findings.append(("inventory", "propose_reorder", proposed))

        if len(findings) == 1:
            _, name, result = findings[0]
            return ProviderResult(text=f"Checked {name.replace('_', ' ')}: {describe(result)}.", tokens_used=0)
        lines = [f"- {area}: {describe(result, 240)}" for area, _, result in findings]
        return ProviderResult(text="What I found:\n" + "\n".join(lines), tokens_used=0)

    def _maybe_reorder(self, tools, user_message, emit, should_cancel) -> str | None:
        """Propose reorders when asked to; returns what was proposed, or None."""
        text = user_message.lower()
        by_name = {t.name: t for t in tools}
        if not any(word in text for word in REORDER_WORDS) or not {"list_low_stock_items", "propose_reorder"} <= set(by_name):
            return None
        try:
            low = json.loads(str(by_name["list_low_stock_items"].call({})))
        except (TypeError, ValueError):
            return None
        if not low:
            return "nothing is below its reorder point"
        named = [row for row in low if str(row.get("name", "")).lower() in text]
        chosen = named or [min(low, key=lambda row: row["quantity_on_hand"] - row["reorder_threshold"])]
        results = []
        for row in chosen:
            quantity = row.get("reorder_qty") or max(row["reorder_threshold"] * 2 - row["quantity_on_hand"], 1)
            args = {
                "inventory_item_id": row["id"],
                "quantity": quantity,
                "note": f"{row['quantity_on_hand']:g} {row['unit']} on hand, reorder point {row['reorder_threshold']:g}.",
            }
            self._pause(should_cancel)
            emit("tool_call", {"tool": "propose_reorder", "input": args})
            result = str(by_name["propose_reorder"].call(args))
            emit("tool_result", {"tool": "propose_reorder", "result": result[:2000]})
            results.append(f"{quantity:g} {row['unit']} of {row['name']}")
        return "proposed reordering " + ", ".join(results)
