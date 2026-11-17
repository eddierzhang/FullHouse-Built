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

## Phases 3 to 6

- [ ] 3 Stock ledger (`v3_stock`): sales deplete stock through recipes, count sheets, waste, variance.
- [ ] 4 Ordering (`v3_autopilot`): pars, supplier calendars, the policy engine and outbox.
- [ ] 5 Receiving (`v3_receiving`): supplier replies, phone receiving, three-way match. Order state is `partial`, not `part_received`.
- [ ] 6 Voice (`v3_voice`): push-to-talk, the rule parser, confirm before write.
