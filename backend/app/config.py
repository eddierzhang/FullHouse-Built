import os

from dotenv import load_dotenv

load_dotenv()


def provider_from_env(environ=os.environ) -> str:
    """LLM_PROVIDER when set; otherwise the provider the keys in the environment point at.

    With no key at all it is "scripted": the rule-based assistant, which answers price
    questions from the invoices and runs the archived agents by rule. No key is needed
    to run the app; a model adds free-form answers and the reading of photos.
    """
    explicit = environ.get("LLM_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    if environ.get("ANTHROPIC_API_KEY", "").strip():
        return "anthropic"
    if environ.get("LLM_API_KEY", "").strip():
        return "openai"
    return "scripted"


class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./app.db")
    frontend_origins: list[str] = [
        origin.strip() for origin in os.getenv("FRONTEND_ORIGIN", "http://localhost:5173").split(",") if origin.strip()
    ]
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY")

    # Which backend runs the agents: "anthropic" (hosted), "openai" (any
    # hosted OpenAI-compatible API -- Gemini's free tier by default),
    # "ollama" (local), "scripted" (rule-based, no model: answers price
    # questions from the invoices; also what tests, CI and the demo run on) or
    # "none" (the assistant is off and says so). Unset, it follows whichever
    # key is present: ANTHROPIC_API_KEY, then LLM_API_KEY, else "scripted"
    # (see `provider_from_env`).
    llm_provider: str = provider_from_env()

    # OpenAI-compatible APIs. The base URL defaults to Google Gemini's; there
    # is no default model, so LLM_MODEL must name one the API serves.
    # `llm_model` replaces AgentDefinition.model for every run, as with Ollama.
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
    llm_api_key: str | None = os.getenv("LLM_API_KEY", "").strip() or None
    llm_model: str = os.getenv("LLM_MODEL", "").strip()
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

    # The job queue (app.platform.jobs). By default the web process runs worker threads itself,
    # which is right for one free container; WORKER_INPROCESS=false and `python -m app.worker`
    # run them apart.
    worker_inprocess: bool = os.getenv("WORKER_INPROCESS", "true").strip().lower() in ("1", "true", "yes")
    worker_threads: int = int(os.getenv("WORKER_THREADS", "2") or 2)
    worker_poll_seconds: float = float(os.getenv("WORKER_POLL_SECONDS", "0.5") or 0.5)
    #: A job whose worker goes quiet this long is leased to another.
    job_lease_seconds: int = int(os.getenv("JOB_LEASE_SECONDS", "300") or 300)
    #: Who may open the ops page: emails, comma-separated.
    superuser_emails: list[str] = [e.strip().lower() for e in os.getenv("SUPERUSER_EMAILS", "").split(",") if e.strip()]
    #: "json" for one JSON object per log line (hosts that collect stdout); anything else is readable text.
    log_format: str = os.getenv("LOG_FORMAT", "text").strip().lower()
    #: Where uploaded documents live when no object store is configured (app.storage).
    storage_dir: str = os.getenv("STORAGE_DIR", "./data/files").strip() or "./data/files"
    #: Invoices by email: each restaurant gets {slug}@INBOUND_DOMAIN; deliveries POST to the inbound
    #: webhook with X-Inbound-Secret. Unset, the address isn't offered.
    inbound_domain: str | None = os.getenv("INBOUND_DOMAIN", "").strip() or None
    inbound_secret: str | None = os.getenv("INBOUND_SECRET", "").strip() or None

    # Pause between the scripted provider's steps, so a demo run streams
    # visibly instead of finishing before the page can show it.
    scripted_step_delay_seconds: float = float(os.getenv("SCRIPTED_STEP_DELAY_SECONDS", "0"))

    # Public demo: replace the data with the demo restaurant at startup and
    # again on this schedule (UTC), so visitors' changes never accumulate.
    demo_mode: bool = os.getenv("DEMO_MODE", "false").strip().lower() in ("1", "true", "yes")
    demo_reset_cron: str = os.getenv("DEMO_RESET_CRON", "0 4 * * *")

    # Where owners forward supplier invoices, shown on the Invoices page when
    # this site has a mailbox that feeds them in. Empty: forwarding isn't offered.
    invoice_forward_address: str | None = os.getenv("INVOICE_FORWARD_ADDRESS", "").strip() or None

    # Reading a photo or PDF of an invoice into lines (app.prices.extract). Claude with an Anthropic
    # key; a vision model on the OpenAI-compatible API with LLM_API_KEY (Gemini's free tier reads
    # photos); with no model at all, PDFs that carry text are read by rule. INVOICE_READER_MODEL
    # pins the reading model; unset, Claude uses claude-sonnet-4-5 and the openai reader LLM_MODEL.
    invoice_reader_model: str | None = os.getenv("INVOICE_READER_MODEL", "").strip() or None

    # The weekly digest mail. Sent with Resend when RESEND_API_KEY is set, printed to the log
    # otherwise. DIGEST_CRON is read on each restaurant's own clock (default Monday 6am).
    resend_api_key: str | None = os.getenv("RESEND_API_KEY", "").strip() or None
    # Or any mailbox over SMTP, which costs nothing: a Gmail address with an app password
    # (smtp.gmail.com, 587, the address, the 16-character password), Outlook, Fastmail, a
    # host's own. Resend is used when both are set. Port 465 means TLS from the start.
    smtp_host: str | None = os.getenv("SMTP_HOST", "").strip() or None
    smtp_port: int = int(os.getenv("SMTP_PORT", "587") or 587)
    smtp_user: str | None = os.getenv("SMTP_USER", "").strip() or None
    smtp_password: str | None = os.getenv("SMTP_PASSWORD", "") or None
    # The sender. Unset, the SMTP mailbox when there is one: Gmail rewrites anything else anyway.
    mail_from: str = os.getenv("MAIL_FROM", "").strip() or (
        f"FullHouse <{os.getenv('SMTP_USER', '').strip()}>" if os.getenv("SMTP_USER", "").strip() else "FullHouse <digest@fullhouse.app>"
    )
    digest_cron: str = os.getenv("DIGEST_CRON", "0 6 * * 1").strip()
    # Lets an outside scheduler (Render cron, GitHub Actions) call POST /api/v1/prices/digest/cron.
    cron_secret: str | None = os.getenv("CRON_SECRET", "").strip() or None
    # Where links in mail point: PUBLIC_URL, else the address Render gives the service
    # (RENDER_EXTERNAL_URL), else the first FRONTEND_ORIGIN.
    public_url: str = (os.getenv("PUBLIC_URL", "").strip() or os.getenv("RENDER_EXTERNAL_URL", "").strip()).rstrip("/")

    # Bill.com (v3 API). All four set: "Connect Bill.com" pulls bills. BILLCOM_ENVIRONMENT is
    # sandbox or production. BILLCOM_WEBHOOK_SECRET guards POST /api/v1/prices/billcom/webhook.
    billcom_username: str | None = os.getenv("BILLCOM_USERNAME", "").strip() or None
    billcom_password: str | None = os.getenv("BILLCOM_PASSWORD", "").strip() or None
    billcom_org_id: str | None = os.getenv("BILLCOM_ORG_ID", "").strip() or None
    billcom_dev_key: str | None = os.getenv("BILLCOM_DEV_KEY", "").strip() or None
    billcom_environment: str = os.getenv("BILLCOM_ENVIRONMENT", "sandbox").strip().lower()
    billcom_webhook_secret: str | None = os.getenv("BILLCOM_WEBHOOK_SECRET", "").strip() or None

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    @property
    def mail_configured(self) -> bool:
        return bool(self.resend_api_key) or self.smtp_configured

    @property
    def billcom_configured(self) -> bool:
        return all((self.billcom_username, self.billcom_password, self.billcom_org_id, self.billcom_dev_key))

    @property
    def link_base(self) -> str:
        return self.public_url or (self.frontend_origins[0].rstrip("/") if self.frontend_origins else "")

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

    # Whether new restaurants can sign up from the landing page: each gets an
    # owner account and a restaurant of its own, kept apart from every other.
    # Off by default, which makes this one restaurant's private site: whoever
    # signs up first is its owner, and after that nobody can. Either way a
    # restaurant's team joins it with invite links from the Profile page.
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
