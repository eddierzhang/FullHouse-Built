# Development log

How FullHouse was built, phase by phase, with the decisions and dead ends along the way. The
original plan is in [design/original-plan.txt](design/original-plan.txt); the release summary is in
[CHANGELOG.md](../CHANGELOG.md).

## Phase 0 — Scaffolding (done)

**Backend** (`backend/`):
- FastAPI app skeleton (`app/main.py`, `app/config.py`), sync SQLAlchemy 2.0 session
  (`app/db/session.py`), matching the `chineseappdemo` house convention.
- Alembic wired up (`alembic/`, `alembic.ini`) — `target_metadata` pulls from both
  `app.restaurant.models` and `app.agents.models`. Initial migration
  (`alembic/versions/efd4e0aab222_initial_schema.py`) creates all 10 tables; `main.py`
  no longer calls `create_all` — schema is Alembic's job now (`alembic upgrade head`).
- `/health` endpoint verified.

**Frontend** (`frontend/`):
- Vite + React 19 + TypeScript skeleton, mirroring `chineseappdemo/frontend`'s tooling
  (no framework beyond React, plain CSS, `VITE_API_BASE` env var).
- `src/api/client.ts` — typed fetch wrapper; `App.tsx` does a live health check + fetches
  the agent roster from the backend as a connectivity smoke test.
- Verified: `npx tsc -b` type-checks clean, `npm run dev` serves on :5173, backend CORS
  confirmed to allow it.

## Phase 1 — Agent control plane MVP (built ahead of Phase 0, since done)

Built before Phase 0 was fully wired up (Alembic/frontend came after, retroactively):
- Restaurant domain models + CRUD REST API (menu, orders, inventory, suppliers, staff, shifts).
- Agent platform models: `AgentDefinition`, `AgentRun` (with `parent_run_id` for the run
  tree), `AgentEvent`, `AgentAction` (approval queue).
- Real Boss + 5 subagent orchestration via the Anthropic `tool_runner`/`@beta_tool` API
  (`app/agents/runner.py`, `orchestrator.py`, `registry.py`, `tools/*.py`). Boss delegates
  via `delegate_to_*` tools that recursively run a subagent to completion and return its
  summary.
- Seed script (`scripts/seed_data.py`) with sample data + all 6 agent definitions.
- Verified end-to-end including the failure path (no `ANTHROPIC_API_KEY` in this sandbox
  correctly produces a `failed` run with a persisted error, not a crash).

**Known simplifications vs. the full plan** (`docs/design/original-plan.txt`): no WebSocket live streaming yet
(poll `GET /agents/runs/{id}`), no APScheduler cron wiring, and a pending `AgentAction`
doesn't literally pause its run — the run finishes normally and the proposal just sits in
the approvals queue. Frontend has no agent-specific pages yet (Agents list, run detail,
approvals inbox) beyond the Phase 0 connectivity check.

## Phase 2 — Change tracking (done)

Approving an `AgentAction` used to flip a status column and stop: the reorder never
restocked anything, the shift never reached the schedule. Three pieces, in dependency
order, since an audit trail over changes that never happen is worth nothing.

**1. Appliers** (`app/agents/appliers.py`): `action_type` -> handler, covering all four
proposal types (`inventory_reorder`, `purchase_order`, `shift_change`, `menu_change`).
Appliers never commit; `POST /agents/actions/{id}/approve` owns one transaction covering
the domain mutation, the status flip and the audit rows, so a failure can't leave an
action marked approved whose effect never landed. A proposal that can't be carried out
returns 422 and stays pending rather than silently no-op'ing.

**2. `change_log` table** (`app/audit/models.py`, migration `a649971e1021`): one row per
mutation — entity, operation, before/after, changed fields, actor, and the agent run and
action it traces back to. No foreign keys: an audit row has to outlive what it describes.
`agent_actions` also gained `applied_result`, recording what an approval actually did.

**3. Automatic capture** (`app/audit/listener.py`): a session-level listener, not
hand-placed log calls, so a change is recorded whichever path made it. Updates and deletes
are captured on `before_flush` (the only point where the prior value is still knowable)
and inserts on `after_flush` (the only point where a new row knows its primary key).
Attribution rides in a ContextVar set by `AuditActorMiddleware` for HTTP callers and by
`runner.py` for agent runs — so an operator approving a proposal records as a human actor
against the agent run that proposed it.

29 tests in `backend/tests/` (`pytest`), covering capture, attribution, appliers,
transaction rollback, and the migration up and down.

