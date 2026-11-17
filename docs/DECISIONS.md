# Decisions

One line per decision made where `docs/SPEC-margin-labor.md` was silent, or where it and the repository disagreed.
"Default" means the spec's own default for an open question, used until Eddie says otherwise.

| Date | Decision | Why |
|---|---|---|
| 2026-11-17 | The spec is kept as `docs/SPEC-margin-labor.md`, extracted from the PDF as text; its tables are flattened and the PDF is the authority | The PDF could not be exported as Markdown here |
| 2026-11-17 | Repository table names are used (`agent_actions`, `change_log`, `price_items`, `price_item_aliases`, `staff`), extended by migration | Spec section 1, rule 4 |
| 2026-11-17 | New tables use `Numeric(12,4)` and new code computes in `Decimal`; existing float columns are read through `Decimal(str(value))` and not migrated | The spec forbids floats for money and quantities; rewriting every existing column is outside these modules |
| 2026-11-17 | No `price_points` table: prices are read from invoice lines as decimals, and `recost` runs on the existing `invoice.ingested` event | A second table would duplicate the invoice lines (spec section 1, rule 4) |
| 2026-11-17 | Today stays the home page; the modules get one sidebar group with the spec's labels and their cards on the Digest; "Review" is Recommendations | Eddie asked earlier that the operations pages stay the main navigation |
| 2026-11-17 | Module C's drafts are never sent by the v3 autopilot, and no module reads counts or the v3 stock ledger | Ground rules 6 and 7 are not weakened for the new modules; the v3 features stay as built, behind their own flags. Awaiting Eddie on whether autopilot should change |
| 2026-11-17 | Modules B and C use `stock_estimates` only, never `inventory_items.quantity_on_hand` | Spec: every module must work with no counts |
| 2026-11-17 | Module A extends `recipes`, `recipe_lines` and `plate_costs`; `recipe_items` (operations recipes) is left alone | The spec names the v2 tables |
| 2026-11-17 | `staff` and `shifts` are extended for Module D; there is one schedule, not two | Spec section 1, rule 4 |
| 2026-11-17 | The interface-text lint and the page limits apply to the new module pages only | Rewriting existing pages is outside the spec |
| 2026-11-17 | Charts are hand-built SVG, as the rest of the app | The repository has no chart library and needs none |
| 2026-11-17 | Default: each module gets its own detail page | Spec section 14 |
| 2026-11-17 | Default: order-level data comes from CSV import only | Spec section 14 |
| 2026-11-17 | Open, recommended: a real `findings` table, with digest cards built from it | Findings need state the JSON cards cannot hold. Awaiting Eddie |
| 2026-11-17 | Additive only: new tables, new optional columns, new pages and cards. No existing table, column, page, route or behavior is replaced or removed | Eddie, 2026-11-17: "do not override anything that already exists, only add new features and make improvements to the current design" |
| 2026-11-17 | The `findings` table is built (the recommended default) | Unanswered; a table nothing reads yet costs nothing to change |
| 2026-11-17 | New module settings live in a new `restaurant_settings` table; the time zone and hours stay on `restaurants` | Additive only |
| 2026-11-17 | `supplier_schedules` and `item_conversions` are new tables beside v3's `supplier_calendars` and `unit_conversions`, which are untouched | Additive only; the spec's shapes differ (one row per delivery day; a factor to a base unit) |
| 2026-11-17 | On SQLite the decimal column type binds as a float and reads back through the column's scale; Postgres stores exact numerics. Python only sees `Decimal` | SQLite has no decimal type; production is Postgres |
| 2026-11-17 | `rollup_intervals` writes `sales_items` rows (source "orders") only for a date no other source supplies | Older readers see CSV-imported days without anything that exists being overwritten |
| 2026-11-17 | An order with one unreadable line is left out whole and reported with its line number | Half an order would give wrong totals; nothing is guessed |
| 2026-11-17 | Derived-table recomputes run on one new job queue, `derive`, one job per restaurant-day, keyed so a replay is a no-op | Ground rule 4 |
| 2026-11-17 | The module flags (`ml_*`) are off by default and off in the demo until each module has a page | A flag with nothing behind it would show an empty page |
| 2026-11-17 | Phase 0's read-performance check times the three indexed reads module pages will make (five restaurants, 120 days each), not a page | No module page exists until phase 1; the page-level 300 ms check moves there |
| 2026-11-17 | The interface-text lint runs before every frontend build (`npm run lint:text`), on `src/ml` only | Spec 10.5; existing pages are out of scope |
