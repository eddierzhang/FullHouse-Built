# Full House v2: completion output

What the build spec asked for at the end, as it stands in this repository. The task list with each phase's notes is
[V2_TASKS.md](V2_TASKS.md).

## 1. Commands

Everything runs from the repository root with Python 3.11+ and Node 22. There is no Docker locally; the image is built
by CI and by Render.

```bash
# Once on a new machine: venv, pip, npm, and backend/.env from the example.
python dev.py setup

# Database and migrations (SQLite by default; set DATABASE_URL for Postgres, e.g. a Neon URL).
cd backend && .venv/bin/python -m alembic upgrade head && cd ..

# Seed the sample restaurant (the "Botanica" of this repo is the demo restaurant: eight weeks of invoices, sales,
# labor, two orders in flight, every v2 flag on).
cd backend && DEMO_MODE=true .venv/bin/python -c "from app.bootstrap import setup; setup(); from app.demo import reset_now; print(reset_now())" && cd ..

# The full test suite: backend (pytest), frontend type-check and build, then the browser suite.
python dev.py test
python dev.py e2e

# The load test (Phase E): synthetic restaurants on CSV connectors, the Monday pipeline on N worker threads.
cd backend && .venv/bin/python scripts/load_test.py --restaurants 1000 --invoices 50 --workers 4 && cd ..

# The web app with worker threads inside it (the default), API on :8000 and the frontend on :5173.
python dev.py dev

# One worker process beside a web app that has WORKER_INPROCESS=false.
cd backend && WORKER_INPROCESS=false .venv/bin/uvicorn app.main:app --port 8000 &
cd backend && .venv/bin/python -m app.worker
```

The free public demo is `python dev.py demo` (one process on :8080, no key, resets on each start).

## 2. Where a key or credential is needed

All optional. With none of them the app runs the rule-based assistant, reads PDFs by rule, prints mail to the log
and offers CSV connectors only.

| Credential | Variable(s) | What it turns on |
|---|---|---|
| Anthropic API key | `ANTHROPIC_API_KEY` (`CLAUDE_READER_MODEL` optional) | Claude for the assistant, photo and PDF reading, document classification and structured reading |
| Any OpenAI-compatible key (Gemini free tier works) | `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL` | The same, through that API |
| Bill.com sandbox or production | `BILLCOM_USERNAME`, `BILLCOM_PASSWORD`, `BILLCOM_ORG_ID`, `BILLCOM_DEV_KEY`, `BILLCOM_ENVIRONMENT`, `BILLCOM_WEBHOOK_SECRET` | Bills pulled as invoices; the webhook |
| Resend (sending) | `RESEND_API_KEY`, `MAIL_FROM` | Monday digests and purchase orders by mail (SMTP with `SMTP_HOST/PORT/USER/PASSWORD` works too, e.g. a Gmail app password) |
| Resend inbound domain, or any mail provider that can POST JSON | `INBOUND_DOMAIN`, `INBOUND_SECRET` | Each restaurant's `{slug}@INBOUND_DOMAIN` address for invoices and documents |
| Square sandbox app | `SQUARE_APP_ID`, `SQUARE_APP_SECRET`, `SQUARE_ENV` | Sales by day and item by OAuth |
| Gusto developer app | `GUSTO_CLIENT_ID`, `GUSTO_CLIENT_SECRET`, `GUSTO_ENV` | Labor by day by OAuth |
| QuickBooks sandbox app | `QBO_CLIENT_ID`, `QBO_CLIENT_SECRET`, `QBO_ENV` | Pushing checked invoices as bills |
| Object storage (S3-compatible, MinIO) | `S3_BUCKET`, `S3_ENDPOINT_URL` and the AWS credential variables boto3 reads; `STORAGE_DIR` without it | Where documents and files live |
| Credential encryption key | `SECRET_KEY` | Connector credentials and two-step secrets at rest; set once, never change |
| Cron secret | `CRON_SECRET` | The outside scheduler's call to the Monday mail route |
| OTLP endpoint | not built; logs are structured JSON with `LOG_FORMAT=json` | |
| Sentry | `SENTRY_DSN` | Error alerts |
| Ops page | `SUPERUSER_EMAILS` | `/api/v1/platform/ops` |

Square, Gusto and QuickBooks adapters were built against golden fixtures and stubbed APIs; none has been exercised
against a live sandbox from here. Clover and Toast are listed as providers and not built.

## 3. Flags per phase

