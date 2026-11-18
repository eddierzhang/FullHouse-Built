# FullHouse Margin & Labor Engine: design doc and build prompt

Nov 17, 2026 · Eddie

> This file is the text of "FullHouse Margin & Labor Engine — Design Doc and Claude Code Build Prompt.pdf" (50 pages),
> extracted from the PDF. Section headings are marked; page breaks are kept as comments. **Tables were flattened by the
> extraction**: a table's cells appear one per line, and a table that ran across a page break is split around the next
> page's text. Where a table's reading is unclear, the PDF is the authority. Formulas and worked examples are intact.

<!-- page 1 -->

FullHouse Margin & Labor Engine — Design 
Doc and Claude Code Build Prompt
Nov 17, 2026  ·  @Eddie

## 1. How to use this document

This document is a build prompt for Claude Code. It specifies five modules that cut food
and labor losses in FullHouse, plus the shared data they run on.
Instructions to Claude Code. Follow these in order.
1. Read this whole document before writing code.
2. Do the repository survey in Phase 0 (section 13) first. Report what you find before
changing anything.
3. Where the repository and this document disagree on names, file layout, or libraries,
the repository wins. Where they disagree on behavior, formulas, or rules, this
document wins. Report every such conflict.
4. Table and column names here are defaults taken from the v2 build spec. If an
equivalent table exists under another name, reuse it and extend it by migration. Never
create a duplicate.
5. Build one phase at a time. A phase is done only when its acceptance tests pass and
the full existing test suite still passes.
6. Never weaken a rule in section 2 to make a feature work. Stop and ask instead.
7. If a decision is not covered here, choose the simpler option, record it in
docs/DECISIONS.md , and continue.
8. Items marked Confirm with Eddie in section 14 need an answer before the phase that
depends on them.
What is covered. Modules A to E map to features 2, 3, 4, 5 and 7 of the loss-reduction list.
Module Feature Loss it targets Minimum data it needs
A. Live recipe costing 2 Menu prices that lag
ingredient costs
Invoices (already ingested),
confirmed recipes, POS item
prices

<!-- page 2 -->

Module D includes the five labor refinements: per-role standards, outside demand
drivers, forecast ranges, intraday reforecasting, and generated draft schedules.
Not in scope. Do not build any of these.
A time clock, shift swapping, or staff messaging.
Any write to payroll, a POS, a scheduler, or a delivery platform.
Sending purchase orders, schedules, or price changes automatically. A person always
sends.
Inventory count workflows. Every module must work with no counts.
Invoice auditing and sales anomaly flags (features 1 and 6). They are separate work.
Allocating labor cost to individual dishes or channels.

## 2. Ground rules

Nine of the ten invariants in the v2 build spec hold unchanged. This document amends the
tenth (the digest rule) and reverses one earlier exclusion (scheduling).
Inherited invariants (unchanged).
1. The agent writes only to proposals . Each new capability is a new proposals.kind ,
enforced in the tool layer.
2. Every tenant table has restaurant_id  and is tenant-isolated. Tenant scope never
comes from a prompt, request body, or connector payload.
3. Every automated or human action lands in audit_log  with before and after values.
Module Feature Loss it targets Minimum data it needs
B. Usage variance 3 Waste, over-portioning,
theft
Module A, plus 4 weeks of
POS item sales
C. Ordering and prep 4 Spoilage and over-
prepping
Module A, item demand
forecast, supplier delivery
days
D. Labor forecasting
and schedules
5 Overstaffing, overtime,
late cuts
4 weeks of timestamped POS
orders and time punches
E. Channel
profitability
7 Delivery orders that
lose money
Module A, orders tagged by
channel, commission terms

<!-- page 3 -->

4. Every write that starts outside the app (webhook, sync, email, cron) is idempotent on
an external key and safe to replay.
5. No price is stored on supplier_item_mappings . No market or "correct" price is
computed.
6. Nothing requires inventory counts to work.
7. Humans send. Nothing in this document emails staff or suppliers, or calls a connector
write endpoint.
8. Every extracted field carries a confidence. Anything under 0.8 or failing validation
goes to /review .
9. Pending proposals are capped per kind, per restaurant, per week. Beyond the cap,
hold back and log it.
Changes this document makes.
New rules for these modules.
Money and quantities are decimals. Use Decimal  in Python and numeric(12,4)  in
the database. Never use floats for money, quantities, or unit prices. Forecast values
may be floats.
Every displayed number is explainable. Each derived row stores the inputs it used
( basis ). The UI shows them on request.
Derived data is recomputable. Each derived table is a pure function of its inputs and
a calc_version . Rerunning a job with the same inputs gives the same rows.
No number without enough data. Each module defines a minimum data rule. Below it,
the UI shows the setup state, not an estimate.
Earlier rule Change Reason
"The digest is the product;
no module gets its own
dashboard page."
The digest stays the home
screen. Each module adds
one detail page, opened
from its digest card.
The request is for an
informative UI per tool. A
card cannot hold a schedule
or a forecast.
Scheduling was excluded
from the roadmap.
Labor forecasting and draft
schedules are now in scope.
Time clocks, shift swaps,
staff messaging, and payroll
writes stay out.
The request names labor
forecasting.

<!-- page 4 -->

Timestamps are stored in UTC. Each restaurant has an IANA time zone. All grouping
by day uses the business date (section 4).
Wages are restricted. hourly_rate  is readable only by the owner and manager roles.
It never appears in logs or agent prompts.
Labor rules are configuration, not code. Overtime, break, rest, minor, and advance-
notice rules live in labor_rules . Defaults are empty until the owner confirms them.
The app does not give legal advice.
Proposal caps for new kinds.
Intraday labor suggestions are not proposals. They are short-lived labor_adjustments
rows with their own limits (section 8).
Assumed stack. Recent work points to Python with FastAPI, SQLAlchemy, Alembic,
SQLite in development, Postgres in production, pytest, and Playwright. The v2 spec
described a TypeScript monorepo. Phase 0 settles which is true. On SQLite, tenant
isolation is enforced in the data-access layer; on Postgres, also by row-level security.

## 3. Architecture and scalability

All five modules share one pipeline: raw data in, events out, jobs recompute derived
tables, and the UI reads only derived tables.
SOURCES                 RAW (idempotent upsert)        EVENTS (append-only)
Invoices (existing) --> price_points               --> price_point.created
POS orders          --> sales_orders, _lines       --> sales.synced
Time punches        --> labor_punches              --> punches.synced
Delivery payouts    --> channel_payouts            --> payouts.synced
Weather, holidays,  --> demand_drivers             --> drivers.updated
proposals.kind Cap Notes
menu_reprice 3 per week Already defined in v2; cap set here
channel_reprice 3 per week New
schedule_draft 1 open per week being
scheduled
Regenerating replaces the open
draft
purchase_order 3 per week Unchanged from v2
prep_sheet 1 per business date Regenerating replaces it

<!-- page 5 -->

events, bookings
                                |
                                v
JOBS (queued, idempotent, per tenant)
  recost -> usage_variance -> channel_margins
  rollup_intervals -> forecast_daily -> forecast_intervals
  order_draft, prep_sheet
  labor_standards -> labor_requirements -> schedule_generate
  intraday_reforecast (every 15 min during service)
                                |
                                v
DERIVED (versioned, recomputable)  -->  findings, proposals, 
labor_adjustments
                                |
                                v
API (reads derived tables only)  -->  Digest cards and module pages  -->  
person approves
Design rules.
Event-driven recompute. A job runs because an input changed, not on a blanket
schedule. A new price point recosts only the recipes that use that ingredient.
Idempotent jobs. Each job has a key built from (restaurant_id, job_name, input
window or input id) . A duplicate key is a no-op. Replays are safe.
Read path does no math. API handlers read derived tables. They never run a forecast,
a solver, or a costing pass inside a request.
Swappable models. Forecasting sits behind a Forecaster  interface. Each forecast
row stores model  and model_version . A new model ships next to the old one and
replaces it only by the promotion rule in section 8.
Feature flags. Each module has one flag in restaurant_flags . Flags default to off.
Phases merge behind flags.
Job runner. Use the job mechanism the repository already has. If there is none, define
a JobRunner  interface with an in-process implementation for SQLite development
and a Postgres-backed queue for production. Ask before choosing the queue library.
Scaling path. The design must hold from 1 restaurant to 10,000 without a rewrite.

<!-- page 6 -->