**Known gaps**: `X-Actor-Id` is a stand-in until real auth exists — anyone can claim any
identity. Approving a reorder books the goods as received immediately, since there is no
purchase-order lifecycle in the schema. `promotion` menu changes can't be applied at all
(no promotion model). Read endpoints for the history, and revert, are not built yet.

## Phase 3 — History API and revert (done)

**Read API** (`app/audit/routes.py`, `crud.py`, `schemas.py`):
- `GET /api/v1/changes` — newest first, paginated (`limit`/`offset`, `total` is unpaginated).
  Filters: `entity_type`, `entity_id`, `operation`, `actor_type`, `actor_id`, `agent_run_id`,
  `agent_action_id`, `since`, `until`. Filtering by `agent_run_id` answers "what did this
  agent run actually change", across every table it touched.
- `GET /api/v1/changes/entity/{entity_type}/{entity_id}` — one record's timeline, oldest first.
- `GET /api/v1/changes/{id}` — a single change.

**Revert** (`app/audit/revert.py`): `POST /api/v1/changes/{id}/revert` applies the inverse —
insert becomes a delete, update restores the `before` values, delete re-creates the row with
its original key. Three things worth knowing:

- A revert is *appended*, never a rewrite. It replays as an ordinary mutation, so the
  listener records it as its own change and the timeline shows both the original and the undo.
  `change_log.note` (previously always null) now carries "Revert of change N" or a caller's
  own reason, via a `note_context` alongside the actor context.
- It refuses (409) when the entity has changed since the recorded change, rather than
  silently discarding newer edits. `force: true` overrides. This also makes a double revert
  fail rather than thrash.
- Values are coerced back on the way out: the log stores dates and times as ISO strings, so
  restoring a `Shift` writes real `date`/`time` objects, not their string forms.

**Also fixed**: SQLite ignores foreign keys unless asked per connection, and this app never
asked — deleting a menu item silently orphaned the order lines recording its sales. Reverting
an insert deletes rows, so `app/db/session.py` now sets `PRAGMA foreign_keys=ON` and a revert
that would break a reference returns 409 instead of quietly corrupting history. This is an
app-wide behaviour change: writes that previously succeeded against a dangling foreign key
now fail loudly.

51 tests (`cd backend && pytest`).

**Still open**: no auth, so `X-Actor-Id` remains self-asserted. Revert lineage is recorded in
the free-text note rather than a `reverted_by_change_id` column, so "has this been reverted"
is not directly queryable. No UI for any of it — the frontend's ActionCenter still only
approves and rejects.

## Phase 4 — Pluggable model backend (Ollama) (done)

Agents can now run on a local Ollama model instead of the Anthropic API, switched by one
env var. `LLM_PROVIDER=ollama` repoints the whole platform.

**No tool changes were needed.** `@beta_tool` objects already expose `name`, `description`,
a real JSON Schema in `input_schema`, and `call(dict)` — exactly what any tool-calling API
wants — so the five tool modules and `orchestrator.py` are untouched. Only the loop that
drives them moved.

- `app/agents/providers/base.py` — `LLMProvider` interface, `ProviderResult`,
  and `to_function_schema()` which renders a `@beta_tool` as an OpenAI-style declaration.
- `anthropic_provider.py` — the original `tool_runner` path, unchanged in behaviour.
- `ollama_provider.py` — Ollama has no agentic helper, so the loop lives here: call
  `/api/chat`, execute the tools it asks for, feed results back as `role: tool` messages,
  repeat until it answers without calling one. Handles arguments arriving as either an
  object (Ollama) or a JSON string (OpenAI-compatible paths), truncates tool output so a
  big result can't blow a small model's context, and caps rounds so a model that loops on
  one call fails loudly rather than forever.
- `runner.py` now asks `get_provider()` for a backend and emits a `log` event naming the
  provider and model actually used.

`AgentRun.tokens_used` was another declared-but-never-written column; providers now report
usage and it is populated.

**Verified against a live local model** (`qwen2.5:7b`): a subagent run listed low stock and
queued a real reorder proposal (2119 tokens), and a Boss run delegated to the inventory
subagent and relayed its summary, queuing 3 proposals. Delegation works unchanged because
the delegate tools are `@beta_tool`s like any other.

73 tests (`cd backend && pytest`), including the Ollama loop exercised over real HTTP
against a stub server, and the Anthropic adapter against a stubbed SDK client.

**Known gaps**: the model must support tool calling (`ollama show <model>` must list
"tools") — one that doesn't will silently just talk instead of acting. `OLLAMA_MODEL`
applies to every agent, ignoring each `AgentDefinition.model`, since those are seeded with
Claude ids. Small models are noticeably worse at multi-step delegation than Opus.

