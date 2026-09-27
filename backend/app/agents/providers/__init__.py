"""Model backends. Selected by LLM_PROVIDER; see `get_provider`."""

from app.agents.providers.base import (
    EventEmitter,
    LLMProvider,
    ProviderError,
    ProviderResult,
)
from app.config import settings

__all__ = [
    "EventEmitter",
    "LLMProvider",
    "ProviderError",
    "ProviderResult",
    "get_provider",
]


def get_provider(key: str | None = None) -> LLMProvider:
    """Build the configured provider.

    Read at call time rather than import time so the setting can be
    changed without restarting the process.
    """
    key = (key or settings.llm_provider or "anthropic").strip().lower()

    if key == "anthropic":
        from app.agents.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    if key == "ollama":
        from app.agents.providers.ollama_provider import OllamaProvider

        return OllamaProvider()

    if key == "scripted":
        from app.agents.providers.scripted_provider import ScriptedProvider

        return ScriptedProvider()

    raise ProviderError(f"Unknown LLM_PROVIDER {key!r}; expected 'anthropic', 'ollama' or 'scripted'")
