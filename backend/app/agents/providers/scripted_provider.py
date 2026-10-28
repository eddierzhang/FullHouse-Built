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
model.

Asked about customers, it reviews the open customer requests the way a
script honestly can: it counts them by kind, saves that as the summary,
and adds an action item to follow up each complaint and each request,
quoting how the customer's message starts.

Asked a price question from the thread (the task starts "Price question:"),
it answers from the invoices the way the real agent would: it finds the
item the question names, reads its price history, and posts what was paid,
where and when -- with another supplier's price when there is one -- citing
the invoices it used. "Short" questions read the short-delivery list;
"cheapest" and "supplier" questions read the supplier summary.

Selected with LLM_PROVIDER=scripted, and what a site with no model key runs
on: no key is needed to run the app, and this is the assistant it has then.
"""

import datetime
import inspect
import json
import re
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
    "customers": ("customer", "complaint", "guest", "service request"),
    "prices": ("invoice", "paid", "pay for", "cheapest", "charged", "short", "price question"),
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

        answered = self._maybe_answer_price_question(tools, user_message, emit, should_cancel)
        if answered is not None:
            return ProviderResult(text=answered, tokens_used=0)

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
        reviewed = self._maybe_review_customers(tools, user_message, findings, emit, should_cancel)
        if reviewed:
            findings = [f for f in findings if f[0] != "customers"] + [("customers", "customer requests", reviewed)]

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

    def _maybe_review_customers(self, tools, user_message, findings, emit, should_cancel) -> str | None:
        """Summarise the open customer requests when asked about customers; returns the summary, or None."""
        by_name = {t.name: t for t in tools}
        wanted = {"save_customer_service_summary", "add_customer_action_item"}
        listed = next((result for area, _, result in findings if area == "customers"), None)
        if listed is None or not wanted <= set(by_name) or not any(w in user_message.lower() for w in ROUTING["customers"]):
            return None
        try:
            requests = [r for r in json.loads(listed) if r.get("status") == "open"]
        except (TypeError, ValueError):
            return None
        if not requests:
            return "no open customer requests"

        def call(name: str, args: dict) -> None:
            self._pause(should_cancel)
            emit("tool_call", {"tool": name, "input": args})
            emit("tool_result", {"tool": name, "result": str(by_name[name].call(args))[:2000]})

        counts: dict[str, int] = {}
        for request in requests:
            counts[request["kind"]] = counts.get(request["kind"], 0) + 1
        order = ("complaint", "question", "request", "compliment")  # what needs an answer first
        kinds = sorted(counts, key=lambda kind: order.index(kind) if kind in order else len(order))
        parts = ", ".join(f"{counts[kind]} {kind}{'s' if counts[kind] != 1 and kind != 'other' else ''}" for kind in kinds)
        summary = f"{len(requests)} open customer request{'s' if len(requests) != 1 else ''}: {parts}."
        call("save_customer_service_summary", {"summary": summary, "request_ids": [r["id"] for r in requests]})

        follow_ups = [r for r in requests if r["kind"] in ("complaint", "request")]
        for request in follow_ups:
            opening = request["message"].strip().splitlines()[0]
            opening = opening if len(opening) <= 80 else opening[:77] + "..."
            call("add_customer_action_item", {
                "title": f"Follow up the {request['kind']} from {request['received']}",
                "details": f"The customer wrote: \"{opening}\" Decide what to do and get back to them.",
                "request_ids": [request["id"]],
                "priority": "high" if request["kind"] == "complaint" else "normal",
            })
        return f"{summary} Added {len(follow_ups)} action item{'s' if len(follow_ups) != 1 else ''}"

    PRICE_PREFIX = "price question:"

    def _maybe_answer_price_question(self, tools, user_message, emit, should_cancel) -> str | None:
        """Answer a thread question from the invoices; returns the answer, or None when this isn't one."""
        by_name = {t.name: t for t in tools}
        if not user_message.lower().startswith(self.PRICE_PREFIX) or "answer_price_question" not in by_name:
            return None
        question = user_message[len(self.PRICE_PREFIX):].strip()
        lowered = question.lower()

        def call(name: str, args: dict):
            self._pause(should_cancel)
            emit("tool_call", {"tool": name, "input": args})
            result = str(by_name[name].call(args))
            emit("tool_result", {"tool": name, "result": result[:2000]})
            try:
                return json.loads(result)
            except (TypeError, ValueError):
                return result

        def post(answer: str, invoice_ids: list[str], item_ids: list[str]) -> str:
            call("answer_price_question", {"answer": answer, "invoice_ids": invoice_ids, "item_ids": item_ids})
            return answer

        items = call("list_price_items", {})
        if not isinstance(items, list):
            return "Couldn't read the items."
        money = lambda v: f"${v:,.2f}"  # noqa: E731
        when = lambda iso: f"{datetime.date.fromisoformat(iso).strftime('%b')} {datetime.date.fromisoformat(iso).day}"  # noqa: E731

        # "about <item>, item id <id>" from the ask field's item context, else the first item the words name.
        chosen = None
        id_match = re.search(r"item id ([0-9a-f]{32})", question)
        if id_match:
            chosen = next((i for i in items if i["id"] == id_match.group(1)), None)
        if chosen is None:
            for item in sorted(items, key=lambda i: -len(i["name"])):
                head = item["name"].split(",")[0].lower()
                stems = {w.rstrip("es").rstrip("s") for w in head.split() if len(w) > 2}
                if stems and all(any(stem in w for w in re.findall(r"[a-z]+", lowered)) for stem in stems):
                    chosen = item
                    break

        if "short" in lowered or "deliver" in lowered or "credit" in lowered:
            shorts = call("short_deliveries", {"days": 90})
            if not shorts:
                return post("No invoice in the last 90 days billed more than arrived.", [], [i["id"] for i in items[:1]])
            supplier = next((s for s in {r["supplier"] for r in shorts} if s.lower() in lowered), None)
            rows = [r for r in shorts if supplier is None or r["supplier"] == supplier]
            owed = sum(r["owed"] for r in rows)
            who = supplier or ("suppliers" if len({r["supplier"] for r in rows}) > 1 else rows[0]["supplier"])
            first = rows[0]
            text = (f"{who} billed {money(owed)} more than it delivered across {len(rows)} invoice{'s' if len(rows) != 1 else ''} "
                    f"in the last 90 days. {money(first['owed'])} of it, {first['item']} on {when(first['date'])}, has no credit yet. "
                    f"Draft a credit claim.")
            return post(text, [r["invoice_id"] for r in rows[:3]], [])

        if chosen is not None:
            history = call("item_price_history", {"item_id": chosen["id"]})
            lines = history.get("lines", []) if isinstance(history, dict) else []
            if not lines:
                return post(f"Not enough matched invoices to compare yet. {chosen['name']} has no priced lines in the last 8 weeks.",
                            [], [chosen["id"]])
            latest = lines[0]
            others = [l for l in lines if l["supplier"] != latest["supplier"]]
            text = f"You paid {money(latest['per_pack'])} a {chosen['pack'].split(' per ')[-1]} for {chosen['name']} at {latest['supplier']} on {when(latest['date'])}."
            invoice_ids = [latest["invoice_id"]]
            if others:
                other = others[0]
                text += f" {other['supplier']} charged {money(other['per_pack'])} on {when(other['date'])}."
                invoice_ids.append(other["invoice_id"])
                if other["per_pack"] < latest["per_pack"]:
                    text += f" Move {chosen['name'].split(',')[0].lower()}."
            else:
                earlier = next((l for l in reversed(lines) if l["per_pack"] != latest["per_pack"]), None)
                if earlier:
                    text += f" On {when(earlier['date'])} it was {money(earlier['per_pack'])}."
                    invoice_ids.append(earlier["invoice_id"])
            return post(text, invoice_ids, [chosen["id"]])

        if "cheap" in lowered or "supplier" in lowered or "who" in lowered:
            summary = call("supplier_price_summary", {})
            rows = [r for r in summary if r["cheapest_on"] or r["dearer_on"]] if isinstance(summary, list) else []
            category = next((c for c in ("paper", "dry", "food") if c in lowered), None)
            if category:
                for r in rows:
                    r["cheapest_on"] = [c for c in r["cheapest_on"] if c["category"] == category]
                    r["dearer_on"] = [c for c in r["dearer_on"] if c["category"] == category]
                rows = [r for r in rows if r["cheapest_on"] or r["dearer_on"]]
            if not rows:
                return post("No item is bought from more than one supplier yet, so there is nothing to compare.", [], [items[0]["id"]] if items else [])
            best = max(rows, key=lambda r: len(r["cheapest_on"]))
            names = ", ".join(c["item"] for c in best["cheapest_on"][:3])
            text = (f"{best['supplier']} charged least on {len(best['cheapest_on'])} of the items you also buy elsewhere"
                    f"{': ' + names if names else ''}.")
            item_ids = [i["id"] for i in items if i["name"] in {c["item"] for c in best["cheapest_on"]}][:3]
            return post(text, [], item_ids or [items[0]["id"]])

        top = items[0] if items else None
        if top is None:
            return post("No invoices are matched yet. Add one and answer the agent's first question.", [], [])
        return post(f"Ask about an item, a supplier, or an invoice by name. The biggest change lately is {top['name']}, "
                    f"{money(abs(top['change_8w'] or 0))} a pack {'up' if (top['change_8w'] or 0) > 0 else 'down'} at {top['last_supplier']}.",
                    [], [top["id"]])