## Phase 5 — Employee management page (done)

**Backend.** "Manage" needs more than the create-only API had: staff and shifts could be
added but never corrected or removed. Added `PATCH`/`DELETE` for both
(`app/restaurant/routes.py`), with `StaffUpdate`/`ShiftUpdate` partial-update schemas so a
caller sends only what changed. Deleting someone who still has shifts returns 409 naming the
count, rather than letting the enforced foreign key raise something opaque. Every edit and
removal flows through the change log like anything else, so a deleted shift is revertible.

**Frontend.** New `EmployeesPage.tsx` reachable at `#employees` (sidebar → Restaurant → Team):
- Roster with inline editing, add, and remove. Shows each person's shift count, flagging
  anyone with none in coral — the coverage gap the agent watches for.
- Schedule grouped by day, with add and remove.
- A banner when the Employee Management agent has scheduling proposals pending, linking to
  the action center.

Routing is a 20-line `useHashRoute` hook rather than a router dependency — the sidebar
already navigated by `#anchor`, and the app's house style is "no framework beyond React".
Unknown hashes fall through to the dashboard, so the existing `#tasks`/`#agents` anchors
still scroll as before. Sidebar highlighting now follows the route.

84 backend tests; frontend builds clean (31 modules).

**Known gaps**: no auth still, so anyone can edit the roster. Shift editing is remove-and-re-add
in the UI (the `PATCH /shifts/{id}` endpoint exists and is tested, but nothing calls it yet).
No week/calendar view — shifts are a flat list grouped by date.

## Phase 6 — Marketing / Product page (done)

The marketing agent "analyzes top/slow sellers and proposes promotions or menu changes", but
the frontend had no way to see sales per dish and no way to edit the menu — the API was
create-only and exposed no per-item sales figures at all.

**Backend.**
- `GET /api/v1/restaurant/menu-performance` — per dish: units sold, revenue, unit margin,
  margin %, and gross profit contributed. Aggregated in SQL (`crud.menu_performance`) rather
  than by walking every order line in Python the way `top_selling_items` does. Counts
  completed orders only.
- `PATCH /menu-items/{id}` — price, cost, description, category, availability, with
  non-negative validation.
- `DELETE /menu-items/{id}` — allowed only for a dish that has never sold. Once it appears on
  an order its row is part of the sales history the profit and marketing agents read, so the
  endpoint returns 409 naming the order-line count and points at `is_available` instead.

**Frontend.** `MarketingPage.tsx` at `#marketing` (sidebar → Restaurant → Menu):
- Menu table sortable by units, profit, or margin, with a margin bar per dish and a hairline
  under each row showing its share of the best seller's volume.
- Inline price/description editing, an availability toggle, add-a-dish, and delete offered
  only where it is legal (never-sold items).
- "Promotion candidates" callout listing dishes selling at or below a quarter of the best
  seller — the slow movers the agent proposes promotions for.
- Banner when the agent has `menu_change` proposals pending.

95 backend tests; frontend builds clean (32 modules).

**Known gaps**: margins still use the hand-entered `cost` field, so they are only as good as
that number — the recipe/BOM gap means nothing ties a dish to the ingredients it consumes.
No revenue-over-time view; figures are lifetime totals. Category is editable on create but
not in the inline editor.

## Phase 7 — Profit monitor page (done)

**Backend.** `GET /api/v1/restaurant/profit-summary?days=N` (1–365, default 30) — revenue,
cost of goods, gross profit and margin over a trailing window, plus average order value,
capital tied up in stock, a continuous daily series, and breakdowns by menu category and
order channel. COGS comes from each line's menu-item cost.

Found and fixed a real bug while building it: the daily series bucketed by `date.today()`
(local) while `created_at` is stored in UTC, so the current day's orders counted in the
headline total but had no slot in the series — $360 revenue against a chart summing $288.
Now UTC throughout, with a test asserting the daily rows reconcile to the headline.

**Frontend.** `ProfitPage.tsx` at `#profit` (sidebar → Restaurant → Profit):
- Five stat tiles, gross profit as the hero.
- Daily revenue vs gross profit as a two-series line chart on one axis (both are dollars —
  never a second y-scale), with a hover crosshair and tooltip, endpoint-only direct labels,
  and a Chart/Table toggle so the numbers are reachable without reading the plot.
- Category contribution as horizontal bars, one hue (bar length already encodes magnitude;
  a value-ramp would double-encode it).
