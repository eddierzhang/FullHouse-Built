"""Run agents against a local Ollama model.

Ollama has no agentic helper equivalent to Anthropic's tool runner, so
the loop lives here: call the model, execute whatever tools it asked
for, feed the results back, repeat until it answers without calling a
tool.

The model must actually support tool calling -- `ollama show <model>`
lists "tools" under capabilities. A model without it silently ignores
the tools and just talks, which surfaces as a run that reports instead
of acting.
"""

import json
import threading
from collections.abc import Sequence
from typing import Any

import httpx

from app.agents.providers.base import (
    CancelCheck,
    EventEmitter,
    LLMProvider,
    ProviderError,
    ProviderResult,
    RunCancelledError,
    run_tool_calls,
    to_function_schema,
)
from app.config import settings

#: Tool output is fed back into the prompt, so an unbounded result (a
#: whole inventory dump) can blow the context window on a small model.
MAX_TOOL_RESULT_CHARS = 8000

# One connection pool for the process. A provider is built per run, and a
# Boss run fans out into several at once, so a client per call would pay a
# fresh TCP connection on every model turn. httpx.Client is thread-safe.
_client: httpx.Client | None = None
_client_lock = threading.Lock()


def _shared_client() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None or _client.is_closed:
            _client = httpx.Client()
        return _client


class OllamaProvider(LLMProvider):
    key = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        max_rounds: int | None = None,
        num_ctx: int | None = None,
        keep_alive: str | None = None,
        max_parallel_tools: int | None = None,
    ):
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.timeout = timeout if timeout is not None else settings.ollama_timeout_seconds
        self.max_rounds = max_rounds or settings.agent_max_tool_rounds
        self.num_ctx = num_ctx or settings.ollama_num_ctx
        self.keep_alive = keep_alive or settings.ollama_keep_alive
        self.max_parallel_tools = max(1, max_parallel_tools or settings.agent_max_parallel_tools)

    def resolve_model(self, configured_model: str) -> str:
        """Always the configured Ollama model.

        `AgentDefinition.model` holds a Claude model id from seeding;
        sending that to Ollama would just 404. One env var repoints the
        whole platform rather than requiring every definition be edited.
        """
        return self.model

    def _chat(self, model: str, messages: list[dict], tools: list[dict]) -> dict:
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": self.num_ctx},
        }
        try:
            response = _shared_client().post(
                f"{self.base_url}/api/chat", json=payload, timeout=self.timeout
            )
        except httpx.RequestError as exc:
            raise ProviderError(
                f"Cannot reach Ollama at {self.base_url} ({exc}). Is `ollama serve` running?"
            ) from exc

        if response.status_code == 404:
            raise ProviderError(
                f"Ollama has no model {model!r}. Pull it first: `ollama pull {model}`"
            )
        if response.status_code >= 400:
            raise ProviderError(f"Ollama returned {response.status_code}: {response.text[:500]}")

        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f"Ollama returned a non-JSON response: {response.text[:200]}") from exc

    @staticmethod
    def _arguments(raw: Any) -> dict:
        """Ollama sends an object; OpenAI-compatible paths send a JSON string."""
        if isinstance(raw, str):
            try:
                return json.loads(raw or "{}")
            except json.JSONDecodeError:
                return {}
        return dict(raw or {})

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
        tools_by_name = {tool.name: tool for tool in tools}
        declarations = [to_function_schema(tool) for tool in tools]
        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ]
        tokens = 0

        for _ in range(self.max_rounds):
            if should_cancel and should_cancel():
                raise RunCancelledError("Run cancelled before the next model call")
            response = self._chat(model, messages, declarations)
            tokens += int(response.get("prompt_eval_count") or 0)
            tokens += int(response.get("eval_count") or 0)

            message = response.get("message") or {}
            text = (message.get("content") or "").strip()
            if text:
                emit("log", {"text": text})

            calls = message.get("tool_calls") or []
            if not calls:
                return ProviderResult(text=text, tokens_used=tokens or None)

            messages.append(message)
            requested = []
            for call in calls:
                function = call.get("function") or {}
                name = function.get("name") or ""
                requested.append((name, self._arguments(function.get("arguments"))))

            for name, result in run_tool_calls(requested, tools_by_name, emit, self.max_parallel_tools):
                result = result[:MAX_TOOL_RESULT_CHARS]
                emit("tool_result", {"tool": name, "result": result})
                messages.append({"role": "tool", "tool_name": name, "content": result})

        raise ProviderError(
            f"Agent did not finish within {self.max_rounds} tool rounds. "
            f"Raise AGENT_MAX_TOOL_ROUNDS, or use a stronger model -- small models "
            f"often loop on the same call."
        )
