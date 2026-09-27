import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./app.db")
    frontend_origins: list[str] = [
        origin.strip() for origin in os.getenv("FRONTEND_ORIGIN", "http://localhost:5173").split(",") if origin.strip()
    ]
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY")

    # Which backend runs the agents: "anthropic" (hosted), "ollama" (local),
    # or "scripted" (deterministic, model-free -- for tests, CI and demos).
    llm_provider: str = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()

    # Ollama. `ollama_model` replaces AgentDefinition.model for every run,
    # since definitions are seeded with Claude model ids. It must be a
    # tool-capable model -- check `ollama show <model>` for "tools".
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    ollama_timeout_seconds: float = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "300"))

    # Sent on every request. Ollama reloads a model whenever a request asks
    # for a different context size than the loaded copy has, and unloads it
    # after five idle minutes by default -- either one turns a sub-second
    # call into a multi-second GPU load. Pinning both keeps it warm.
    ollama_num_ctx: int = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
    ollama_keep_alive: str = os.getenv("OLLAMA_KEEP_ALIVE", "30m")

    # Cap on tool round-trips in one run, so a model that loops on the
    # same call fails loudly instead of running forever.
    agent_max_tool_rounds: int = int(os.getenv("AGENT_MAX_TOOL_ROUNDS", "12"))

    # How many tool calls from one model turn may run at once. Only tools
    # marked parallel-safe (the Boss's delegations) ever run concurrently.
    agent_max_parallel_tools: int = int(os.getenv("AGENT_MAX_PARALLEL_TOOLS", "4"))

    # Cron-scheduled agents and the promotion-expiry sweep. Off in tests,
    # where a background thread touching the database would be a flake source.
    scheduler_enabled: bool = os.getenv("SCHEDULER_ENABLED", "true").strip().lower() in ("1", "true", "yes")

    # Pause between the scripted provider's steps, so a demo run streams
    # visibly instead of finishing before the page can show it.
    scripted_step_delay_seconds: float = float(os.getenv("SCRIPTED_STEP_DELAY_SECONDS", "0"))

    # Public demo: replace the data with the demo restaurant at startup and
    # again on this schedule (UTC), so visitors' changes never accumulate.
    demo_mode: bool = os.getenv("DEMO_MODE", "false").strip().lower() in ("1", "true", "yes")
    demo_reset_cron: str = os.getenv("DEMO_RESET_CRON", "0 4 * * *")

    # The built frontend, when one process serves both it and the API (the
    # single-container image). Unset in development, where Vite serves it.
    static_dir: str | None = os.getenv("STATIC_DIR") or None


settings = Settings()
