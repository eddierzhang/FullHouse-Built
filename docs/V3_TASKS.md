# Full House v3 build: task list

The design is "FullHouse v3 design: demo readiness, inventory autopilot and voice" (Nov 17 2026). Each phase is behind
a per-restaurant flag (Profile, Features) and is done when its acceptance tests pass. Work is on `v3/build`.

## Phase 1: demo readiness (no flag)

- [x] One sample restaurant, Botanica: orders, POS sales, labor, invoices and stock scale from `VOLUME`, `STOCK_SCALE`,
      `INVOICE_SCALE` and `LABOR_SHARE` in `app/demo.py`; each day's POS sales are that day's orders.
- [x] Nothing a flag switches on opens empty: confirmed recipes, documents, calendar dates, a prep sheet, sample connections.
- [x] The landing page previews the demo's own top digest card (`demo_card` on `/api/v1/auth/options`).
- [x] `test_every_page_of_the_demo_tells_the_same_story`.
- [ ] A private copy of the demo per visitor; a model behind the demo; an always-on host. Need a key and a host.

## Groundwork for phases 3 to 6

- [x] Tables and migration `d1f4a8c2e6b3`; flags `v3_nav`, `v3_stock`, `v3_autopilot`, `v3_receiving`, `v3_voice`.
- [x] `app/stock/ledger.py`: movements named by `source_key` never apply twice. `app/notify/service.py`: notices and meters.

## Phase 2: navigation, charts and tools (flag `v3_nav`)

- [x] Search from anywhere (Ctrl or Cmd K, "/", the search button): pages, stock, price items, suppliers, invoices,
      dishes; anything else is a question for the assistant. `GET /api/v1/insights/search`.
- [x] Savings & costs page: what the digests found and what was acted on, prime cost by week, spend by supplier and
      category against the period before. Every chart has a table. `app/insights/engine.py`.
- [x] What-if tools page: when an item runs out and what par to hold, what a supplier switch saves in a year, what a
      price or cost change does to a dish. They read; nothing is written.
- [x] An Insights group in the sidebar, groups that fold (all open by default), a savings tally on Today, a five-card
      tour on first sign-in that Help can replay.
- [x] Acceptance: `tests/test_insights.py` (7 tests); the existing browser suite passes with the flag off.
- [ ] Not built: "try your own invoice" from the landing page, breadcrumbs, the autopilot preview (phase 4), the
      count sheet (phase 3), text and push alerts, offline use. No browser test covers the new pages yet.

Notes. The design's five-item menu with the rest under "More" was not built: the operations pages stay in the main
navigation, so the new pages are one more group and folding is each person's choice. The browser tests need
`E2E_PYTHON=backend/.venv/bin/python` on a machine with no `python` on the PATH. A stock hit in search opens the
Inventory page, not the item's row.

## Phase 3: stock that tracks itself (flag `v3_stock`)

- [x] Sales deplete stock: each POS sale line, through its dish's recipe (`recipe_items`), with the item's yield
      applied. `app/stock/engine.py::deplete`; a line already taken off is skipped, a revised day moves only the
      difference. Runs every 30 minutes (workflow `stock`) and after each `sales_items.synced` event.
- [x] Tracking starts the day it is first run (`inventory_settings.deplete_from`); earlier sales are in the opening figures.
- [x] Count sheets by storage area in shelf order: open, save as you go, post. Kept on the phone and re-sent after
      poor signal; `POST /api/v1/stock/counts/now` posts a whole count once per `client_key`.
- [x] Waste in three taps with a reason; never takes more than is on the shelf; once per Idempotency-Key.
- [x] `stock_daily` rollup and the variance report read from it: received, used by sales, wasted, and what counts
      found, in units and dollars. An item nobody counted has no shrink figure.
- [x] Alerts: runs out before a delivery could arrive, and "86 this dish"; written as notifications once a day.
- [x] Pages: Count sheet (with Arrange shelves) and Variance & waste, in the Inventory group.
- [x] Demo: two weeks of ledger history that ends at today's figures, a walk-in count a week ago, five waste entries.
- [x] Acceptance and invariants in `tests/test_stock.py` (11 tests): seven days of sales to within 0.1%, replay
      changes nothing (I12), the ledger is the truth under random movements (I11), another restaurant's items
      cannot be counted.
- [ ] Not built: per-item unit conversion table, POS modifiers, prep recipes, delivery lots and use-by dates,
      text or push delivery of alerts (they are written to `notifications`, which no page shows yet), a browser
      test for the new pages.

