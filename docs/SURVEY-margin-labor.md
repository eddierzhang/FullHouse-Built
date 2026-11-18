# Margin & Labor Engine: repository survey (Phase 0, step 1)

Nov 17 2026. The survey the spec asks for before any code changes. Nothing has been built from the spec yet; the only
files added are `docs/SPEC-margin-labor.md`, this file, `docs/DECISIONS.md` and `CLAUDE.md`.

## 1. What is actually in use

| | In the repository |
|---|---|
| Language and framework | Python 3.12, FastAPI. The spec's assumed stack is right; the v2 TypeScript monorepo was never built |
| ORM and migrations | SQLAlchemy 2 (declarative, `Mapped`), Alembic. Head is `f7c2a9e1b4d8` |
| Databases | SQLite in development and tests; Postgres supported (the suite ran on it in CI before v3; the three v3 migrations have only run on SQLite) |
| Jobs | `app/platform/jobs.py`: a `jobs` table with singleton keys, leasing, retries with backoff, a cap of 2 agent runs per restaurant; worker threads in the web process by default, `python -m app.worker` for a separate one. Queues are a fixed tuple (`QUEUES`) |
| Scheduled work | `app/platform/workflows.py`: per-restaurant cron rows on the restaurant's clock, enqueued once a minute; handlers in `app/platform/handlers.py` |
| Events | `app/platform/events.py`: append-only `events` table, `emit()`, subscribers that enqueue jobs |
| Tests | pytest (701 backend tests), Playwright (53 browser tests), agent evals |
| Front end | React 19, TypeScript, Vite. No router library (hash routes in `lib/useHashRoute.ts`), no component library, no chart library: charts are hand-built SVG in `components/Charts.tsx` and `components/InsightCharts.tsx`. Styles are one `index.css` with CSS variables as design tokens |
| Decimal use | None. Every money and quantity column in the repository is `Float` |
| Solver, numeric libraries | None installed: no OR-Tools, numpy, pandas or scikit-learn |

## 2. The spec's tables against the repository

| Spec table | Exists? | Actual name and differences |
|---|---|---|
| `proposals` | Yes | `agent_actions`; `kind` is `action_type`; payload is JSON; applied by `app/agents/appliers.py` |
| `findings` | No table | Findings are digest cards: JSON in `price_digests.cards` with a `section` key. `menu_margin` exists as section `margin` |
| `audit_log` | Yes | `change_log`, written by a session listener for every flush |
| `events` | Yes | `events` (seq, type, aggregate_table, aggregate_id, payload). Not partitioned |
| `restaurant_flags` | Yes | Same name; flags are constants in `app/platform/flags.py` |
| `canonical_items` | Yes | `price_items` (name, category, unit, pack_unit, pack_size). No base unit of g/ml/each: `unit` is whatever the invoice uses (lb, gal, ct). No `shelf_life_days` or `track_usage` |
| `supplier_item_mappings` | Yes | `price_item_aliases` (no price column, by test) |
| `price_points` | No table | A price is computed on read from `supplier_invoice_lines` (`unit_price / pack_size`) by `prices.engine.gather`. There is no `price_point.created` event; `invoice.ingested` exists |
| `recipes` | Yes | Has `yield_qty`, `yield_unit`, `status`, `pos_item_id`, `menu_item_id`. No `kind`, no `portions` (`yield_qty` plays that part) |
| `recipe_lines` | Yes | Has `item_id`, `sub_recipe_id`, `qty`, `unit`. No `yield_pct` |
| `plate_costs` | Yes | `recipe_id`, `week_of`, `cost`, `basis`, `unpriced`. No `effective_at`, `is_current`, `lines_priced`, `lines_total`, `calc_version`, `trigger_event_id`. One row per recipe per week |
| `sales_items` | Yes | Per item per day, as the spec expects |
| `sales_orders`, `sales_order_lines` | No | The app's own `orders` and `order_items` exist (orders taken in FullHouse, with a `channel` of dine_in, takeout, delivery), but there is no POS order-level table. The Square adapter writes daily rows only |
| `pos_items` | No | POS items exist only as ids and names on `sales_items`. Menu prices live on `menu_items.price` |
| `labor_punches` | No | Only `labor_days` (hours and dollars per day) |
| `channel_payouts`, `channel_terms` | No | |
| `demand_drivers` | No | |
| `item_conversions` | Partly | `unit_conversions` (added in v3) converts one named unit to a stock item's unit, per inventory item, not per canonical item to a base unit. Built-in mass and volume tables are in `prices/stock.py` and `recipes/engine.py` |
| `supplier_schedules` | Partly | `supplier_calendars` (v3): order weekdays, one cutoff time, delivery weekdays, minimum dollars, per supplier in one row, not one row per delivery day |
| `restaurant_settings` | Partly | Columns and JSON on `restaurants` (`timezone`, `opening_hours`, `inventory_settings`, `scheduling_rules`). No `business_day_start`, target percentages, `price_ending` or costing policy |
| `interval_actuals`, `forecast_intervals`, `forecast_runs` | No | |
| `forecasts` | Yes | Per POS item per day with `qty_pred`, `qty_lo`, `qty_hi`, `model`. The band is plus and minus one MAD, not p10 and p90 |
| `forecast_errors` | Yes | |
| `purchase_orders`, `po_lines` | Yes | No `suggested_qty` or `basis` on lines |
| `prep_sheets` | Yes | Lines are JSON; one per date |
| `stock_estimates` | No | v3 keeps actual stock on `inventory_items.quantity_on_hand` with a ledger; there is no count-free estimate per canonical item |
| `usage_variance`, `channel_item_margins` | No | v3's variance report is count-based (`stock_daily`), a different thing |
| `employees` | Yes | `staff` (name, role, contact, hire date). One role as text; no rate, hours limits, minor flag or external id |
| `shifts` | Yes | `shifts` (staff_id, date, start_time, end_time, role, published_at). Integer id, local date and times, no schedule, kind, lock or break |
| `schedules`, `labor_roles`, `employee_availability`, `employee_time_off`, `labor_standards`, `labor_rules`, `labor_requirements`, `labor_adjustments` | No | Role names are a JSON list on `restaurants.staff_roles`; a few limits are in `restaurants.scheduling_rules` |

