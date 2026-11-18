"""Model backends. Selected by LLM_PROVIDER; see `get_provider`."""

from app.agents.providers.base import (
    EventEmitter,
    LLMProvider,
    ProviderError,
    ProviderResult,
)
from app.config import settings

#: What the assistant says when LLM_PROVIDER=none turns it off. (With no key and no setting the
#: rule-based "scripted" assistant runs instead; this is for a site that chose to have none.)
NO_MODEL = (
    "No model is connected to this site, so the assistant can't answer. The digest, questions and "
    "invoices work without one. To turn answers on, set LLM_API_KEY and LLM_MODEL (a free Gemini key "
    "from https://aistudio.google.com/apikey works) or ANTHROPIC_API_KEY in the site's environment."
)

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
    key = (key or settings.llm_provider or "none").strip().lower()

    if key == "none":
        raise ProviderError(NO_MODEL)

    if key == "anthropic":
        from app.agents.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    if key == "ollama":
        from app.agents.providers.ollama_provider import OllamaProvider

        return OllamaProvider()

    if key == "openai":
        from app.agents.providers.openai_provider import OpenAICompatibleProvider

        return OpenAICompatibleProvider()

    if key == "scripted":
        from app.agents.providers.scripted_provider import ScriptedProvider

        return ScriptedProvider()

    raise ProviderError(f"Unknown LLM_PROVIDER {key!r}; expected 'anthropic', 'openai', 'ollama', 'scripted' or 'none'")