- 7/30/90-day window selector.

Chart colors are **not** the app's `--green`/`--coral`: that pair collapses under protanopia
(adjacent CVD ΔE 5.6) and `--green` is below the chroma floor, so it reads gray as a thin
mark. `#1f6f9e`/`#d2652b` validates clean — protan ΔE 18.2, normal 28.0, both ≥3:1 on white.

The channel panel renders a sentence rather than a chart while only one channel exists; a
one-bar bar chart is not a chart.

105 backend tests; frontend builds clean (33 modules).

**Known gaps**: gross profit is only as good as the hand-entered `cost` per dish — the
recipe/BOM gap again, now stated on the page itself. No labour costs, so this is gross not
net. The dashboard's "Tonight's forecast" panel is still hardcoded demo numbers (it is
labelled "Illustrative") and is not wired to this data.

## Phase 8 — Supply chain page (done)

**Backend.**
- `GET /api/v1/restaurant/supply-chain` — stock value grouped by supplier with each one's
  share (concentration risk), item and low-stock counts, lead times, plus a shortfall list
  ordered worst-lead-time-first. "Restock cost" prices each shortfall's `reorder_qty` at unit
  cost — what placing the order costs, not what is already on the shelf.
- `PATCH`/`DELETE /suppliers/{id}` — edit name, contact and lead time; delete refused (409)
  while items still reference the supplier.
- `PATCH /inventory-items/{id}` — reassign supplier, correct thresholds, adjust stock. All
  negative values rejected, all changes audited.

Items with no supplier are reported separately rather than folded into a total, and their
lead time is `null`, not `0` — there is no schedule to plan against, and a zero would read
as "arrives today".

**Frontend.** `SupplyChainPage.tsx` at `#supply` (sidebar → Restaurant → Suppliers):
- Four stat tiles: stock value, items needing reorder, cost to clear every shortfall,
  longest lead time on what is short.
- Supplier table with share-of-value bars, lead times, inline editing, and add/remove.
- "Needs reordering" list with supplier and lead time per item, and an inline supplier
  picker for anything unsourced.
- Callouts for concentration risk (one supplier above 60% of stock value) and unsourced
  items. Banner for pending `purchase_order` proposals.

The seeded data makes the concentration callout real: Sysco Foods holds 91.1% of stock value.

119 backend tests; frontend builds clean (34 modules).

**Known gaps**: no purchase-order lifecycle, so lead times are reference information rather
than a delivery schedule — approving a reorder still books goods as received immediately.
No consumption rate either (the recipe/BOM gap), so "days of cover" cannot be computed and
is deliberately not shown.

## Phase 9 — Background runs with live streaming, and the recipe model (done)

### Background execution + SSE

`POST /agents/runs` used to call `execute_run` inline, holding the HTTP request open for
the whole run — minutes once the Boss starts delegating. Runs now go to a thread pool
(`app/agents/executor.py`) and the caller gets a queued run back in ~0s (202 Accepted).

- `app/agents/broker.py` — in-process pub/sub. Runs execute on worker threads (SQLAlchemy,
  the Anthropic SDK and httpx are all synchronous), while SSE is served on the event loop,
  so publishing hops back with `call_soon_threadsafe`. The database stays the durable
  record; this only makes it live.
- `GET /agents/runs/{id}/stream` — SSE that replays recorded events before going live, so a
  listener arriving late still sees the whole run, with a heartbeat for idle connections.
- `POST /agents/runs/{id}/cancel` — cooperative: providers check between tool rounds, so a
  model call already in flight finishes rather than being torn off mid-write. A cancelled
  run is its own terminal state, not a failure.
- Startup reconciliation marks runs left `queued`/`running` by a dead process as failed.
  They cannot survive the process that was executing them, and left alone they claim to be
  in progress forever.

Frontend `RunsPage.tsx` at `#runs`: run history with the Boss delegation tree, and a live
log driven by `EventSource`. Launching a task now navigates there instead of blocking on a
summary that no longer arrives.

### Recipes

`recipe_items` (migration 95b108f65b06) links a dish to what one serving consumes.

- Menu performance and profit now use the recipe-derived cost where one exists, falling
  back to the typed-in figure otherwise, and report `cost_source` so the difference is
  visible rather than implied.
- `POST /orders` draws ingredients out of stock and refuses (409) rather than letting stock
  go negative, naming every shortfall. Dishes without a recipe consume nothing — the link
  is opt-in per dish.