## 3. How the cross-cutting pieces work today

- **Tenant isolation.** `restaurant_id` on every tenant table, taken from the signed-in user by `restaurant.inventory.scope_for` (and each module's `_scope`), never from a path or body. No row-level security. Enforced by `tests/test_invariants.py` (schema-driven) and `tests/test_restaurant_separation.py`.
- **Proposals.** Tools in `app/agents/tools/` create `agent_actions`; `app/agents/guard.py` raises at flush time on any other tenant-table write inside a tool; `app/agents/appliers.py` carries out an approval; weekly caps in `app/agents/caps.py`.
- **Audit.** `app/audit/listener.py` writes `change_log` for every insert, update and delete, with before and after; `app/audit/revert.py` undoes one.
- **Events and jobs.** As in section 1. Singleton keys already make a duplicate job a no-op while one is queued or active; they do not stop the same key running again later.
- **Flags.** `restaurant_flags`, read by `flags.enabled`; owners switch them under Profile, Features.
- **Review.** There is no `/review` page. Proposals wait in Recommendations (`#approvals`), unsure invoice lines in Questions (`#questions`), unsure documents on Documents.

## 4. Where the spec and the repository conflict

The rule in section 1 of the spec decides most of these. Three need your word, marked **yours**.

| # | Conflict | What I will follow |
|---|---|---|
| 1 | The spec's table names (`proposals`, `findings`, `audit_log`, `canonical_items`, `employees`…) against the repository's | The repository's names, extended by migration. No duplicates |
| 2 | Decimals: the spec forbids floats for money and quantities; every existing column is a float | The spec, for everything new: new tables use `Numeric(12,4)` and the new code computes in `Decimal`. Existing float columns are read through `Decimal(str(value))` and are not migrated. Golden tests compare exact decimals |
| 3 | `price_points` and `price_point.created` do not exist | A new `price_points` table would duplicate the invoice lines. Instead: a read-only view function over invoice lines returning decimals, and `recost` triggered by the existing `invoice.ingested` event, limited to the recipes that use the items on that invoice |
| 4 | `findings` has no table; the spec opens and closes findings with state | **Yours.** Either a real `findings` table (my recommendation: findings need an open/closed state, a two-consecutive-weeks rule and a link to a proposal, which JSON cards cannot hold), with digest cards built from it; or keep cards only. I will build the table unless you say otherwise |
| 5 | Base units: the spec wants g, ml or each per item; `price_items.unit` is the invoice's unit | The spec's behavior: add `base_unit` and a conversion to it per item, reusing the existing mass and volume tables and `unit_conversions`. No second normalization |
| 6 | "The digest is the home screen" against this app, where Today is home and you asked that the operations pages stay the main navigation | The repository: Today stays home. The modules' cards go on the Digest and each gets one page, in one new sidebar group with the spec's labels (Menu costs, Usage, Ordering and prep, Labor, Channels). "Review" maps to Recommendations |
| 7 | "A person always sends" and "nothing requires or builds count workflows" against what was built today at your request: ordering autopilot (sends orders by itself when an owner switches it on), count sheets and a waste log | **Yours.** I will not weaken the rule for the new modules: Module C's drafts are never sent by autopilot (it only acts on drafts it made itself), and no module reads counts. The existing v3 features stay as they are, behind their own flags. Say if you want autopilot removed, or pointed at Module C's quantities instead of its own |
| 8 | Two stock pictures: v3's real stock ledger and the spec's count-free `stock_estimates` | Both, kept apart. Modules B and C use only `stock_estimates`, as the spec requires. They do not read `quantity_on_hand` |
| 9 | Two recipe systems: `recipes`/`recipe_lines` (v2, invoice units, used for plate cost) and `recipe_items` (operations, stock units, used by v3 depletion) | Module A extends `recipes`, `recipe_lines` and `plate_costs`, as the spec says. `recipe_items` is left alone |
| 10 | Scheduling: the app already has a Schedule & staff page, `staff`, `shifts`, and an agent tool that proposes shift changes | Extend `staff` and `shifts` (add the spec's columns; shifts gain a schedule, kind, lock and break) so there is one schedule, not two. The existing page keeps working on the published schedule |
| 11 | UI limits (no tours or tips on live pages, at most 3 tiles, exactly 1 chart, banned words including "insights") against v3 pages | The spec's rules apply to the new module pages and are enforced by a lint over their strings. Existing pages are not rewritten; the v3 tour does not open on module pages. The lint will fail on the word "Insights" if it scans the sidebar group of that name: I will scope it to the new modules, or rename the group if you prefer |
| 12 | The spec wants a chart library decision before adding one | None is needed: the hand-built SVG charts are the repository's "library", and the new chart components are written the same way |
| 13 | OR-Tools for schedule generation | A new dependency (`ortools`), named by the spec, added in Phase 6 |
| 14 | Performance tests on 500 restaurants with 2 years of history each, in CI | Built as a script and a marked test that CI can run; on a laptop it is run at a smaller size and the figures reported, as the v2 load test was |
| 15 | Month partitions and row-level security on Postgres | Written as Postgres-only migration steps, untested here: there is no Postgres on this machine. They need a run in CI or on a real database before production |

## 5. "Confirm with Eddie" items that block Phase 0 or Phase 1

| Question | Blocks | Default the spec gives | Status |
|---|---|---|---|
| Does each module get its own detail page? | Phase 0 | Yes | Proceeding on the default |
| Which POS is first for order-level data? | Phase 0 | CSV import only | Proceeding on the default |

Not blocking yet, with their defaults: labor scheduling in scope (yes; Phase 5), time punches from CSV (Phase 5), no weather source (Phase 4), `channel_terms` only with no payout matching (Phase 3), labor rules confirmed by the owner (Phase 6).

Beyond the spec's list, conflicts 4 and 7 above are the two I would like answered before Phase 1 and Phase 4.

## 6. Migration plan for the section 4 tables

In this order, each additive (new tables and nullable columns only), each on SQLite and Postgres:

1. `restaurants`: `business_day_start` (default 04:00), `target_food_cost_pct` (0.30), `target_labor_pct`, `price_ending` (0.00), `costing_price_policy` (latest). Money and rates as `Numeric`.
2. `price_items`: `base_unit`, `to_base_factor`, `shelf_life_days`, `track_usage`.
3. `item_conversions`: per price item, a unit to the base unit. `unit_conversions` stays for stock items.
4. `pos_items`.
5. `sales_orders`, then `sales_order_lines` (with `business_date` on both).
6. `labor_punches`.
7. `channel_payouts`, `channel_terms`.
8. `demand_drivers`.
9. `supplier_schedules`: one row per delivery day, filled from `supplier_calendars` where one exists.
10. `interval_actuals`, `forecast_runs`, `forecast_intervals`.
11. `findings` (if conflict 4 goes that way).
12. Postgres only: month partitions for `sales_order_lines`, `interval_actuals`, `forecast_intervals`, `labor_punches`; indexes that lead with `restaurant_id`.

Later phases add their own: Module A's columns on `recipes`, `recipe_lines`, `plate_costs` (phase 1); `usage_variance` (2); `channel_item_margins` (3); `stock_estimates` and `po_lines` columns (4); the labor tables and the `staff` and `shifts` columns (5 and 6); `labor_adjustments` (7).