Performance targets. Test these in Phase 0 with synthetic data: 500 restaurants, 2 years
of history each.
Concern Rule
Tenant isolation restaurant_id  is the first column of every
composite index and the partition-pruning key in
every query
Large tables On Postgres, partition sales_order_lines ,
interval_actuals , forecast_intervals ,
labor_punches , and events  by month
Workers Workers are stateless. Add workers to add
throughput. Limit each restaurant to 2 concurrent
jobs so one tenant cannot starve others
Backfills Run in chunks of one restaurant-month. Each
chunk is its own idempotent job
Scheduled work Nightly jobs run per restaurant at 04:30 local time,
spread by a hash of restaurant_id  over 30
minutes
Web tier Stateless. Derived tables carry computed_at ;
responses send an ETag built from it
Hot reads Each module page reads at most 3 indexed queries.
Add a covering index for each
Multi-location groups Out of scope now. Do not block it: never assume
one user maps to one restaurant
Operation Target
Recost after a new price point Affected recipes updated within 60 s, 95th
percentile
Module page API response Under 300 ms, 95th percentile
Nightly forecast, one restaurant Under 60 s
Intraday reforecast, one restaurant Under 5 s

<!-- page 7 -->

Failure handling. A failed job retries 3 times with exponential backoff, then writes a
job.failed  event. A module whose inputs are older than its freshness limit shows a
stale-data banner with the last update time. It never shows old numbers as current.

## 4. Shared data foundation

The v2 tables store sales per item per day. Labor, intraday, and channel work need
timestamped orders, so this section adds order-level data and keeps the daily tables as
rollups.
Conventions. Every table below has id , restaurant_id , created_at , and updated_at .
Money and quantities are numeric(12,4) . Enumerated values are listed after the column
name, separated by commas.
New raw tables.
Operation Target
Schedule generation, 40 employees, one
week
Under 30 s, or return the best feasible result at
30 s
Table Columns Notes
pos_items pos_item_id, name, category,
price, active, connection_id
POS menu catalog. Unique on
(restaurant_id, connection_id,
pos_item_id)
sales_orders external_id, connection_id,
opened_at, closed_at,
business_date, channel: dine_in,
takeout, delivery_own,
delivery_platform, catering;
platform (text, nullable), covers
(nullable), gross, discounts, net,
tax, tips, status: open, closed,
voided, refunded
Unique on (restaurant_id,
connection_id, external_id)

<!-- page 8 -->

New reference and settings tables.
Table Columns Notes
sales_order_lines order_id, business_date,
pos_item_id, pos_item_name, qty,
net, modifiers (json), voided,
voided_after_fire, fired_at
(nullable)
Partition by month of
business_date
labor_punches employee_id, role_key, clock_in,
clock_out (nullable),
business_date, source: pos,
scheduler, payroll, csv;
external_id
Unique on (restaurant_id,
source, external_id)
channel_payouts platform, external_order_id,
order_id (nullable), gross,
commission, fees, adjustments,
payout, paid_at
From platform payout reports.
CSV import first
demand_drivers date, hour (nullable), kind:
holiday, local_event, weather,
promo, closure, reservations;
payload (json), source,
confidence
One row per driver per date or
hour
Table Columns Notes
item_conversions canonical_item_id, from_unit,
to_base_factor, source: invoice, user,
agent; confirmed
Converts a recipe or pack
unit to the item's base
unit (g, ml, or each).
Example: 1 lb = 453.592 g
supplier_schedules supplier_id, order_cutoff_weekday,
order_cutoff_time,
delivery_weekday,
minimum_order_amount (nullable)
One row per delivery day
per supplier

<!-- page 9 -->

Derived tables shared by modules.
Table Columns Notes
channel_terms platform, commission_pct,
processing_pct,
fixed_fee_per_order,
packaging_cost_per_order,
effective_from, effective_to
(nullable)
Used when a payout row
is missing
restaurant_settings timezone, business_day_start
(default 04:00), opening hours per
weekday, target_food_cost_pct
(default 0.30), target_labor_pct
(nullable), price_ending (default
0.00), costing_price_policy: latest,
avg_30d
Extend the existing
settings table if one exists
Table Columns Built by
sales_items
(existing)
Unchanged Rollup of sales_order_lines
when line data exists. Existing
readers keep working
interval_actuals business_date, interval_start,
channel, orders, covers,
items, net
rollup_intervals  job, 15-minute
intervals
forecasts  (existing) Unchanged columns.
qty_lo  and qty_hi  now
hold the 10th and 90th
percentiles
forecast_daily  job, per POS
item per day
forecast_intervals business_date, interval_start,
metric: orders, covers, items,
net; channel (nullable), p10,
p50, p90, kind: weekly,
intraday; run_id
forecast_intervals  and
intraday_reforecast  jobs

<!-- page 10 -->

forecast_intervals  has two more columns: grain  (day or interval) and
drivers_applied  (json). A row with grain day holds the daily total for its metric and
channel, with interval_start  at the start of the business date. kind  records which job
wrote the row: weekly for the nightly forecast, intraday for the reforecast.
Business date. A sale at 01:30 on Saturday belongs to Friday night. Compute it once, at
ingestion:
local_time    = timestamp converted to the restaurant's time zone
business_date = date(local_time - business_day_start)
Example: business_day_start = 04:00
  Sat 01:30 local  ->  Fri 21:30 after subtracting 4 h  ->  business_date = 
Friday
  Sat 04:00 local  ->  Sat 00:00 after subtracting 4 h  ->  business_date = 
Saturday
Covers. Use the POS guest count when present. When it is missing, forecast and staff on
orders  instead. Do not estimate covers from order totals.
Units. Every canonical item has one base unit: g, ml, or each. Recipes may use any unit that
has an item_conversions  row. A recipe line with no conversion is unpriced and goes to
/review . Reuse the existing pack-size normalization; do not write a second one.
Data freshness limits. A module shows a stale-data banner when its input is older than
the limit.
Table Columns Built by
forecast_runs kind, model, model_version,
inputs_hash, started_at,
finished_at, status, metrics
(json)
Every forecast job
Input Limit
POS orders, during opening hours 30 minutes
POS orders, daily sync 36 hours
Time punches 36 hours (30 minutes for the Today screen)
Weather forecast 12 hours

<!-- page 11 -->


## 5. Module A: live recipe costing

Module A recomputes the cost of every affected dish whenever an ingredient price
changes, and flags dishes whose food cost passes the target.
Schema changes.
Unit price of an ingredient. Read it from the existing price_points  normalized unit
price, in the item's base unit.
Policy latest  (default): the price point with the most recent invoice date, across all
suppliers.
Policy avg_30d : the quantity-weighted mean over the last 30 days.
Outlier guard: if a new price is more than 3 times, or less than one third of, the item's
90-day median, do not use it for costing. Send it to /review . Most such cases are unit
or extraction errors.
Cost formula.
Input Limit
Invoices No limit; show the date of the latest invoice per
supplier
Table Change
recipes Add kind : menu_item, sub_recipe. Add portions
(number of portions one batch makes; 1 for a plated
dish)
recipe_lines Add yield_pct  (usable share after trim and
cooking, default 1.0000, must be greater than 0 and
at most 1)
plate_costs Add effective_at , is_current , lines_priced ,
lines_total , calc_version , trigger_event_id .
Keep week_of . Add a unique partial index on
(restaurant_id, recipe_id) where is_current
findings.type menu_margin  already exists; reuse it

<!-- page 12 -->

For an ingredient line:
  purchased_qty = qty_in_base_units / yield_pct
  line_cost     = purchased_qty * unit_price
For a sub-recipe line:
  line_cost     = qty_in_sub_recipe_yield_unit * (sub_recipe_batch_cost / 
sub_recipe_yield_qty)
batch_cost     = sum of line_cost
plate_cost     = batch_cost / portions
food_cost_pct  = plate_cost / menu_price        (menu_price excludes tax)
margin_dollars = menu_price - plate_cost
Worked example. Use this as a unit test. Burger, 1 portion, menu price $12.00.
Plate cost = $2.4406, shown as $2.44. Food cost = 2.4406 / 12.00 = 20.3%. Margin = $9.56.
Recompute flow.
1. On price_point.created , look up the recipes that use that canonical item, directly
or through sub-recipes. Keep a reverse index so this is one query.
2. Recompute sub-recipes before the recipes that use them (topological order). Reject a
recipe save that would create a cycle.
3. For each recipe, write a new plate_costs  row, set is_current  on it, and clear it on
the previous row, in one transaction.
4. Store basis : for each line, the price point id, unit price, conversion factor, and yield
used.
Line Quantity Yield Unit price Line cost
Ground beef 150 g 1.00 $0.0120
per g
$1.8000
Bun 1 each 1.00 $0.4500
each
$0.4500
Onion 30 g
usable
0.85 $0.0020
per g
30 / 0.85 = 35.2941 g,
cost $0.0706
House sauce (sub-recipe,
$3.00 per 500 g batch)
20 g n/a $0.0060
per g
$0.1200

<!-- page 13 -->

