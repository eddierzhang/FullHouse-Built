# Full House v2 build: task list

The build spec ("Full House v2 build spec", Oct 27 2026) is written for a pnpm/Next.js/Drizzle/pg-boss stack.
This repository is FastAPI + SQLAlchemy + React/Vite, running on SQLite or Postgres in one container, and it
already does several things the spec treats as separate work. The spec's substance is implemented here on the
existing stack. These are the standing translations; each phase below says what it adds.

## How the spec maps onto this codebase

| Spec | Here |
|---|---|
| pnpm monorepo, Next.js app, server actions | FastAPI routes + React pages; one deploy |
| Drizzle schema, RLS, `SET LOCAL app.restaurant_id` | SQLAlchemy models with `restaurant_id` on every tenant table; every query goes through the scope helpers (`app.restaurant.inventory.scope_for`, `prices.routes._scope`); the separation and schema-driven invariant tests stand in for RLS |
| pg-boss queues, `apps/worker` | `app/jobs`: a Postgres/SQLite `jobs` table with singleton keys, leasing and backoff; an in-process worker thread by default (free single-container hosting), `python -m app.worker` for a separate process |
| `events` append-only log | `app/events`: an `events` table and `emit()`; subscribers enqueue jobs |
| `audit_log` | `change_log` (app.audit), already automatic on every write |
| `proposals` with `kind` | `agent_actions` with `action_type`; appliers carry out approvals |
| `findings` | digest cards (sections) in `price_digests.cards`; new finding types are new sections |
| `supplier_item_mappings` | `price_item_aliases` (no price column, by test) |
| `canonical_items` | `price_items` |
| `review` queue | Recommendations (proposals) and Questions (mappings), keyboard rules kept |
| `restaurant_flags` | `restaurant_flags` table, `app/flags.py`, owner route and CLI |
| S3/MinIO | `app/storage.py`: local disk by default, S3 when configured |
| Claude structured output | `app/prices/extract.py` readers: Claude, an OpenAI-compatible model, or rules when there is no key |
| roles owner / manager / bookkeeper | owner / admin / bookkeeper (admin = manager); member = staff |
| Botanica scenario | the demo restaurant and its fixture invoices |

Kept on purpose against the spec's "do not build" list, because they already exist and were asked for: the
operations dashboards, inventory with stock on hand and deliveries, purchase orders received into stock, the
schedule. Nothing new is added to those areas. No inventory counts, par levels, autonomous ordering, POS writes,
payments or payroll pushes are built.

Git: the working tree before v2 is committed on `customer-service-requests` and tagged `v1-mvp` locally. Each phase
is a branch `v2/phase-<x>-<name>` merged back when its acceptance test passes. Nothing is pushed unless asked.

## Invariants (a test for each; every phase keeps them green)

- [x] I1 The agent writes only proposals: a session guard raises on any other tenant-table write inside a tool call; the forbidden-write test iterates every tenant table.
- [x] I2 Every tenant table has `restaurant_id` and an index that leads with it (schema-driven test); scope helpers on every route (separation test).
- [ ] I3 Every action lands in `change_log` with before/after; connector syncs log as `system`.
- [ ] I4 Every outside write (webhook, sync, inbound mail, cron) is idempotent on an external key (replay tests).
- [x] I5 No price on `price_item_aliases`; no "market" price anywhere; agreed prices are restaurant-scoped and come from the restaurant's documents.
- [ ] I6 The digest is the product: new modules add cards or one line; no new dashboards for them.
- [ ] I7 Nothing new requires inventory counts.
- [ ] I8 Humans send: no agent path emails a supplier, sends a PO, pushes to payroll or calls a connector write.
- [ ] I9 Every extracted field carries a confidence; under 0.8 or failing validation goes to review.
- [ ] I10 Pending-proposal caps per kind per restaurant per week (mappings 5, recipes 5, purchase orders 3, calendar items 10); beyond the cap, held silently and logged.

## Phase 0: platform (branch `v2/phase-0-platform`, flag: none; always on)

- [x] 0.1 `restaurant_flags` table, `app/flags.py`, `GET/PUT /api/v1/restaurant/flags` (owner), `python -m app.flags enable <email-or-id> <flag>`.
- [x] 0.2 `events` table and `app/events.py` (`emit`, subscribers → jobs).
- [x] 0.3 `jobs` table and `app/jobs` (enqueue with singleton key; worker loop with leasing, retries, backoff, lease recovery; queues ingest, extract, normalize, detect, digest, sync, forecast, agent; in-process worker thread; `python -m app.worker`).
- [x] 0.4 Agent runs execute as `agent` jobs; `agent_runs` gains tokens_in, tokens_out, cost_usd; at most 2 concurrent runs per restaurant; the web process only enqueues.
- [x] 0.5 `webhook_inbox`; the Bill.com webhook writes raw and enqueues a sync job; the same webhook twice yields one invoice.
- [x] 0.6 `app/connectors`: `Connector` interface, `TenantContext`, `connector_providers` (global) and `connections` tables, credentials encrypted at rest with decryption only in the worker module, Bill.com behind the interface unchanged.
- [x] 0.7 `app/storage.py`: local disk by default, S3 when configured, keys `{restaurant_id}/{doc_type}/{uuid}`, signed 15-minute URLs.
- [x] 0.8 Agent write guard (I1) and the schema-driven invariant tests (I2, I5).
- [x] 0.9 Structured JSON logs carrying restaurant_id / job_id / run_id; `/healthz`; `/api/v1/admin/ops` (superusers) with plain tables.
- [x] 0.10 Acceptance: the existing suites pass with the worker executing (backend 569 green; browser suite 43 green with runs through the queue); a worker killed mid-run and restarted completes the run with no duplicate rows (`test_a_lease_left_by_a_dead_worker_is_recovered_and_the_job_runs_once_more`); the same Bill.com webhook twice yields one invoice (`test_the_same_billcom_webhook_twice_yields_one_pull_and_one_invoice`).