| Phase | Flag | Switches on |
|---|---|---|
| 0 | none | The platform is always on |
| A | `v2_connect` | Connections page, CSV and OAuth connectors, inbound mail, prime-cost line |
| B | `v2_documents` | Documents, agreed prices, recipes and plate cost, Menu margin section |
| C | `v2_orders` | Order drafts, forecast, prep sheets, short deliveries against orders |
| D | `v2_calendar` | Calendar, equipment, Coming due section |

Owners switch them under Profile › Features. From a shell:

```bash
cd backend && .venv/bin/python -m app.platform.flags enable owner@example.com v2_connect
cd backend && .venv/bin/python -m app.platform.flags list owner@example.com
```

## 4. Phase E figures

Measured on this laptop, SQLite, 4 worker threads in one process (`scripts/load_test.py --restaurants 1000 --invoices 50 --workers 4`):

| Figure | Measured | Spec's bar |
|---|---|---|
| Monday pipeline for 1,000 restaurants (3,000 jobs: extract, pnl, detect and digest) | 58 s wall-clock; rendering every digest 2 s more | 20 min |
| p95 extraction job latency (rule-based PDF reader) | 0.04 s | 30 s |
| Cross-tenant rows in a random sample of 100 digests | 0 of 800 cards | 0 |
| Agent cost per restaurant per week | $0.00 with the rule-based provider (no model billed); with a model it is the sum of `agent_runs.cost_usd`, shown on the ops page | $0.50 |

With a model key the extraction figure becomes the model's latency, and the cost figure the model's list price for
the week's runs; neither was measured here because no key is set.

## 5. Invariant tests

| Test | Covers |
|---|---|
| `tests/test_invariants.py::test_the_agent_cannot_write_any_tenant_table_through_a_tool` | I1: every tenant table, through the flush-time guard |
| `tests/test_invariants.py::test_the_registry_wraps_every_tool_in_the_guard` | I1: every tool the agent is offered |
| `tests/test_invariants.py::test_every_tenant_table_has_restaurant_id_and_an_index_that_leads_with_it` | I2: schema-driven, every tenant table |
| `tests/test_invariants.py::test_tables_that_mean_a_restaurant_are_tenant_tables` | I2: no table left unscoped by accident |
| `tests/test_restaurant_separation.py` | I2: two restaurants, every route |
| `tests/test_invariants.py::test_no_price_is_stored_on_the_alias_table` | I5: `price_item_aliases` |
| `tests/test_invariants.py::test_no_route_or_page_code_decrypts_credentials` | security: `decrypt` only in the worker's registry |
| `tests/test_invariants.py::test_no_agent_code_imports_the_mail_client_or_a_connector_write` | I8: `app/agents/**` |
| `tests/test_orders_v2.py::test_acceptance_order_draft_send_short_delivery_forecast_prep_and_roles` (last block) | I8: the agent cannot set an order to sent; a bookkeeper cannot send one |
| `tests/test_platform.py::test_the_same_billcom_webhook_twice_yields_one_pull_and_one_invoice` | I4: Bill.com webhook replay |
| `tests/test_connect.py::test_an_invoice_forwarded_to_the_restaurants_address_becomes_an_invoice_once` | I4: inbound mail replay |
| `tests/test_orders_v2.py::test_default_workflows_are_made_at_setup_and_the_minute_tick_queues_each_once_per_period` | I4: the same cron tick twice |
| `tests/test_connect.py::test_a_square_connection_syncs_through_the_worker_with_a_cursor_and_never_doubles` | I4: sync re-run with the same cursor |
| `tests/test_platform.py::test_a_lease_left_by_a_dead_worker_is_recovered_and_the_job_runs_once_more` | Phase 0: a worker killed mid-job |
| `tests/test_documents.py::test_an_unsure_classification_waits_for_the_reviewer_who_picks_the_type` | I9: confidence under the bar goes to review |
| `tests/test_documents.py::test_the_owner_sets_an_agreed_price_from_the_item_page_as_a_proposal` | I10: the weekly cap (agreed prices, 10) |
| `tests/test_prices.py::test_five_questions_a_week_and_the_rest_wait_in_dollar_order` | I10: mappings, 5 |
| `tests/test_audit*.py`, `tests/test_revert.py`, `tests/test_appliers.py` | I3: every change in `change_log` with before and after |
| `tests/test_permissions.py::test_every_write_is_for_managers_unless_staff_are_meant_to_use_it` | roles on every write route |