5. Emit plate_cost.changed  when the cost moves by at least $0.01.
Menu price. Use pos_items.price . If it is missing, use the median of net / qty for that
item over the last 14 days. If neither exists, show the cost with no percentage.
Alert rule. Open one menu_margin  finding for a dish when all of these hold:
The recipe is confirmed and every line is priced ( lines_priced = lines_total ).
food_cost_pct  is at least the target plus 0.02. The target is target_food_cost_pct ,
with an optional per-category override.
The dish sold at least 10 units in the last 28 days.
No finding for this dish is already open.
Close the finding when food_cost_pct  falls below the target plus 0.01.
weekly_impact = (plate_cost_now - plate_cost_28_days_ago) * 
units_sold_last_28_days / 4
Reprice proposal ( menu_reprice ).
price_at_target = plate_cost / target_food_cost_pct, rounded up to the 
restaurant's price ending
price_cap       = menu_price * 1.10, rounded down to the price ending
suggested_price = min(price_at_target, price_cap)
Example: plate_cost $4.20, menu_price $12.00, target 0.30, price ending .00
  price_at_target = 4.20 / 0.30 = $14.00
  price_cap       = 13.20 -> $13.00
  suggested_price = $13.00, giving food cost 4.20 / 13.00 = 32.3%
When the cap applies, the proposal states the food cost the suggested price reaches. A
person changes the price in the POS; FullHouse never does.
Partial recipes. A recipe with any unpriced line shows its cost as "at least $X" and lists the
missing lines. It raises no alert.
Limits in this version. Modifiers and add-ons are not costed. Labor is not part of plate
cost.

## 6. Module B: usage variance without counts

Module B compares what recipes say was used with what was bought, per ingredient, and
ranks the unexplained gap in dollars per week. It needs no inventory counts.

<!-- page 14 -->

Why it works without counts. Over any window, purchases equal usage plus the change
in stock. Stock change is unknown without counts, but it is bounded: it is rarely more than
one delivery. So the module uses a 28-day window and treats one typical delivery as noise.
Formulas. All quantities are in the ingredient's base unit. The window is the last 28
business dates.
theoretical_qty = sum over sold lines of (qty_sold * 
purchased_qty_of_ingredient_per_portion)
                  - purchased_qty per portion comes from Module A, including 
yield and sub-recipes
                  - include lines where voided = false, and lines where 
voided_after_fire = true
purchased_qty   = sum of invoice line quantities dated in the window (credits 
subtract)
excess_qty      = purchased_qty - theoretical_qty
noise_qty       = median quantity per delivery of this ingredient over the 
last 8 weeks
unexplained_qty = excess_qty - noise_qty
weekly_dollars  = unexplained_qty * average_unit_price_in_window / 4
Worked example. Use this as a unit test. Chicken breast, 28-day window.
Coverage rule. The result is only as good as the recipes behind it.
Value Amount
theoretical_qty 82,000 g
purchased_qty 104,000 g
excess_qty 22,000 g
noise_qty (median delivery) 9,000 g
unexplained_qty 13,000 g, which is 15.9% of theoretical
average unit price $0.0088 per g
weekly_dollars 13,000 * 0.0088 / 4 = $28.60

<!-- page 15 -->

recipe_coverage = net sales of POS items with a confirmed, fully priced 
recipe / total net sales, in the window
An ingredient can be marked track_usage = false  (for example salt or fryer oil). It is
then left out.
Finding rule. Open one usage_variance  finding for an ingredient when all of these hold
on two consecutive weekly runs:
The module is Live.
unexplained_qty  is greater than 0 and at least 5% of theoretical_qty .
weekly_dollars  is at least $25. The threshold is a restaurant setting.
Show at most 5 findings in the digest, ranked by weekly_dollars .
Data-gap rule. If purchased_qty  plus noise_qty  is below 85% of theoretical_qty ,
open a usage_data_gap  finding instead. The likely causes are recipe quantities that are
too high, or purchases that never reached FullHouse, such as cash buys.
Schema.
recipe_coverage Module state Behavior
Below 0.80 Setup Show the list of top-selling items that still need
recipes. Show no variance numbers
0.80 to below 0.90 Learning Show the variance table, labeled as an estimate.
Open no findings
0.90 or above Live Show the table and open findings
Table Columns
usage_variance canonical_item_id, window_start, window_end,
theoretical_qty, purchased_qty, noise_qty,
unexplained_qty, avg_unit_price, weekly_dollars,
recipe_coverage, state: setup, learning, live; basis
(json), calc_version. Unique on (restaurant_id,
canonical_item_id, window_end)
findings.type Add usage_variance  and usage_data_gap

<!-- page 16 -->

Job. usage_variance  runs weekly after the week's sales and invoices have synced, and
again when a recipe is confirmed or changed.
What the module cannot say. It cannot separate waste, over-portioning, and theft. It
shows where the gap is and how large. Each finding lists three checks for the owner:
weigh five portions of the top dish that uses the ingredient, check the last delivery
against its invoice, and review the recipe quantity.

## 7. Module C: ordering and prep suggestions

Module C turns the item demand forecast into draft purchase orders and daily prep
sheets. It extends the v2 po/draft  and prep  jobs; it does not replace their tables.
Item demand forecast. Forecast daily quantity per POS item with the method in section 8,
written to forecasts  as p10, p50, and p90. For items that average under 5 units a day,
forecast the category total and split it by each item's share of the last 28 days.
Ingredient demand per day.
ingredient_p50 = sum over items of (item_p50 * purchased_qty_per_portion)
ingredient_p10 = sum over items of (item_p10 * purchased_qty_per_portion)
ingredient_p90 = sum over items of (item_p90 * purchased_qty_per_portion)
sigma_day      = (ingredient_p90 - ingredient_p10) / 2.563
Summing item percentiles assumes items rise and fall together within a day. That is
deliberately cautious. The constant 2.563 is the width of a normal 10th-to-90th percentile
range in standard deviations.
Order quantity. The coverage period is every business date from this order's delivery
date up to, but not including, the supplier's next delivery date.

<!-- page 17 -->

period_demand   = sum(ingredient_p50 over period) + z * sqrt(sum(sigma_day^2 
over period))
need            = period_demand * (1 + loss_factor)
raw_qty         = need - est_on_hand_at_delivery
order_qty       = 0 if raw_qty <= 0, else ceil(raw_qty / pack_size) * 
pack_size
z           = 0.8416 (80% service level) when shelf_life_days <= 5
            = 1.6449 (95% service level) otherwise
loss_factor = unexplained_qty / theoretical_qty from Module B, limited to the 
range 0 to 0.15
            = 0 when Module B is not Live for this ingredient
If shelf_life_days  is set and shorter than the coverage period, sum demand over only
the first shelf_life_days  dates. Summing variances across days assumes days are
independent, which slightly understates the buffer in a busy week.
Worked example. Use this as a unit test. Chicken breast, delivery Tuesday, next delivery
Friday, shelf life 4 days, pack size 4,535.92 g (a 10 lb case).
sum of p50            = 15,000 g
sqrt(sum of sigma^2)  = 1,689.9
period_demand         = 15,000 + 0.8416 * 1,689.9 = 16,422 g
need (loss 0.05)      = 17,243 g
on hand at delivery   = 3,000 g
raw_qty               = 14,243 g = 3.14 cases
order_qty             = 4 cases
Estimated stock without counts. Keep a running estimate per ingredient in
stock_estimates .
Day p10 p50 p90 sigma_day
Tue 3,000 g 4,000 g 5,000 g 780.3
Wed 3,800 g 5,000 g 6,200 g 936.4
Thu 4,500 g 6,000 g 7,500 g 1,170.5

<!-- page 18 -->