Notes. The agent's own summaries of customer requests (`customer_service_reviews`, `customer_action_items`) are
allowed through the write guard beside the conversation: they are the agent's words for managers to read, not a
change to operations. Cancellation of a run is in-process; a separate worker process does not see a cancel asked
for on the web process.

## Phase A: connect and prime cost (branch `v2/phase-a-connect`, flag `v2_connect`)

- [ ] A.1 Tables `connections` (used), `sales_days`, `sales_items`, `labor_days`, `pnl_weeks`, `csv_profiles`.
- [ ] A.2 `csv` adapter: Toast, Square and Clover exports by header sniffing; a generic column mapper saved per restaurant.
- [ ] A.3 `resend_inbound`: per-restaurant inbound address, sender allow-list, attachments become documents and invoices.
- [ ] A.4 `square` (OAuth, orders with cursor pagination, webhook) and `gusto` (OAuth, pay periods allocated to days) adapters with golden-file tests; `qbo` push behind the interface (never pull).
- [ ] A.5 `pnl` job weekly before the digest; `pnl_weeks`; the prime-cost line at the top of the digest, or the connect prompt when inputs are missing.
- [ ] A.6 Connections page (providers from `connector_providers`, OAuth, CSV drop zone with mapping preview, inbound address with copy, last sync and one-line errors).
- [ ] A.7 Acceptance: 14 days of Square-format sales CSV and a payroll CSV, one invoice by inbound mail, `pnl` within 0.1 point of hand-computed fixtures, "Prime cost" first in the digest, and the connect prompt after one labor day is removed.

## Phase B: documents, agreed prices, recipes, menu margin (branch `v2/phase-b-documents`, flag `v2_documents`)

- [ ] B.1 `documents` registry with doc-type classification (confidence; under 0.9 the reviewer picks) and schemas: price_list, recipe, license/permit/inspection/warranty.
- [ ] B.2 `agreed_prices`, `agreed_price_check` on normalized invoices → `credit_owed` cards with dollar impact citing both the line and the price list.
- [ ] B.3 `recipes`, `recipe_lines`, `plate_costs`, weekly `recipes/cost` (sub-recipes to depth 4), `menu` findings (`menu_margin` section).
- [ ] B.4 Tools `propose_recipe`, `propose_agreed_price`, `propose_menu_reprice` with caps; appliers for the new proposal kinds; `recipe_onboarding` workflow.
- [ ] B.5 Pages: Documents, Recipe, the agreed-price sentence on the item page, renderers per proposal kind in Recommendations.
- [ ] B.6 Acceptance: the price-list → agreed price → credit_owed chain; Square CSV "Kale Caesar" → recipe proposal → plate cost citing price points; a 10% kale rise → menu_margin finding → menu_reprice proposal with no POS write; the agent cannot write recipes directly.

## Phase C: purchase orders, forecasting, prep, workflows (branch `v2/phase-c-orders`, flag `v2_orders`)

- [ ] C.1 `purchase_orders` gains `draft` and `sent` with lines (`po_lines`); `po_draft` workflow (8-week medians, ≥ 4 invoices); `propose_purchase_order` with cap 3; "Send to rep" is a human button (mail stub), sets `sent`; short delivery matches invoice lines to open PO lines.
- [ ] C.2 `forecasts`, `forecast_errors`, nightly baseline forecast (weighted same-weekday mean × trend, ±1 MAD), the accuracy line in the digest.
- [ ] C.3 `prep_sheets`, `prep` workflow and tool; a printable page on approval.
- [ ] C.4 `workflows` table and runner on each restaurant's clock; default rows at onboarding.
- [ ] C.5 Acceptance per the spec: PO median, send as manager, cost recorded, short delivery, forecast within ±1 MAD, forecast error row and digest line, prep quantities, agent cannot set `sent`, bookkeeper cannot send.

## Phase D: calendar and assets (branch `v2/phase-d-calendar`, flag `v2_calendar`)

- [ ] D.1 `calendar_items`, `assets`, `referrals`; daily `calendar` job; `propose_calendar_item`.
- [ ] D.2 Calendar page (a list), "Coming due" digest section, proof attachment, overdue styling.
- [ ] D.3 Acceptance per the spec.

## Phase E: scale proof (branch `v2/phase-e-scale`)

- [ ] E.1 Load script: 1,000 synthetic restaurants × 50 invoices on CSV connectors; the Monday pipeline on 4 workers; measured figures printed.

## Completion output

- [ ] Commands to start, migrate, seed, test, load-test, start web and a worker.
- [ ] Every place a key or credential is needed.
- [ ] Flag names per phase and the command to enable one.
- [ ] Phase E figures.
- [ ] The list of invariant tests and what each covers.
