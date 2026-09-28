import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./app.db")
    frontend_origins: list[str] = [
        origin.strip() for origin in os.getenv("FRONTEND_ORIGIN", "http://localhost:5173").split(",") if origin.strip()
    ]
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY")

    # Which backend runs the agents: "anthropic" (hosted), "openai" (any
    # hosted OpenAI-compatible API -- Gemini's free tier by default),
    # "ollama" (local), or "scripted" (deterministic, model-free -- for
    # tests, CI and demos).
    llm_provider: str = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()

    # OpenAI-compatible APIs. The defaults are Google Gemini's free tier;
    # a key from https://aistudio.google.com/apikey is all it needs.
    # `llm_model` replaces AgentDefinition.model for every run, as with Ollama.
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
    llm_api_key: str | None = os.getenv("LLM_API_KEY")
    llm_model: str = os.getenv("LLM_MODEL", "gemini-3.5-flash")
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "120"))

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

    # The longest one agent run may take, start to finish. A model that is
    # slow or stuck ends as a failed run saying so, instead of "running"
    # forever; everything else in the app carries on regardless.
    agent_run_timeout_seconds: float = float(os.getenv("AGENT_RUN_TIMEOUT_SECONDS", "600"))

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

    # Whether seed_data.py fills an empty database with a sample restaurant
    # (suppliers, stock, menu, staff, orders). Off for a customer's own
    # instance, which starts empty and is filled by the owner's setup.
    seed_sample_data: bool = os.getenv("SEED_SAMPLE_DATA", "true").strip().lower() in ("1", "true", "yes")

    # Encrypts two-step sign-in secrets at rest. Set a long random value in
    # production (render.customer.yaml has Render generate one) and never
    # change it: secrets saved under the old key stop working, and everyone
    # with two-step sign-in would have to set it up again.
    secret_key: str | None = os.getenv("SECRET_KEY") or None

    # Owners and admins must use two-step sign-in. Off for the public demo,
    # whose one shared account is meant for strangers to use.
    require_mfa: bool = os.getenv("REQUIRE_MFA", "true").strip().lower() in ("1", "true", "yes")

    # Whether anyone can create an account in the restaurant from the login
    # page. Off by default: the team joins with invite links from the Profile
    # page. (Before the restaurant exists, signing up is how its owner starts.)
    allow_signup: bool = os.getenv("ALLOW_SIGNUP", "false").strip().lower() in ("1", "true", "yes")

    # The built frontend, when one process serves both it and the API (the
    # single-container image). Unset in development, where Vite serves it.
    static_dir: str | None = os.getenv("STATIC_DIR") or None

    # Error alerts. With a DSN set, unhandled errors -- in the API, in agent
    # runs, and ones browsers report -- go to Sentry, which emails you about
    # new ones. Unset (the default), nothing is sent anywhere.
    sentry_dsn: str | None = os.getenv("SENTRY_DSN") or None
    sentry_environment: str = os.getenv("SENTRY_ENVIRONMENT", "production")

    # Who owners and staff contact for help, shown on the Help page. Blank
    # until you set them.
    support_email: str | None = os.getenv("SUPPORT_EMAIL") or None
    support_url: str | None = os.getenv("SUPPORT_URL") or None


settings = Settings()