Notes. Depletion uses the operations recipes (stock units), not the v2 costing recipes (invoice units); a dish
needs a recipe on the Menu page to be tracked. Orders taken in the app still deplete at once, as before; POS
sales lines deplete through the job. A restaurant feeding the same sales through both would count them twice.
The demo sets `deplete_from` to the day it loads, so its seeded history is never taken off again.

## Phase 4: ordering (flag `v3_autopilot`)

- [x] Pars: a day's use × (lead time + days between order days + safety days); a pinned par is never overwritten.
      `app/ordering/pars.py`.
- [x] Supplier calendars: order days, cutoff, delivery days, minimum, channel, order address.
- [x] Policy as pure functions (`app/ordering/policy.py`): ten rules, all always evaluated, each said in words.
- [x] Autopilot (`app/ordering/autopilot.py`): a draft on the order day within three hours of the cutoff, brought
      to par where stock is tracked; decide; an outbox row written with the approval; send after the cancel
      window; cancel; three failed or cancelled sends in a row switch it off. Job every five minutes.
- [x] A held order stays an ordinary proposal with the failed rules on it. Only an owner sets limits; any manager
      can pause all or stop a send.
- [x] Preview: the supplier's last eight weeks of invoices replayed against the limits.
- [x] Acceptance and I13/I14 in `tests/test_ordering.py` (22 tests): each rule blocks its draft, a fuzz over 3,000
      drafts, sends once after the window, cancel sends nothing, nothing goes for a supplier that is off.
- [ ] Not built: top-up suggestions under a minimum (the order is held to the next order day instead), text or
      push for the "about to send" notice, portal or EDI ordering.

Notes. Autopilot needs a mailbox on the site; without one every order is held (the public demo treats the
server log as delivery). Orders autopilot may send are not counted against the weekly cap on order proposals.
The landing page, README and user guide now say an order can go out by itself when an owner switches it on.

## Phase 5: receiving (flag `v3_receiving`)

- [x] Deliveries page: what is due, one tap when everything arrived, per-line amounts and reasons otherwise, a
      photo of the slip kept as a document. Stock is booked through the ledger once per receipt line.
- [x] Part received: a line marked "rest still coming" stays open; the order closes when every line is settled.
      The state is `partial` (the status column is 12 characters; the design says `part_received`).
- [x] Anything short, damaged or missing drafts a message to the rep (`short_delivery`) and a notice.
- [x] The invoice for a delivery already checked in is matched to it and books nothing more.
- [x] Supplier replies: each order mail carries its own reply address (`po-<token>@INBOUND_DOMAIN`); a reply is
      read by rule as confirmed, shipped, or a change for a person. A manager can also paste a reply in.
- [x] Late orders are said once a morning, with a chase message to send from your own mail.
- [x] `tests/test_receiving.py` (13 tests).
- [ ] Not built: reading the slip photo to pre-fill the lines, price-on-invoice against price-on-order as its own
      check, cheapest alternative supplier for a late item, offline queueing of a check-in (it retries, and is
      recorded once, but needs signal).

## Phase 6: voice (flag `v3_voice`)

- [x] `app/voice/parser.py`: count, waste, delivery, question, page. Numbers said many ways, units and pack words,
      items by name or a learned spoken alias, entry boundaries without commas. No model.
- [x] `POST /voice/parse` writes only a preview; `POST /voice/commands/{id}/confirm` is the only write (I15), once.
- [x] Microphone button on every page: the browser's own recognition, or typing; the preview is read back;
      amounts can be corrected and an unclear item picked (and remembered) before Confirm.
- [x] Hands-free count on the count sheet: each item read out; a number, "same", "skip", "back" or "stop".
- [x] `tests/test_voice.py` (18 tests): 260 spoken lines, all parsed; no write before confirm; the budget blocks
      transcription (I16).
- [ ] Not built: server-side speech to text (`/voice/transcribe` checks the budget, then says no service is
      connected), a phone line, Spanish, a model fallback for sentences the rules can't read, a spoken digest.

Notes. Voice has only been driven by typed text in a headless browser here; the microphone path uses the
browser's speech recognition and has not been tried on a real device in a real kitchen. A spoken delivery is
carried out only as "everything arrived" or "everything arrived except…"; anything else goes to the Deliveries page.

## Workstream G (scale): not built

Stateless web processes, split queues, row-level security, month partitions, per-restaurant model budgets
enforced on agent runs, live Square/Toast/Clover/Gusto/QuickBooks/Bill.com against their sandboxes, and text and
push delivery. `notifications` and `usage_meters` exist and are written; no page lists notifications yet.
