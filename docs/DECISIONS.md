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
| 2026-11-17 | Module A writes a new `dish_costs` table; `plate_costs` is left as it is. This replaces the earlier line about extending `plate_costs` | `plate_costs.cost` is a float with one row per recipe per week; the spec needs decimals and a row per change. Additive only |
| 2026-11-17 | A recost is triggered by a session watcher that emits `price_point.created` and `recipe.saved` at commit, not by changes to the invoice or recipe code | No such events existed; additive only. Nothing is emitted for a restaurant with no module on |
| 2026-11-17 | The 3-a-week `menu_reprice` cap is enforced inside Module A; the older constant (5) for the older reprice button is unchanged | Ground rule 9 for the new module; additive only |
| 2026-11-17 | Menu price: `pos_items.price`, then the app's own `menu_items.price`, then the 14-day median of net / qty | The spec names the first and third; this app also keeps menu prices of its own |
| 2026-11-17 | "Last 28 days" and "last 14 days" are the complete business dates before today; today's partial sales are left out | A partial day would understate every weekly figure |
| 2026-11-17 | "Cost per plate 28 days ago" is the recipe as it is now at the prices of 28 days ago | It isolates the price move, which is what the weekly impact formula measures; cost history is the same pure function at earlier dates |
| 2026-11-17 | The current cost uses every invoice price that has arrived, even one dated ahead of the business date | Before 04:00, or on a server in a later time zone, an invoice can carry tomorrow's date |
| 2026-11-17 | Two invoices dated the same day are taken in the order they were saved | "Latest" needs a tie-break the same every run |
| 2026-11-17 | An outlier price is left out of costing and listed on the dish page with a link to Invoices; it does not open a Question | Questions are the agent's item-matching queue with their own cap; additive only |
| 2026-11-17 | No reprice is proposed when the 10% cap rounds down to today's price | There is nothing to suggest |
| 2026-11-17 | Approve, Dismiss and Publish are sent after 10 seconds, with Undo on screen until then; leaving the page sends at once | The spec asks for Undo for 10 seconds; the proposal endpoints have no un-approve |
| 2026-11-17 | Pages are hash routes: `#menu-costs`, `#menu-costs/{recipe}`; later `#usage`, `#ordering-prep`, `#labor`, `#channels` | The app's router; `#ordering` and `#prep` already belong to older pages |
| 2026-11-17 | Times show on a 12-hour clock | Restaurants have no 12 or 24 hour setting yet |
| 2026-11-17 | List endpoints send an ETag and are not paginated | A restaurant's dishes and ingredients are bounded lists; cursor pagination is left for order-level lists |
| 2026-11-17 | The Margin & Labor browser tests run in their own seeded restaurant | So their invoices, recipes and sales never show in the other suites |
| 2026-11-17 | `@axe-core/playwright` added as a dev dependency | The spec requires automated WCAG 2.1 AA checks |
| 2026-11-17 | `test_two_managers_approving_at_once_apply_it_once` gained a second barrier so both threads read before either writes | The test relied on timing for that; the new session watcher shifted the timing and exposed it |
| 2026-11-17 | Shares (food cost, coverage, unexplained share) are stored to six places, not four | Rounding to four and then to one for display can show 15.8% for a true 15.85% |
| 2026-11-17 | Usage windows are the 28 business dates ending on the last Sunday, and each run rewrites the last eight weekly windows | "Two consecutive weekly runs" needs stable weekly windows; rewriting them keeps the job a pure function and gives the ingredient page its eight points |
| 2026-11-17 | Expected use takes units sold from `sales_items` plus lines voided after fire from `sales_order_lines` | The spec counts food that was made and voided; daily sales are what every restaurant has |
| 2026-11-17 | An invoice line left out by the outlier guard is also left out of purchased quantity | Its quantity rests on the same pack size the price does |
| 2026-11-17 | A usage finding closes when it stops being true; a dismissed one stays dismissed while it stays true | The spec says when to open one and is silent on closing |
| 2026-11-17 | A data-gap finding needs the module live but not two weeks | The spec says "instead" and gives no second condition |
| 2026-11-17 | Rows under $5 a week are collapsed into one line; the level is a setting | Spec 10.4 gives the example and no number for Usage |
| 2026-11-17 | Usage is recomputed when sales, an invoice price or a recipe changes, and on the first read of a new week; there is no new scheduled workflow | Additive only: the workflow table is counted by an existing test, and a pure recompute on change covers the weekly run |
| 2026-11-17 | One confirmed dish per POS item is used for expected use: the first by name | Two confirmed recipes on one POS item would double the expected use |
| 2026-11-17 | Per-unit channel figures are averaged weighted by units sold | "Average per item" has to be dollars per unit sold for the weekly impact formula to hold |
| 2026-11-17 | A matched platform order uses its payout row's commission and fees, plus the contract's packaging; an unmatched one uses the contract's rates, fixed fee and packaging | A payout report never contains the restaurant's own packaging cost; the platform's fixed fees are already inside what it reports |
| 2026-11-17 | Cost per plate on an order's business date is the recipe at that day's prices; if it was not fully priced then, the cost now | An invoice can arrive after the first orders in the window |
| 2026-11-17 | With no dine-in sales of an item, "half of its dine-in contribution" is half of the target (dine-in menu price less cost per plate) | The spec's rule needs a dine-in figure |
| 2026-11-17 | The reprice uses the contract's commission and processing rates, else the rates the payouts actually took | The spec's formula needs both rates |
| 2026-11-17 | The first rates saved for a platform cover its history; later ones start the day they are saved, and earlier rates keep pricing the orders they applied to | A window's orders were sold under the rates of their day |
| 2026-11-17 | The Channels table puts the platform under the item name: seven columns, not eight | Spec 10.3 caps a table at seven; its own list for Channels has eight |
| 2026-11-17 | The Channels header says "keeps too little" when a flagged item still keeps something, and "loses money" when one is below zero | A finding also opens under half of the dine-in amount; the sentence has to be true |
| 2026-11-17 | `channel_reprice` is registered beside the older proposal kinds from the new module; approving records the intent and changes nothing | Ground rules 1 and 7 |
| 2026-11-17 | A link that opens a page with a panel showing is read once and removed from the address | Otherwise a reload reopens the panel over the page |
| 2026-11-17 | `test_stock_outlook...` now names the date it checks | The demo is seeded on the UTC date and the outlook used the server's; they differ after midnight UTC on a server west of it. The product mismatch itself is left as it was and is listed in the report |