- Days of cover on the supply chain page is finally real: consumption comes from what sold
  and the recipes behind it, compared against supplier lead time to flag anything that runs
  out before a delivery could land. Null where unknowable, never zero.
- `GET`/`PUT /menu-items/{id}/recipe`, with a `RecipeEditor` on the marketing page that
  reprices the margin live as you type.

Verified end to end: Margherita Pizza costed at $1.88 from ingredients (86.6% margin, versus
the $4.20 that had been typed in), selling 4 drew down flour, cheese and tomatoes, a 50-pizza
order was refused for tomatoes, a real agent run streamed 7 events live, and a Boss run was
cancelled mid-flight.

155 backend tests; frontend builds clean (36 modules).

**Known gaps**: the broker is in-process, so a second worker would not see the events —
Redis pub/sub is the next step if this is ever scaled out. Recipes are per-serving only, with
no yields, prep batches or waste factor.

## Phase 10 — Docker (done, not yet run under Docker)

`docker-compose.yml` runs the stack as four services: nginx serving the built frontend and
proxying `/api` to the backend, the FastAPI backend (migrates and seeds on start, SQLite on a
volume), Ollama, and a one-shot job that pulls the model. `DEPLOY.md` covers local use and a
free deployment on an Oracle Cloud Always Free VM.

Decisions worth knowing:
- The frontend image builds with `VITE_API_BASE=same-origin` and calls relative URLs, so one
  image works on any host with no CORS setup. An empty value was tried first and silently
  fell back to `localhost:8000` — `client.ts` now treats the explicit sentinel specially.
- The backend runs one uvicorn process; the in-process broker requires it.
- No entrypoint shell script: `core.autocrlf` would give it CRLF endings and break it in Linux.
- nginx disables buffering and raises the read timeout on `/api` so run streams work.

Verified without Docker (not installed on the dev machine): the container-mode frontend build
contains no `localhost:8000`, the backend startup chain migrates and seeds a fresh database and
passes its healthcheck, a restart does not duplicate data, and the compose file parses with the
intended dependency ordering. The images themselves have not been built.

## Phase 11 — Evals, scheduling, forecast, Postgres, E2E, CI, redesign (done)

- **Postgres** runs the full suite (196 on Postgres 16). Tests no longer touch the developer's
  `app.db` — startup recovery used to run against it whenever a test started the app.
- **Scheduling**: APScheduler drives `schedule_cron`; overlapping fires are skipped; promotions
  can end on their own and restore the regular price.
- **Forecast** from completed trading days, reporting method and confidence.
- **Evals** (`backend/evals/`): six scenarios through the real pipeline, scored on behaviour, with a
  do-nothing baseline (70%). qwen3.5:4b 97%, qwen2.5:7b 73%.
- **Scripted provider** for model-free CI and browser tests.
- **Playwright**: 8 end-to-end tests against an isolated stack.
- **CI** (GitHub Actions): SQLite and Postgres test jobs, build, E2E, and a Docker build + smoke test
  that streams a live run through nginx — the first real verification of the containers.
- **Frontend redesign**: one design system, grouped navigation where every item is a page, real figures
  in place of hard-coded dashboard numbers, and pages for agent schedules and inventory.
- **README** with architecture, engineering notes and eval results.

## Phase 12 — Agent speed (done)

Profiling showed generation was fast (~140 tok/s, sub-second warm calls); runs were slow because
Ollama reloaded the model on nearly every call. Another project sharing the same server loaded the
model with a 32K context, and FullHouse's requests (no context size) forced a reload back, 10–16s
each time.

- **Pinned `num_ctx` and `keep_alive`** on every request (`OLLAMA_NUM_CTX`, `OLLAMA_KEEP_ALIVE`), so a
  loaded model is reused and never idles out mid-run. One shared `httpx.Client` for the process.
- **Dedicated Ollama** for development on port 11435 (`OLLAMA_NUM_PARALLEL=4`,
  `OLLAMA_MAX_LOADED_MODELS=1`); compose sets the same on its `ollama` service.
- **Parallel delegation**: when the Boss asks for several subagents in one turn they run concurrently
  (`AGENT_MAX_PARALLEL_TOOLS`). Only tools marked `parallel_safe` are ever run concurrently; each
  delegation opens its own session, sequence numbers on the Boss's events are serialised by a lock,
  and cancelling the Boss stops its children.

Measured on qwen2.5:7b: a Boss run on the shared server timed out after 300s; on the dedicated one a
two-agent review finished in 19.9s, and a three-agent review that also filed proposals in 73.6s
against ~134s of subagent time.
