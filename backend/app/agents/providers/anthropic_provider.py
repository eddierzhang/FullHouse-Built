"""The original backend: Anthropic's tool runner drives the loop for us."""

from collections.abc import Sequence
from typing import Any

import anthropic

from app.agents.providers.base import (
    CancelCheck,
    EventEmitter,
    LLMProvider,
    ProviderError,
    ProviderResult,
    RunCancelledError,
)
from app.config import settings


def _extract_text(message) -> str:
    if message is None:
        return ""
    return "\n".join(block.text for block in message.content if block.type == "text" and block.text)


class AnthropicProvider(LLMProvider):
    key = "anthropic"

    def __init__(self, api_key: str | None = None, max_tokens: int = 16000):
        self.api_key = api_key if api_key is not None else settings.anthropic_api_key
        self.max_tokens = max_tokens

    def resolve_model(self, configured_model: str) -> str:
        return configured_model

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
        client = anthropic.Anthropic(api_key=self.api_key)
        try:
            tool_runner = client.beta.messages.tool_runner(
                model=model,
                max_tokens=self.max_tokens,
                system=system,
                tools=list(tools),
                messages=[{"role": "user", "content": user_message}],
            )

            last_message = None
            tokens = 0
            for message in tool_runner:
                if should_cancel and should_cancel():
                    raise RunCancelledError("Run cancelled between model turns")
                last_message = message
                usage = getattr(message, "usage", None)
                if usage is not None:
                    tokens += (usage.input_tokens or 0) + (usage.output_tokens or 0)
                for block in message.content:
                    if block.type == "text" and block.text:
                        emit("log", {"text": block.text})
                    elif block.type == "tool_use":
                        emit("tool_call", {"tool": block.name, "input": block.input})
        except anthropic.AnthropicError as exc:
            raise ProviderError(f"Anthropic request failed: {exc}") from exc

        return ProviderResult(text=_extract_text(last_message), tokens_used=tokens or None)
