# FullHouse: notes for Claude Code

FastAPI + SQLAlchemy 2 + Alembic on SQLite (development) and Postgres (production); React 19 + TypeScript + Vite with
hand-built SVG charts and no UI library; pytest and Playwright. `python dev.py test` runs the backend suite and the
frontend build; the browser tests need `E2E_PYTHON=backend/.venv/bin/python` on a machine with no `python` on the PATH.
Work is behind per-restaurant flags (`app/platform/flags.py`). Phase notes: `docs/V2_TASKS.md`, `docs/V3_TASKS.md`.

## Margin & Labor Engine

The spec is `docs/SPEC-margin-labor.md`; the survey of this repository against it is `docs/SURVEY-margin-labor.md`;
decisions the spec does not cover are one line each in `docs/DECISIONS.md`. Build one phase at a time, write the golden
tests in section 12 before the code, and stop at the end of each phase with the acceptance results. Where the
repository and the spec disagree on names, layout or libraries, the repository wins; on behavior, formulas or rules,
the spec wins. Never weaken a ground rule to make a feature work: stop and ask.

All eight phases are built, each behind its flag (`ml_menu_costs`, `ml_usage`, `ml_ordering`, `ml_labor`, `ml_channels`),
off by default. Backend: `backend/app/margin_labor/` (pure arithmetic in `costing.py`,
`usage_math.py`, `channel_math.py`, `forecasting.py`, `ordering_math.py`, `labor_math.py`, `scheduling.py`,
`intraday_math.py`; jobs and readers beside them; routes under `/api/v1/margin-labor`). Frontend: `frontend/src/ml/`.
Tests: `backend/tests/test_margin_labor_*.py`; browser tests in `frontend/e2e/` run in two seeded restaurants of their
own (`scripts/seed_e2e_margin_labor.py`). `python -m app.margin_labor.backtest` replays the forecast on three fixtures.
The demo (`DEMO_MODE`) has the modules on except Channels: `app/margin_labor/preview.py` fills them from the demo
restaurant's own data after each reset and adds an empty restaurant; sign-in offers either. Since 2026-11-18 the sidebar is consolidated at Eddie's request: Menu costs and Usage are sections of the Menu page,
Labor is a section of Labor and staffing (`frontend/src/pages/SectionsPage.tsx`), and Channels is hidden for now (see
the 2026-11-18 lines of `docs/DECISIONS.md`). Before that, additive only: no existing table, page or route was replaced (see the first 2026-11-17 lines of `docs/DECISIONS.md`).

### Ground rules (spec section 2)

Inherited, unchanged:

1. The agent writes only proposals (`agent_actions`). Each new capability is a new `action_type`, enforced in the tool layer.
2. Every tenant table has `restaurant_id` and is tenant-isolated. Tenant scope never comes from a prompt, a request body or a connector payload.
3. Every automated or human action lands in the audit log (`change_log`) with before and after values.
4. Every write that starts outside the app (webhook, sync, email, cron) is idempotent on an external key and safe to replay.
5. No price is stored on the supplier-item mapping (`price_item_aliases`). No market or "correct" price is computed.
6. Nothing requires inventory counts to work.
7. Humans send. Nothing in these modules emails staff or suppliers, or calls a connector write endpoint.
8. Every extracted field carries a confidence. Anything under 0.8 or failing validation goes to review.
9. Pending proposals are capped per kind, per restaurant, per week. Beyond the cap, hold back and log it.

Changed by the spec: the digest stays the home of these modules and each module adds one detail page opened from its
digest card; labor forecasting and draft schedules are in scope (time clocks, shift swaps, staff messaging and payroll
writes stay out).

New for these modules:

- Money and quantities are decimals: `Decimal` in Python, `numeric(12,4)` in the database. Never floats for money,
  quantities or unit prices. Forecast values may be floats.
- Every displayed number is explainable: each derived row stores the inputs it used (`basis`), and the UI shows them.
- Derived data is recomputable: a pure function of its inputs and a `calc_version`. The same inputs give the same rows.
- No number without enough data: below a module's minimum, the UI shows the setup state, not an estimate.
- Timestamps are UTC; each restaurant has an IANA time zone; grouping by day uses the business date.
- Wages are restricted: `hourly_rate` is readable only by owners and managers and never appears in logs or agent prompts.
- Labor rules are configuration, not code, and are empty until the owner confirms them. The app does not give legal advice.
- Proposal caps: `menu_reprice` 3 a week; `channel_reprice` 3 a week; `schedule_draft` 1 open per week being
  scheduled; `purchase_order` 3 a week; `prep_sheet` 1 per business date. Intraday labor suggestions are not proposals.

### Not in scope for these modules (spec section 1)

- A time clock, shift swapping, or staff messaging.
- Any write to payroll, a POS, a scheduler, or a delivery platform.
- Sending purchase orders, schedules, or price changes automatically. A person always sends.
- Inventory count workflows. Every module must work with no counts.
- Invoice auditing and sales anomaly flags. They are separate work.
- Allocating labor cost to individual dishes or channels.

### Writing rules for interface text (spec section 10.5)

- Sentences: subject, verb, number. At most 20 words. One idea each.
- Labels: at most 3 words, sentence case, no abbreviations except $, %, hr.
- Buttons: one verb, or verb plus noun: Approve, Edit, Dismiss, Publish, Regenerate, Send order.
- Numbers: dollars without cents at $100 or more, with cents below. Percentages to one decimal. Always include the
  period: "a week", "last 28 days".
- Dates and times: "Tue Nov 17" and the restaurant's 12 or 24 hour setting. Never a raw timestamp.
- Errors: state what failed and what the user can do. "Square has not synced since 9:10. Reconnect in Settings."
- Empty results: state the fact. "No dishes are over target."
- Banned words: smart, powerful, seamless, insights, optimize, unlock, leverage, AI-powered, magic, effortless, robust, delight.
- Banned forms: exclamation marks, emoji, questions as headings, greetings, praise, apologies.
- Say this, not the internal term: Expected use (theoretical usage); Unexplained use (variance); Expected (p50);
  Likely range (p10 to p90); Average miss (WAPE); Kept per item (unit contribution); Cost per plate (plate cost);
  People needed, People needed if busy (need_p50, need_p90).
