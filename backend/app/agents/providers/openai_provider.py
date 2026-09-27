"""Run agents against any hosted OpenAI-compatible chat API.

One wire format covers most hosted models: Google Gemini (whose free tier
is the default here, so an instance can run real agents at no cost), Groq,
OpenRouter, Mistral, and OpenAI itself. Point LLM_BASE_URL, LLM_API_KEY and
LLM_MODEL at any of them.

Like Ollama, there is no tool runner, so the loop lives here. The format
differs from Ollama's in the details that matter: arguments arrive as a
JSON string, and each tool result must name the call it answers by id.
"""

import json
import threading
import time
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

#: Tool output is fed back into the prompt; free tiers meter tokens per
#: minute, so an unbounded result (a whole inventory dump) costs twice.
MAX_TOOL_RESULT_CHARS = 8000

#: Free tiers allow a handful of requests a minute, and a lead agent
#: delegating to three specialists makes several at once. A 429 is waited
#: out this many times before the run fails.
RATE_LIMIT_RETRIES = 4
#: Longest single wait on a 429, whatever Retry-After asks for.
MAX_RETRY_WAIT_SECONDS = 30.0

# One connection pool for the process, as in the Ollama provider.
_client: httpx.Client | None = None
_client_lock = threading.Lock()


def _shared_client() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None or _client.is_closed:
            _client = httpx.Client()
        return _client


def _retry_after(response: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying: Retry-After if sent, else exponential."""
    try:
        wait = float(response.headers.get("retry-after", ""))
    except ValueError:
        wait = 2.0 * 2**attempt
    return min(max(wait, 0.0), MAX_RETRY_WAIT_SECONDS)


class OpenAICompatibleProvider(LLMProvider):
    key = "openai"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        max_rounds: int | None = None,
        max_parallel_tools: int | None = None,
        sleep=time.sleep,
    ):
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model = model or settings.llm_model
        self.timeout = timeout if timeout is not None else settings.llm_timeout_seconds
        self.max_rounds = max_rounds or settings.agent_max_tool_rounds
        self.max_parallel_tools = max(1, max_parallel_tools or settings.agent_max_parallel_tools)
        self._sleep = sleep

    def resolve_model(self, configured_model: str) -> str:
        """Always LLM_MODEL: definitions are seeded with Claude model ids."""
        return self.model

    def _chat(self, model: str, messages: list[dict], tools: list[dict]) -> dict:
        if not self.api_key:
            raise ProviderError(
                "LLM_API_KEY is not set. Get a free Gemini key at https://aistudio.google.com/apikey "
                "and set it in the host's environment."
            )
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            payload["tools"] = tools

        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                response = _shared_client().post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=self.timeout,
                )
            except httpx.RequestError as exc:
                raise ProviderError(f"Cannot reach the model API at {self.base_url} ({exc})") from exc

            if response.status_code == 429 and attempt < RATE_LIMIT_RETRIES:
                self._sleep(_retry_after(response, attempt))
                continue
            break

        if response.status_code == 429:
            raise ProviderError(
                "The model API's rate limit was hit and did not clear. On a free tier, wait a minute "
                f"and retry, or lower AGENT_MAX_PARALLEL_TOOLS. Response: {response.text[:300]}"
            )
        if response.status_code in (401, 403):
            raise ProviderError(f"The model API rejected LLM_API_KEY ({response.status_code}): {response.text[:300]}")
        if response.status_code >= 400:
            raise ProviderError(f"The model API returned {response.status_code}: {response.text[:500]}")

        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f"The model API returned a non-JSON response: {response.text[:200]}") from exc

    @staticmethod
    def _arguments(raw: Any) -> dict:
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
            usage = response.get("usage") or {}
            tokens += int(usage.get("total_tokens") or 0)

            choices = response.get("choices") or []
            if not choices:
                raise ProviderError(f"The model API returned no choices: {json.dumps(response)[:300]}")
            message = choices[0].get("message") or {}
            text = (message.get("content") or "").strip()
            if text:
                emit("log", {"text": text})

            calls = message.get("tool_calls") or []
            if not calls:
                return ProviderResult(text=text, tokens_used=tokens or None)

            # Echo the assistant turn back exactly as the API expects it:
            # the results that follow refer to these call ids.
            messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": calls})
            requested = []
            for call in calls:
                function = call.get("function") or {}
                requested.append((function.get("name") or "", self._arguments(function.get("arguments"))))

            results = run_tool_calls(requested, tools_by_name, emit, self.max_parallel_tools)
            for call, (name, result) in zip(calls, results):
                result = result[:MAX_TOOL_RESULT_CHARS]
                emit("tool_result", {"tool": name, "result": result})
                messages.append({"role": "tool", "tool_call_id": call.get("id") or "", "content": result})

        raise ProviderError(
            f"Agent did not finish within {self.max_rounds} tool rounds. "
            f"Raise AGENT_MAX_TOOL_ROUNDS, or set LLM_MODEL to a stronger model."
        )