est_on_hand = max(0, previous_est + received_qty - theoretical_usage * (1 + 
loss_factor))
est_on_hand_at_delivery = max(0, est_on_hand - expected usage at p50 between 
now and delivery)
Starting value: half the ingredient's median delivery quantity. Label the first two
drafts for each supplier "Check quantities".
A person's edit corrects the estimate. If the draft says 4 cases and the person orders
3, add one case to est_on_hand .
Each draft line has two one-tap corrections: "We're out" sets the estimate to 0. "We
have plenty" sets the line to 0 and sets the estimate to need .
These are corrections, not counts. Nothing asks the user to count stock.
Draft purchase order ( purchase_order  proposal). Create one draft per supplier before
each order_cutoff_time , by the lead time set in the workflow (default 4 hours). Each
line stores its basis: the forecast run, the period, the estimate, and the pack rounding. If a
supplier's total is under minimum_order_amount , say so on the draft. A person reviews,
edits, and sends.
Prep sheet ( prep_sheet  proposal). Generate one per business date at 04:30 local time.
par_today = p50_usage + 0.6745 * sigma_day      (the 75th percentile of 
today's usage)
The sheet lists each prepped item with its par, batch size, and shelf life, plus blank "On
hand" and "Prep" columns to fill on paper. The cook preps up to par. FullHouse does not
record what was on hand.
Schema.
Table Columns
stock_estimates canonical_item_id, est_on_hand, as_of,
last_correction_at, basis (json). Unique on
(restaurant_id, canonical_item_id)
canonical_items Add shelf_life_days  (nullable), track_usage
(default true), pack_size  if not already stored
po_lines Add suggested_qty , basis  (json)

<!-- page 19 -->

Accuracy tracking. After each day closes, write forecast_errors . Report weighted
absolute percentage error over the last 28 days:
WAPE = sum(|actual - p50|) / sum(actual)
Minimum data. Module A is live for the ingredient, the supplier has a delivery schedule,
and there are at least 4 weeks of item sales.

## 8. Module D: labor forecasting and draft schedules

Module D forecasts demand in 15-minute intervals with a range, converts it to heads
needed per role, drafts a weekly schedule, and suggests cuts or extensions during
service.

### 8.1 Schema

Table Columns
labor_roles key (for example server, host, bartender, line_cook,
prep, dish), name, is_tipped
employees external_id, display_name, role_keys (list),
hourly_rate (nullable, restricted),
min_weekly_hours, max_weekly_hours,
desired_weekly_hours, is_minor, active
employee_availability employee_id, weekday, start_time, end_time
employee_time_off employee_id, start_at, end_at
labor_standards role_key, driver_metric: covers, orders, items, net;
driver_filter (json: channels, POS categories),
units_per_labor_hour, min_on_floor,
open_lead_minutes, close_lag_minutes, source:
learned, manual; sample_size, effective_from

<!-- page 20 -->


### 8.2 Forecast method

The forecast is built in two steps: a daily total per channel, then a split into 15-minute
intervals. Three model stages share one Forecaster  interface.
Stage 1, baseline (needs 4 weeks). For each weekday and channel:
base   = median of the same weekday over the last 6 normal weeks
trend  = mean daily total, last 2 weeks / mean daily total, the 4 weeks 
before; limited to 0.80 to 1.25
p50    = base * trend
p10    = 10th percentile of the same weekday over the last 12 normal weeks * 
trend
p90    = 90th percentile of the same weekday over the last 12 normal weeks * 
trend
Table Columns
labor_rules weekly_overtime_hours, daily_overtime_hours,
min_rest_hours, min_shift_hours, max_shift_hours,
max_hours_per_day, max_consecutive_days,
break_after_hours, break_minutes,
min_paid_hours, advance_notice_days,
allow_on_call, minor_rules (json), confirmed_by,
confirmed_at. All nullable until confirmed
labor_requirements business_date, interval_start (30-minute), role_key,
units_p50, units_p90, need_p50, need_p90, run_id
schedules week_of, status: draft, published; version,
proposal_id, hours_total, cost_total, labor_pct_p50,
generated_at, published_at, published_by
shifts schedule_id, employee_id (nullable for an open
shift), role_key, start_at, end_at, kind: regular, flex;
break_minutes, locked, note
labor_adjustments business_date, kind: cut, extend, call_in; role_key,
shift_id, suggested_time, savings_estimate, reason
(json), status: proposed, accepted, dismissed,
expired; expires_at

<!-- page 21 -->

A normal week excludes closures, holidays, and days flagged as POS outages. With fewer
than 12 weeks, use the weeks available. Then order the three values so p10 <= p50 <= p90.
Stage 2, driver multipliers (needs 8 weeks). Adjust the baseline for known outside
conditions.
multiplier(driver) = median(actual / baseline_p50) on past days with that 
driver, limited to 0.60 to 1.60
forecast           = baseline * product of the multipliers that apply to the 
day
Each forecast row stores which multipliers applied and their values. The UI shows them.
Stage 3, quantile model (needs 52 weeks, or pooled data). A gradient-boosted quantile
regression predicting p10, p50, and p90 from the same drivers plus lags. Sort the three
outputs so p10 <= p50 <= p90. Build it only after stages 1 and 2 are live.
Driver Source Rule
Rain Weather forecast Applies when precipitation probability is
50% or more during opening hours. Needs 6
past rainy days
Temperature
band
Weather forecast Cold, mild, or hot by the local terciles of
daily high. Needs 6 past days per band
Holiday Built-in calendar by
country
Per named holiday, plus the day before and
after. With no history, widen the range by 1.5
and ask the owner
Local event Owner entry or import Size small, medium, or large. Learned per
size after 6 events; before that the owner
picks the expected effect
Promotion Owner entry Learned per promotion type after 6 uses
Reservations Booking connector, if
any
Multiplier = 1 + 0.5 * (covers booked now /
typical covers booked at this lead time - 1),
dine-in only
Closure Owner entry Forecast is 0. The day is excluded from
training

<!-- page 22 -->

Promotion rule. A later stage replaces an earlier one for a restaurant only if it has lower
WAPE and lower pinball loss on a rolling backtest of the last 8 weeks. Recheck weekly.
pinball_loss(q, actual, forecast) = q * (actual - forecast)        if actual 
>= forecast
                                  = (1 - q) * (forecast - actual)  otherwise
Interval split.
share(t)        = mean share of the day's volume in interval t, same weekday 
and channel, last 8 weeks,
                  smoothed with a 3-interval moving average and rescaled to 
sum to 1
interval_p50(t) = daily_p50 * share(t)
interval_p10(t) = daily_p10 * share(t) * r10(daypart)
interval_p90(t) = daily_p90 * share(t) * r90(daypart)
r10  and r90  are the 10th and 90th percentiles of actual interval volume divided by
(actual daily volume * share), per daypart. They widen the range because a single interval
is noisier than a full day.
Range calibration. Each week, measure the share of actual daily totals inside the p10 to
p90 range over the last 8 weeks. The target is 80%. If it is below 70% or above 90%, scale
both half-widths by the smallest factor that brings it to 80%.
Cold start. With under 4 weeks of data, the owner enters expected covers or orders per
daypart. Ranges are plus and minus 30%. Everything is labeled "Your estimate".

### 8.3 Per-role standards and requirements

A standard says how many units of work one person in a role handles per hour.
Role Default driver Default filter
Server, host, dish covers Dine-in
Bartender items POS categories mapped to the bar
Line cook items Food categories, all channels
Expo or packer orders Takeout and delivery
Prep none Fixed blocks set by the owner

<!-- page 23 -->

Learning a standard. For each 30-minute interval in the last 8 weeks with at least one
person clocked in for the role and at least one unit:
rate = units / (heads * 0.5)
units_per_labor_hour = 70th percentile of rate over intervals whose units are 
above the role's median
Using the busier half keeps slow periods from lowering the standard. Learn only with at
least 4 weeks of punches and 100 qualifying intervals. Otherwise the owner enters the
standard. An owner-entered value always wins.
Requirement.
need(role, t) = max(min_on_floor, ceil(units(t) / (units_per_labor_hour * 
0.5)))
Compute it with p50 units and with p90 units. Units for a 30-minute interval are the sum of
its two 15-minute forecast intervals. Add min_on_floor  heads from open_lead_minutes
before opening to close_lag_minutes  after closing.
Worked example, use as a unit test: 24 covers forecast in a 30-minute interval, standard 16
covers per labor hour. Capacity per head is 8. need_p50  = ceil(24 / 8) = 3. At p90 of 31
covers, need_p90  = ceil(31 / 8) = 4.

### 8.4 Staffing to a range

Schedule regular shifts to need_p50 . Cover the gap up to need_p90  with flexibility, not
padding.
If allow_on_call  is true: add up to max_flex_shifts_per_day  (default 1) shifts of
kind flex  over the peak gap.
If it is false or unset: mark the regular shift ending nearest the peak as "may extend" in
its note. Add no flex shifts.
Show both lines in the UI so the manager sees what the range costs.

### 8.5 Schedule generation

Generate the draft in two stages with a constraint solver (OR-Tools CP-SAT). Total time
limit 30 seconds; return the best feasible result found.

<!-- page 24 -->

Stage 1, shifts. Per role and business date, choose how many of each candidate shift to
use. Candidates start on the 30-minute grid and run from min_shift_hours  to
max_shift_hours  in 30-minute steps.
minimize  sum over t of (10 * under(t) + 1 * over(t)) + 0.5 * 
number_of_shifts
under(t) = max(0, need_p50(t) - coverage(t))
over(t)  = max(0, coverage(t) - need_p50(t))
Stage 2, people. Assign employees to the chosen shifts.
A hard constraint whose rule value is unset is skipped, and the draft lists the unset rules
at the top. Weights are settings. Never use pay rate, age, or any personal attribute as a
selection criterion.
Cost.
paid_hours    = shift length - unpaid break minutes / 60
shift_cost    = paid_hours * hourly_rate          (role median when the rate 
is unknown)
labor_pct_p50 = total shift cost / forecast net sales p50
Type Constraint
Hard Employee holds the role; shift is inside availability;
no time off; no overlapping shifts
Hard Daily hours at most max_hours_per_day ; rest
between shifts at least min_rest_hours ; days in a
row at most max_consecutive_days
Hard Minor rules; weekly hours at most
max_weekly_hours ; locked shifts stay as they are
Soft, weight 50 Shift left unassigned. It becomes an open shift
Soft, weight 20 per hour Hours over the overtime thresholds
Soft, weight 2 per hour Distance from desired_weekly_hours
Soft, weight 1 Shift differs from the same employee's shift last
week

<!-- page 25 -->

Output. One schedule_draft  proposal per week. The manager edits it, locks shifts,
regenerates around locks, and publishes. Publishing sets the status and offers PDF and
CSV exports. FullHouse does not notify staff. If publishing is later than
advance_notice_days  before the week starts, warn before confirming.

### 8.6 Intraday reforecast

Every 15 minutes during opening hours, compare the day so far with the forecast and
adjust the rest of the day.
elapsed_share = share of the day's forecast volume expected by now
pace          = actual volume so far / forecast p50 volume so far
weight        = min(1, elapsed_share / 0.40)
multiplier    = weight * pace + (1 - weight), limited to 0.50 to 1.75
revised(t)    = forecast(t) * multiplier, for each remaining interval and 
each of p10, p50, p90
Make no adjustment while elapsed_share  is under 0.15. Recompute
labor_requirements  for the remaining intervals from the revised values.
Worked example, use as a unit test: forecast p50 so far is 60 covers of 200 for the day, so
elapsed_share  is 0.30. Actual so far is 75, so pace  is 1.25. weight  = 0.30 / 0.40 = 0.75.
multiplier  = 0.75 * 1.25 + 0.25 = 1.1875. The remaining p50 of 140 covers becomes 166.25.
Suggestion rules.
Skip a cut that would pay less than min_paid_hours ; it saves nothing.
savings_estimate  = hours removed * hourly rate.
Kind Condition Who
Cut Heads on the clock exceed the revised
need_p90  in each of the next three 30-
minute intervals, and the cut keeps
heads at or above min_on_floor
First the person closest to overtime
this week, then the person on shift
longest today
Extend Revised need_p50  exceeds heads in
each of the next two intervals, and no
later shift covers it
The person whose shift ends soonest,
if it stays within daily limits and
overtime thresholds
Call in Same as extend, when nobody can
extend and a flex shift exists
The flex shift

<!-- page 26 -->

At most one open suggestion per role. A suggestion expires after 20 minutes. After a
dismissal, make no new suggestion for that role for 60 minutes.
Suggestions go to the manager only, on the Today screen and by push notification.
The manager tells the employee.
Turn intraday suggestions on only when daypart WAPE is 25% or lower over the last 4
weeks.

### 8.7 States and metrics

Track and display: WAPE by daypart, range coverage, scheduled versus actual hours, labor
percentage versus target, intervals understaffed against actual volume, and suggestions
accepted versus dismissed.

## 9. Module E: channel profitability

Module E shows what each menu item earns per unit on each sales channel after
commissions, fees, packaging, and food cost, and proposes prices for items that lose
money.
Per-unit contribution. Compute per order line, then average per item, channel, and
platform over the last 28 business dates.
State Condition Behavior
Setup Under 4 weeks of timestamped orders,
or no roles and standards set
Show the setup checklist
Learning 4 to under 8 weeks Baseline forecast, owner-entered
standards, drafts labeled as estimates,
no intraday suggestions
Live 8 weeks or more All features, subject to the intraday
accuracy rule

<!-- page 27 -->

unit_revenue      = line net / line qty                      (after 
restaurant-funded discounts, before tax)
line_share        = line net / order net
commission_unit   = order commission * line_share / line qty
processing_unit   = order processing fee * line_share / line qty
fixed_unit        = (fixed fee per order + packaging cost per order) * 
line_share / line qty
unit_contribution = unit_revenue - commission_unit - processing_unit - 
fixed_unit - plate_cost
plate_cost  is the Module A cost current on the order's business date. Labor is not
allocated.
Where fees come from. Match channel_payouts  to sales_orders  on the platform's
order id. A matched order uses its actual commission and fees. An unmatched order uses
channel_terms  in effect on that date. Show the match rate. Under 80% matched, label
the page "Based on your contract rates".
Finding rule. Open one channel_margin  finding per item and platform when all of these
hold:
The item sold at least 10 units on that platform in the window, and its recipe is fully
priced.
unit_contribution  is below 0, or below half of the item's dine-in contribution.
target_contribution = dine-in menu price - plate_cost       (default; a 
restaurant setting can replace it)
weekly_impact       = (target_contribution - unit_contribution) * 
units_in_window / 4
Reprice proposal ( channel_reprice ).
price = (plate_cost + fixed_unit + target_contribution) / (1 - commission_pct 
- processing_pct)
suggested_price = price rounded up to the restaurant's price ending
Worked example, use as a unit test: plate cost $4.00, fixed and packaging $0.50 per unit,
dine-in price $9.50 so target contribution $5.50, commission 25%, processing 3%.

<!-- page 28 -->

price             = (4.00 + 0.50 + 5.50) / (1 - 0.25 - 0.03) = 10.00 / 0.72 = 
$13.89
suggested_price   = $14.00
check             = 14.00 * 0.72 - 4.50 = $5.58 contribution, at or above the 
$5.50 target
Every proposal carries this note: "Check your agreement with the platform before
changing prices." A person changes the price on the platform.
Schema.
Job. channel_margins  runs weekly, and again after payouts.synced  or
plate_cost.changed .
Minimum data. Module A is live, orders carry a channel, and either payout reports or
channel_terms  exist for each platform in use.

## 10. UI design

Every screen answers one question in its first sentence, with a number, and shows how
the number was computed on request. The rules below are requirements; each has a test
in section 12.

### 10.1 Navigation

The digest is the home screen. Each module has one card on it and one detail page.
Table Columns
channel_item_margins pos_item_id, channel, platform (nullable),
window_start, window_end, units, unit_revenue,
commission_unit, processing_unit, fixed_unit,
plate_cost, unit_contribution, contribution_pct,
payout_match_rate, basis (json), calc_version.
Unique on (restaurant_id, pos_item_id, channel,
platform, window_end)
findings.type Add channel_margin
proposals.kind Add channel_reprice

<!-- page 29 -->

Use the navigation labels exactly as written. Use the repository's existing front-end
framework, component library, and design tokens.

### 10.2 Page template

Every module page uses this order, top to bottom. Do not add sections.
+----------------------------------------------------------------------+
| Page title                                   Updated 10:42  [state]  |
| Header sentence: the answer, with a number, a unit, and a period.    |
+----------------------+----------------------+------------------------+
| Tile 1               | Tile 2               | Tile 3                 |
| value, label, change | value, label, change | value, label, change   |
+----------------------+----------------------+------------------------+
| Primary chart: one chart. Its title states the finding.              |
| [View as table]                                                      |
+----------------------------------------------------------------------+
| Table: sorted by dollars per week, largest first. 10 rows, Show all. |
| Each row: name | key numbers | trend | action                        |
+----------------------------------------------------------------------+
Nav item Page Question it answers
Digest /digest  (existing) What needs my attention this
week, in dollars?
Menu costs /menu-costs , /menu-costs/{recipe} Which dishes cost too much to
make?
Usage /usage , /usage/{item} Which ingredients are going
missing?
Ordering and
prep
/ordering , /prep/{date} What should I order and prep?
Labor /labor/week/{week} , /labor/today ,
/labor/standards
How many people do I need, and
when?
Channels /channels Which delivery items lose
money?
Review /review  (existing) What is waiting for my
approval?

<!-- page 30 -->


### 10.3 How to make each screen clear

Lead with the answer. The header sentence states the result. It is never a description
of the page.
Use one unit for comparison. Rank everything by dollars per week. Show percentages
second.
Show the comparison. A number always appears next to its target, its prior value, or
its range. A number alone is not allowed.
Explain every number. Clicking any derived number opens the Why panel (10.7).
State confidence in words. Use "Estimate (5 weeks of data)" or "Based on 14 weeks of
data". Do not show confidence as a bare percentage.
Show ranges as ranges. Write "140 to 190 covers, most likely 165". Never show only the
middle value for a forecast.
Do not rely on color. Every status has an icon and a word. Charts use labels or
patterns as well as color.
Say what to do. Each finding ends with one recommended action as a button.
Keep the data visible while acting. Approve and edit in place or in a side panel. Do not
navigate away from the list.
Allow undo. Approve, dismiss, and publish each show an Undo option for 10 seconds.
Element Limit
Header sentence At most 2 short sentences and 20 words in total.
Contains a number
Tiles At most 3. Each has one value, a label of at most 3
words, and the change against the prior period
Charts above the table Exactly 1. Other charts live on detail pages
Table columns At most 7 on desktop, 3 on a phone
Table rows 10 by default, then "Show all"
Actions per row At most 2 buttons, plus a menu for the rest
Clicks from digest card to the action At most 2

<!-- page 31 -->


### 10.4 How to keep each screen concise

Remove any element that does not change a decision. Test: if the number changed,
would the user act differently?
Show totals on the list page and the breakdown on the detail page. Do not show both
on one page.
Collapse rows under the materiality threshold into one line: "12 more items under $5 a
week".
Do not repeat a number in a tile, the chart, and the table. Each number appears once
per page, except the header total.
No introductory paragraphs, tips, or tours on live pages. Help text appears only in the
setup state and in the Why panel.

### 10.5 Writing rules

All interface text follows these rules. Add them to CLAUDE.md  so they persist across
sessions.
Rule Requirement
Sentences Subject, verb, number. At most 20 words. One idea
each
Labels At most 3 words, sentence case, no abbreviations
except $, %, hr
Buttons One verb, or verb plus noun: Approve, Edit,
Dismiss, Publish, Regenerate, Send order
Numbers Dollars without cents at $100 or more, with cents
below. Percentages to one decimal. Always include
the period: "a week", "last 28 days"
Dates and times "Tue Nov 17" and the restaurant's 12 or 24 hour
setting. Never a raw timestamp
Errors State what failed and what the user can do. "Square
has not synced since 9:10. Reconnect in Settings."
Empty results State the fact. "No dishes are over target."

<!-- page 32 -->


### 10.6 Chart rules

The chart title states the finding: "Burger cost per plate rose 14% in 8 weeks", not
"Cost over time".
Rule Requirement
Banned words smart, powerful, seamless, insights, optimize,
unlock, leverage, AI-powered, magic, effortless,
robust, delight
Banned forms Exclamation marks, emoji, questions as headings,
greetings, praise, apologies
Internal term Text shown to the user
Theoretical usage Expected use
Variance, unexplained_qty Unexplained use
p50 Expected
p10 to p90 Likely range
WAPE Average miss
Unit contribution Kept per item
Plate cost Cost per plate
need_p50, need_p90 People needed, People needed if busy
Write this Not this
3 dishes are over your 30% food cost target.
Cost: about $212 a week.
Welcome to your menu insights dashboard
Chicken rose 9% since Oct 20. Burger
margin fell $0.31.
We noticed some exciting changes in your
costs
Cut one server at 8:30. Saves about $42. Our smart engine recommends optimizing
your floor staff
Estimate (5 weeks of data) Confidence: 62%

<!-- page 33 -->

Label both axes with units. Start bar charts at zero.
Label series directly on the chart when there are 3 or fewer. Use at most 4 series.
Draw a target as a dashed line with its value labeled.
Draw a forecast as a solid line for Expected inside a shaded band for the Likely range.
Draw actuals as dots.
Mark the current time on any intraday chart with a vertical line labeled "Now".
Tooltips show the exact value, the unit, the date, and the source.
Every chart has a "View as table" toggle and a one-sentence text summary for screen
readers.
Do not use pie charts, 3D, dual y-axes, or stacked areas.
Use the chart library already in the repository. If there is none, ask before adding one.

### 10.7 The Why panel

Clicking any derived number opens a side panel. It is the main way the UI stays informative
without crowding the page.
The panel reads the stored basis  of the row. It does not recompute.

### 10.8 Figures and screens per module

A. Menu costs
Part Content
Result The number, its unit, and its period
Formula One line in plain words: "Cost per plate = sum of
each ingredient's quantity x its latest price"
Inputs A table of each input with its value and a link to its
source: the invoice, the recipe line, the forecast day
Data basis How much data it rests on: "9 weeks of sales, 14
invoices"
Freshness When it was computed and when the inputs last
changed
Fix A link to correct an input: edit the recipe, review
the invoice, change the standard

<!-- page 34 -->

Header: "{n} dishes are over your {target}% food cost target. Cost: about ${x} a
week."
Tiles: Dishes over target. Average food cost (weighted by sales). Largest ingredient
increase, last 28 days.
Table columns: Dish, Price, Cost per plate, Food cost %, Change in 28 days, Sold in 28
days, Action (Review price).
B. Usage
Header: "About ${x} a week of ingredients is unexplained, across {n} items."
Tiles: Unexplained use a week. Recipe coverage. Items flagged.
Table columns: Ingredient, Expected use, Purchased, Unexplained use, Dollars a week,
Trend, Action (See checks).
Figure Type Shows Where
A1 Horizontal bars,
sorted, top 15
Food cost % per dish, with the
target as a dashed line
List page, primary
chart
A2 Scatter with median
lines
Units sold (x) against margin per
plate in dollars (y). Shows which
dishes to reprice and which to
promote
List page, behind a
"Menu map" tab
A3 Step line, 26 weeks Cost per plate over time, with
the menu price as a second line
and a marker at each ingredient
price change
Dish page, primary
chart
A4 Horizontal bars,
sorted
Cost per ingredient line, each
labeled with its change in 28
days
Dish page
A5 Sparkline, 12 weeks Cost per plate trend Each table row
Figure Type Shows Where
B1 Horizontal bars, sorted,
top 10
Unexplained dollars a week per
ingredient
List page, primary
chart

<!-- page 35 -->

C. Ordering and prep
Header: "{n} orders to review. Next cutoff: {supplier}, {day} {time}."
Tiles: Orders waiting. Next cutoff. Average miss, last 28 days.
Order table columns: Item, Suggested, Pack, Expected use, Estimated on hand, Line
total, Corrections (We're out, We have plenty).
Prep sheet: a print layout, one page per station, large type, columns Item, Par, Batch
size, Shelf life, On hand, Prep.
D. Labor: Week
Header: "Draft for {week}: {hours} hours, ${cost}, {pct}% of expected sales. Target
{target}%."
Tiles: Scheduled hours against last week. Labor % against target. Open shifts and
understaffed intervals.
Figure Type Shows Where
B2 Two lines, 8 weekly
points, gap shaded
Expected use against
purchased quantity per 28-day
window, with the one-delivery
noise band
Ingredient page,
primary chart
B3 Horizontal bars The dishes that use the
ingredient, by expected use
Ingredient page
B4 Progress bar with a
marker at 90%
Recipe coverage against the
level needed for findings
Setup and learning
states
Figure Type Shows Where
C1 Line with band, 14 days
back and 14 ahead
Daily demand: actual dots,
Expected line, Likely range
band, and driver markers such
as rain or a holiday
Ordering page,
primary chart
C2 Step line with delivery
markers
Estimated stock of one
ingredient through the
coverage period, against zero
Order line Why
panel
C3 Line, 12 weeks Average miss per week Ordering page,
"Accuracy" tab

<!-- page 36 -->

Figure Type Shows Where
D1 Bars with range
whiskers, 7 days
Expected covers or orders per
day with the Likely range, last
week's actual as a marker, and
driver chips under each day
Week page, primary
chart
D2 Step chart per role, 30-
minute intervals
People needed (solid step),
People needed if busy (dashed
step), and people scheduled
(filled step). Gaps are hatched:
under in one pattern, over in
another
Day view inside the
Week page
D3 Schedule grid Rows are employees, columns
are days or hours, bars are
shifts labeled with role and
times. An Open shifts row sits
on top. A totals column shows
weekly hours with an overtime
icon
Week page, main
work area
D4 Bars with a dashed
target line, 7 days
Labor % per day, with a whisker
for the range of expected sales
Week page, "Cost"
tab

<!-- page 37 -->

Labor: week of Nov 23                              Draft v2   [Regenerate] 
[Publish]
Draft: 412 hours, $7,416, 28.4% of expected sales. Target 30%.
+-------------------+--------------------+--------------------------------+
| 412 hr            | 28.4%              | 2 open shifts                  |
| -18 vs last week  | target 30%         | 3 intervals understaffed       |
+-------------------+--------------------+--------------------------------+
| D1  Expected covers by day, with likely range                           |
|     Mon  Tue  Wed  Thu  Fri  Sat  Sun     [rain Fri] [holiday Thu]      |
+-------------------------------------------------------------------------+
| Roles: [All] [Server] [Line cook] [Bar] [Host] [Dish]      Day | Week   |
|            Mon      Tue      Wed      Thu      Fri      Sat     Hours   |
| Open       -        -        -        -        5-10 SV  -               |
| Ana R.     4-10 SV  off      4-10 SV  off      4-11 SV  4-11 SV  26     |
| Ben T.     11-7 LC  11-7 LC  off      11-7 LC  2-10 LC  2-10 LC  40 !   |
+-------------------------------------------------------------------------+
| D2  Fri, Server: people needed vs scheduled, 30-minute intervals        |
+-------------------------------------------------------------------------+
Grid interactions: drag a shift edge to change its time; click a shift to edit it in a dialog;
every drag action also works from the dialog and the keyboard. The header sentence and
tiles update after each edit. A lock icon keeps a shift through regeneration.
D. Labor: Today
Built for a phone held in one hand during service. One column. No table.
Header: "Running {x}% above forecast." or "On forecast." or "Running {x}% below
forecast."
Tiles: Covers so far against expected. People on now against people needed. Labor %
so far today.
Suggestion card, when one exists: one sentence, the saving, two buttons, and the
expiry time.
Figure Type Shows Where
D5 Cumulative line with band
and a Now line
Actual covers so far (solid),
original forecast (band), revised
forecast for the rest of the day
(dashed)
Today, primary
chart

<!-- page 38 -->

+--------------------------------------+
| Today, Fri Nov 20        Updated 7:15|
| Running 12% below forecast.          |
+--------------------------------------+
| Cut one server at 7:45.              |
| Saves about $42. Ana R. is closest   |
| to overtime.          Expires 7:35   |
| [ Accept ]   [ Dismiss ]   Why       |
+--------------------------------------+
| 96 covers        | 5 on, 4 needed    |
| expected 109     | servers           |
+--------------------------------------+
| D5  Covers today: actual vs forecast |
| D6  People on vs needed, to close    |
+--------------------------------------+
D. Labor: Standards
One row per role: driver, standard, minimum on floor, source (Learned or Set by you),
and the data behind it.
Figure D7: a histogram of observed units per labor hour for the role, with the current
standard as a labeled vertical line.
Changing a standard shows the effect before saving: "Raises Friday server hours from
38 to 42."
An "Accuracy" tab holds figure D8: average miss by daypart over 12 weeks, and the
share of days inside the Likely range against the 80% target.
E. Channels
Header: "{n} items lose money on {platform}. Cost: about ${x} a week."
Tiles: one per channel, showing Kept per item as a percentage of price.
Table columns: Item, Platform, Price, Fees, Cost per plate, Kept per item, Sold in 28
days, Action (Review price).
Figure Type Shows Where
D6 Step chart, Now to close People on the clock and
scheduled, against revised
people needed, per role
Today, below
D5

<!-- page 39 -->


### 10.9 Digest cards

Each module adds at most one card. A card has one sentence, one number, at most one
sparkline, and one button that opens the module page or the proposal. A module in the
setup state shows a card only until setup is done: "Add 6 recipes to turn on Usage."

### 10.10 States

Figure Type Shows Where
E1 Grouped horizontal bars
with a zero line, top 10
items by delivery units
Kept per item on each
channel
List page, primary
chart
E2 Waterfall Price, minus commission,
processing, fixed fees and
packaging, and food cost,
ending at Kept per item
Item Why panel and
reprice proposal
State What the page shows
Setup A checklist of what is missing, each item with a
button and a progress count. No charts, no
placeholder numbers
Learning The page with a banner: "Estimate. {n} weeks of
data; needs {m}." No findings
Live The full page
Loading Skeletons in the final layout. No spinners over a
blank page
Empty One sentence stating the fact, and nothing else
Stale A banner with the last update time and the fix.
Numbers stay visible, greyed
Error What failed, what still works, and a retry button

<!-- page 40 -->


### 10.11 Layout and accessibility

Design phone-first for Today, suggestion cards, and the digest. The schedule grid
needs a width of 1024 px; below that, show a read-only list by day.
Meet WCAG 2.1 AA: contrast, keyboard access to every action, visible focus, labels on
all inputs.
Touch targets are at least 44 by 44 px.
Never show hourly_rate  or shift cost per person to a role without wage access.
Show totals only.

### 10.12 Shared components

Build these once and reuse them on every page: HeaderSentence , StatTile ,
MoneyDelta , RangeText , ConfidenceLabel , StateBanner , SetupChecklist , WhyPanel ,
ProposalCard , SuggestionCard , ForecastChart , CoverageChart , ScheduleGrid ,
Waterfall , SortedBars , ViewAsTable .

## 11. API and background jobs

The API is read-mostly: pages read derived tables, and writes are limited to edits,
approvals, and job requests.
Conventions.
Follow the repository's existing routing, auth, and error format. Paths below are
defaults under the existing API prefix.
The tenant comes from the session, never from a path or body parameter.
Money and quantities are decimal strings. Times are ISO 8601 with offset. Business
dates are YYYY-MM-DD .
Lists use cursor pagination. Reads send an ETag built from computed_at .
Every POST accepts an Idempotency-Key  header.
Every response for a module includes state  (setup, learning, live), computed_at , and
stale  (true or false).
Endpoints.

<!-- page 41 -->

Method and path Purpose
GET /menu-costs Dishes with current cost, food cost %,
28-day change, units sold
GET /menu-costs/{recipe_id} One dish: lines, cost history, open
finding
PUT /recipes/{recipe_id} Edit a recipe and its lines. Triggers
recost
GET /usage Ingredients with expected use,
purchased, unexplained use, dollars a
week
GET /usage/{item_id} One ingredient: 8 weekly windows,
dishes that use it
GET /ordering/drafts Open purchase order drafts with lines
and basis
POST /ordering/lines/{line_id}/correction Body: out  or plenty . Updates
stock_estimates
GET /prep/{business_date} The prep sheet for a date
GET /forecast/daily?from=&to=&metric= Daily forecast with range, drivers
applied, and actuals
GET /labor/weeks/{week_of} Forecast, requirements, draft or
published schedule, totals
POST /labor/weeks/{week_of}/generate Queue schedule generation. Returns a
job id
GET /jobs/{job_id} Job status: queued, running, done,
failed
POST /labor/shifts , PATCH and DELETE
/labor/shifts/{shift_id}
Edit the draft. Returns updated totals
POST
/labor/schedules/{schedule_id}/publish
Publish. Returns warnings that need
confirmation
GET /labor/today Pace, revised forecast, people on,
revised requirements, open suggestion

<!-- page 42 -->

Proposals are approved, edited, and dismissed through the existing proposal endpoints.
Jobs.
Method and path Purpose
POST /labor/adjustments/{id}/accept ,
/dismiss
Record the manager's decision
GET and PUT /labor/standards , /labor/rules Read and edit standards and rules
GET and POST /drivers List and add events, promotions,
closures
GET /channels Items by channel and platform with per-
unit breakdown
GET /why/{table}/{row_id} The stored basis of a derived row, for
the Why panel
Job Trigger Idempotency
key
Writes
recost price_point.created ,
recipe saved
recipe id + input
hash
plate_costs , menu_margin
findings
rollup_intervals sales.synced business date interval_actuals ,
sales_items
forecast_daily Nightly, 04:30 local target date
range + inputs
hash
forecasts , daily rows in
forecast_intervals ,
forecast_runs
forecast_intervals After forecast_daily same forecast_intervals
forecast_errors After each business date
closes
business date forecast_errors
usage_variance Weekly; recipe confirmed window end usage_variance , findings
stock_estimate Nightly; invoice normalized;
correction
item id + as-of
date
stock_estimates
order_draft Lead time before each
supplier cutoff
supplier id +
delivery date
purchase_orders ,
po_lines , proposal
prep_sheet Nightly, 04:30 local business date prep_sheets , proposal

<!-- page 43 -->

New event types. sales.synced , punches.synced , payouts.synced ,
drivers.updated , plate_cost.changed , forecast.computed ,
requirements.computed , schedule.generated , schedule.published ,
adjustment.proposed , adjustment.decided , job.failed .
Connectors. Add adapters in this order, each behind the existing connector interface:
CSV import for orders, punches, and payouts; one POS with order-level data; a weather
source that provides both history and an hourly forecast; a delivery payout report. All are
read-only.

## 12. Testing, acceptance, and observability

Every formula in this document has a worked example, and each worked example is a
required unit test with the exact numbers shown.
Golden unit tests.
Job Trigger Idempotency
key
Writes
labor_standards Weekly role + week labor_standards  (learned
rows only)
labor_requirements After forecast_intervals ;
standard changed
business date +
run id
labor_requirements
schedule_generate User request; weekly
workflow
week + version schedules , shifts ,
proposal
intraday_reforecast Every 15 minutes during
opening hours
business date +
interval
intraday rows in
forecast_intervals ,
labor_requirements ,
labor_adjustments
channel_margins Weekly; payouts.synced ;
plate_cost.changed
window end channel_item_margins ,
findings
driver_sync Every 6 hours date range +
source
demand_drivers
Test Source Expected
Business date Section 4 Sat 01:30 with a 04:00 start gives Friday; Sat 04:00
gives Saturday

<!-- page 44 -->

Compare money and quantities exactly as decimals. Compare forecast values within 0.5.
Property tests.
For every forecast row, p10 <= p50 <= p90, and none is negative.
Interval shares for a day sum to 1 within 0.001.
Plate cost never decreases when a single ingredient price increases and nothing else
changes.
need  is never below min_on_floor  and never decreases when units increase.
A generated schedule never violates a hard constraint. Check with a validator that is
separate from the solver.
Running any job twice with the same inputs leaves the same rows.
A sub-recipe cycle is rejected on save.
Tenant and safety tests.
Each new table: a query under restaurant A returns no rows of restaurant B. Add one
case per table.
The agent tool layer cannot write to any new table except through proposals . Add
one forbidden-write case per table.
Replaying a connector sync, a webhook, or an import creates no duplicate rows.
A user without wage access receives no hourly_rate  and no per-person cost from
any endpoint.
Test Source Expected
Plate cost Section 5 Burger costs $2.4406; food cost 20.3% at $12.00
Menu reprice with cap Section 5 $4.20 cost, $12.00 price, 0.30 target gives $13.00
and 32.3%
Usage variance Section 6 Chicken gives 13,000 g unexplained and $28.60 a
week
Order quantity Section 7 Period demand 16,422 g, need 17,243 g, order 4
cases
Labor requirement Section 8.3 24 covers at 16 per hour gives 3; 31 covers gives 4
Intraday multiplier Section 8.6 Multiplier 1.1875; 140 remaining becomes 166.25
Channel reprice Section 9 $13.89, suggested $14.00, contribution $5.58

<!-- page 45 -->

No test or code path calls a connector write endpoint.
Forecast backtests. Keep a backtest command that replays history week by week and
reports WAPE, pinball loss, and range coverage per stage. A model change must not ship
unless the backtest on the fixture data set is equal or better. Include fixture data for three
synthetic restaurants: steady, strongly seasonal, and sparse.
UI tests.
End-to-end flows (Playwright).
1. An invoice with a higher chicken price arrives; the burger's cost updates; a finding
appears; the reprice proposal is approved.
2. A manager generates a schedule, edits one shift, locks it, regenerates, and publishes.
3. During service, sales run below forecast; a cut suggestion appears; the manager
accepts it.
4. A purchase order draft is opened, one line is corrected with "We have plenty", and the
draft is approved.
Performance tests. Assert the targets in section 3 against the synthetic data set in
continuous integration.
Rule Test
Header sentence Every module page renders one, at most 20 words,
containing a digit
Tile and chart limits At most 3 tiles and exactly 1 chart above the table
Banned words A lint step fails the build if any interface string
contains a banned word, an exclamation mark, or an
emoji
Why panel Every derived number on every page opens a panel
with a formula, inputs, and a timestamp
States Each page has a stored test for setup, learning, live,
loading, empty, stale, and error
Accessibility Automated WCAG 2.1 AA checks pass on every
page; every action works by keyboard
Phone layout Today and the digest render in one column at 375
px with no horizontal scroll

<!-- page 46 -->

Observability.
Edits and dismissals are the main quality signal. Review any proposal kind with an
approval rate under 50%.

## 13. Build phases and the opening prompt

Build in eight phases. Each phase merges behind its feature flag and ends with the
acceptance test listed.
Signal Detail
Per job Count, duration, failures, and queue wait, tagged
by job name
Per restaurant Data freshness per source, module state, forecast
WAPE, range coverage
Alerts A job failing 3 times; a source stale past its limit
during opening hours; range coverage outside 70%
to 90% for 2 weeks
Product metrics Proposals approved, edited, and dismissed per
kind; suggestions accepted and dismissed; edits to
draft order quantities
Phase Builds Acceptance test
0. Survey and
foundation
Repository survey report. Section
4 tables and migrations. CSV
import for orders, punches, and
payouts. Business date.
rollup_intervals . Synthetic data
generator. Shared UI components
from 10.12. Interface-text lint
Import a fixture CSV twice;
row counts match after the
second run. Business-date test
passes. Performance targets
for reads pass on synthetic
data
1. Module A Schema changes, recost , alert
rule, menu_reprice , Menu costs
pages, digest card
End-to-end flow 1 passes. Plate
cost and reprice golden tests
pass. Recost completes within
60 s of a new price point

<!-- page 47 -->

Phases 2 and 3 depend only on phase 1 and can swap order. Phases 5 to 7 depend on phase
4.
Branching. Follow the rules in the v2 build spec: one short branch per phase, merged
behind its flag, with the full suite passing.
Files to create first.
Phase Builds Acceptance test
2. Module B usage_variance  job, findings,
Usage pages, coverage states
Usage golden test passes. A
fixture at 0.85 coverage shows
the learning state and opens
no findings
3. Module E Payout matching,
channel_margins ,
channel_reprice , Channels page
Channel golden test passes.
An unmatched order falls back
to channel_terms  and the
page shows the match rate
4. Forecast core
and Module C
Forecaster  interface, stage 1 and
stage 2 models, backtest
command, stock_estimates ,
order_draft , prep_sheet ,
Ordering pages
Order golden test passes.
Backtest report runs on all
three fixtures. End-to-end flow
4 passes
5. Labor forecast
and standards
Interval split, range calibration,
labor_standards ,
labor_requirements , Standards
page, Week page without the grid
Requirement golden test
passes. Changing a standard
updates requirements and the
preview sentence
6. Schedule
generation
Solver stages 1 and 2, validator,
schedule_draft , schedule grid,
publish and export
End-to-end flow 2 passes. The
validator finds no hard-
constraint violation across 50
random fixtures. Generation
finishes within 30 s for 40
employees
7. Intraday intraday_reforecast , suggestion
rules, Today page, push notification
to the manager
End-to-end flow 3 passes.
Intraday golden test passes.
No suggestion is produced
while elapsed_share  is under
0.15

<!-- page 48 -->

docs/SPEC-margin-labor.md : this document, exported as Markdown.
CLAUDE.md : add section 2 (ground rules), the "Not in scope" list from section 1, and
section 10.5 (writing rules).
docs/DECISIONS.md : one line per decision made where this document was silent.
Opening prompt for Claude Code. Paste this after adding the files above.
Read docs/SPEC-margin-labor.md in full, then CLAUDE.md.
Do Phase 0, step 1 only: survey this repository and report. Do not change any 
file yet.
In the report, list:
1. The language, framework, ORM, migration tool, job mechanism, test tools, 
and front-end stack actually in use.
2. For every table named in the spec: whether it exists, its actual name, and 
any column differences.
3. How tenant isolation, the proposals flow, the audit log, the event log, 
and feature flags work today, with file paths.
4. Every place the spec conflicts with the repository, and which one you will 
follow under the rule in section 1.
5. Every "Confirm with Eddie" item in section 14 that blocks Phase 0 or Phase 
1.
6. A migration plan for the section 4 tables, in order.
Rules for all later work:
- Build one phase at a time. Stop at the end of each phase and show the 
acceptance test results.
- Write the golden unit tests from section 12 before the code they test.
- Use Decimal for money and quantities. Never use float for them.
- Never weaken a rule in section 2. Stop and ask.
- Follow section 10 for every screen: one header sentence with a number, at 
most 3 tiles, 1 chart, the Why panel on every derived number, and the writing 
rules in 10.5.
- Record every decision the spec does not cover in docs/DECISIONS.md.
Prompt for each later phase.
Phase {n} of docs/SPEC-margin-labor.md. Restate the scope and the acceptance 
test in 5 lines or fewer, list the files you will add or change, then build 
it behind its feature flag. Write the tests first. Finish by running the full 
suite and showing the acceptance test results.

<!-- page 49 -->


## 14. Assumptions and open questions

Seven decisions need an answer from Eddie; each blocks the phase named. The
assumptions below hold until the Phase 0 survey says otherwise.
Confirm with Eddie.
Assumptions.
The backend is Python with FastAPI, SQLAlchemy, and Alembic, on SQLite in
development and Postgres in production. The v2 spec described TypeScript; the
repository decides.
The v2 tables recipes , recipe_lines , plate_costs , sales_items , forecasts ,
forecast_errors , purchase_orders , po_lines , prep_sheets , proposals ,
findings , events , and restaurant_flags  exist or are planned under those names.
price_points  holds a normalized unit price per canonical item in a base unit.
One restaurant_id  is one location.
Question Default if unanswered Blocks
Does each module get its own detail
page, changing the "digest is the
product" rule?
Yes, as written in section 2 Phase
0
Is labor scheduling now in scope,
reversing the earlier exclusion?
Yes, limited to forecasting, draft
schedules, and suggestions
Phase
5
Which POS is first for order-level data? CSV import only, until one is chosen Phase
0
Where do time punches come from:
the POS, a scheduler, or payroll?
CSV import only Phase
5
Which weather source? It must
provide both history and an hourly
forecast
None; stage 2 runs without weather
drivers
Phase
4
Which delivery platforms and payout
report formats come first?
channel_terms  only, no payout
matching
Phase
3
Who confirms labor_rules  values for
each restaurant's location?
The owner, in onboarding. All rules stay
unset until then
Phase
6

<!-- page 50 -->

Thresholds and weights in this document are starting defaults. All are settings, and
none is an industry benchmark.
Known limits.
Usage variance and stock estimates are estimates. Without counts they drift, and
they cannot separate waste from theft.
Forecasts for a single location rest on little data. Ranges will be wide for the first
months, and the UI must say so.
Schedule compliance depends on rules the owner enters. FullHouse checks drafts
against those rules and nothing else.
Modifiers, labor per dish, and multi-location groups are not covered.
Deferred, not designed here. Optional spot counts, a waste log, service-quality signals
for staffing (ticket times, table turns), staff-facing schedule delivery, and POS or platform
price writes.
