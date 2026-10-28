# Changelog

All notable changes to FullHouse. The full story, phase by phase, is in [docs/devlog.md](docs/devlog.md).

## [2.0.0] — 2026-10-26

FullHouse gains price intelligence: an **Invoices & prices** section in the sidebar beside the operations dashboards from 1.x.

### Prices, the main job
- **Invoices in five ways:** a photo or PDF read by Claude into lines with a confidence on each (or typed up where
  there's no key), bills pulled from Bill.com with their line items (v3 client, once per bill; a CSV export still
  works), forwarded or pasted invoice text, or lines typed by hand. Each line is kept as printed. Nothing is saved
  until you check it.
- **Every read is checked.** Lines have to add to the printed total and quantity × price has to be the line total,
  within two cents; anything read with doubt is flagged. An invoice that fails stays out of prices until a manager
  says "Looks right". Five sample invoices from two suppliers live in `backend/fixtures/invoices/`.
- **A changed pack is asked about, not assumed.** The same printed line in a different pack size raises one question
  before any per-unit price moves.
- **The digest as mail.** Monday 6am on the restaurant's clock (or an outside scheduler with `CRON_SECRET`), HTML and
  plain text, the same three sections, every card with a signed "Not useful" link that hides the item for four weeks.
  Delivered with Resend or printed to the log; "Email me this digest" sends it now.
- **Runs with no key.** The provider follows whichever key is set (`ANTHROPIC_API_KEY`, else `LLM_API_KEY` for
  Gemini, Groq or any OpenAI-compatible API); with none, the rule-based assistant answers price questions from the
  invoices. A photo of an invoice is read by Claude or by a vision model on the OpenAI-compatible API; a PDF that
  carries text is read with any model or none. Hosting costs nothing and asks for no card:
  `render.customer.yaml` deploys a site for any number of restaurants on Render's free plan with Neon Postgres, the
  Monday digest goes out from a Gmail address over SMTP (`SMTP_*`; Resend still works), and
  `.github/workflows/digest.yml` mails it from GitHub Actions, since a free service is asleep at six.
- **The history keeps the price work.** Invoices, items, matches, the agent's questions and drafts are in History,
  the agent's questions as the agent's and the answers as the person's. The review queue answers from the keyboard:
  Enter confirms, 1–3 pick, Esc skips.
- **Matching with memory.** A confirmed match binds a supplier's printed description to a canonical item and pack size
  for good. The engine never guesses: an unplaceable line becomes a one-tap question, five a week at most, biggest
  dollars first.
- **The weekly digest:** dollars at stake, then Switch supplier, Price went up, Owed to you, each card with one action.
  Beer, wine and liquor only trend. Thin evidence is withheld, not hedged.
- **The conversation:** ask about prices, invoices or suppliers from the field pinned to every screen; the agent answers in
  two sentences with source chips, or asks a question back. Meeting talking points come from the digest without a
  model call.
- **Drafts to reps:** price challenges and credit claims, three sentences and the lines cited, sent only from your own
  mail app after a confirm sheet. The rep's name and email are remembered per supplier.
- **Bookkeepers:** a new role that belongs to no restaurant, invited by an owner, with a clients table and the right
  to answer questions for each client.
- The scripted provider answers price questions deterministically, so the demo, CI and the browser tests exercise the
  real tool path.

### Connections and prime cost (v2, Phase A; `v2_connect`)
- Square and Gusto by OAuth, QuickBooks for pushing bills, Bill.com behind the same connector interface, CSV
  exports from Toast, Square and Clover and payroll read by their headers with a remembered mapping, and a
  per-restaurant inbound address for invoices with a sender allow-list.
- A weekly prime-cost P&L from the week's invoices, sales and labor, and a prime-cost line at the top of the
  digest, or the connect prompt when a day is missing.
- Under the hood (Phase 0): a job queue with worker threads or a separate worker process, an event log, a
  webhook inbox, per-restaurant feature flags, encrypted connector credentials, file storage with signed
  links, structured logs, an ops endpoint, and an agent write guard enforced at flush time.

### Documents, agreed prices, recipes, menu margin (v2, Phase B; `v2_documents`)
- A document registry: anything uploaded or mailed in is classified (model or rules) and read with the schema for
  its kind; the reviewer picks the type when the reader isn't sure.
- Agreed prices from price lists or the item page, as proposals; invoices billed above an agreed price open an
  "Owed to you" card citing the line and the price list.
- Recipes proposed for top sellers from the POS name and what the restaurant buys, weekly plate cost from price
  points with sub-recipes to depth four, a Menu margin section in the digest, and reprice suggestions that never
  write to a menu. New agent tools: list_recipes, propose_recipe, propose_agreed_price, propose_menu_reprice,
  explain_finding. Weekly caps per proposal kind.

### Order drafts, forecasting, prep sheets, schedules (v2, Phase C; `v2_orders`)
- Approving a reorder now drafts the order; a person sends it to the rep (mail with the lines and a PDF) or marks it
  placed by phone. Orders with lines, drafted per supplier from eight weeks of invoices by a Sunday workflow or the
  agent's `propose_purchase_order`; an invoice receives order lines one by one, and a short delivery against an order
  shows under Owed to you.
- A nightly baseline forecast per dish with a low and high, errors recorded as actuals arrive, and the digest's
  "Forecast accuracy last week" and "Recovered to date" lines. Prep sheets from the forecast and the recipes, as
  proposals, printable once confirmed.
- Per-restaurant schedules (`workflows`) run on the restaurant's clock from one minute tick, each queued once per
  period; owners switch them on or off and run them now under Profile › Features.

### Calendar and equipment (v2, Phase D; `v2_calendar`)
- Licenses, permits, inspection reports and warranties read from documents become calendar proposals; warranties add
  the equipment. A daily scan marks overdue items and proposes service dates; "Coming due" in the digest lists what is
  within thirty days; proof attached clears an item and makes the next recurrence. Referrals are recorded.

### Inventory meets the invoices
- An item on the invoices is counted as the Inventory item its name matches, or the one the owner picks on its page.
  A confirmed invoice's lines are booked into stock as deliveries, once, in the agent's name. An item at its reorder
  point gets a purchase order proposed under Recommendations from the cheapest recent supplier, in whole packs, with
  the cost; a delivery that lifts the stock withdraws it. The Inventory page shows the last price paid per item with a
  link to its prices. Nothing is sent to a supplier.
- **Purchase orders, placed to received.** Approving a reorder now places an order instead of booking the goods at
  once: it sits under **On order** on Inventory with the supplier and expected date (the supplier's lead time), can be
  marked shipped, shows as overdue when late, and is booked into stock when someone receives it with what actually
  arrived. An invoice for an open order receives it; a hand receipt is matched to its invoice when that comes, so
  nothing counts twice. Orders placed by phone are recorded the same way. Today counts what is on its way.

### Navigation
- The sidebar is one section per part of the business: Today and Recommendations, then Inventory (with Suppliers),
  Staffing, Customers, **Invoices & prices** in the order an invoice moves through it (Invoices, Questions, Items,
  Digest, Conversation), Menu, Agent, Records and Restaurant.
  Today is still the home page; `#archive` from an earlier build lands there.

## [1.0.0] — 2026-09-13

The first complete release: a team of agents that proposes, a manager who decides, and a record of everything.

### Agents
- **Maestro and five specialists** — inventory, supply chain, staff, menu and profit — each with tools scoped to its
  own domain. Maestro delegates, and specialists asked for in the same turn **run in parallel**, each in its own
  database session.
- **Three interchangeable model backends:** Anthropic Claude, a local model through Ollama, and a deterministic
  scripted provider for tests, CI and the public demo.
- **Background runs with live streaming.** Runs execute on a thread pool and stream every tool call and result to the
  browser over server-sent events, replaying history for late listeners. Runs can be cancelled, and cancelling Maestro
  stops its specialists.
- **Cron schedules** per agent, skipping a run while the previous one is still going.
- **Faster local runs:** requests pin Ollama's context size and keep-alive, which stopped the model being reloaded
  between calls — a Maestro run that timed out after 300 s now completes in about a minute.
- **Agent evaluation:** six scenarios scored on what an agent did, against a do-nothing baseline.

### Approvals and history
- **Approving a proposal performs it** — restock, purchase order, new shift, price change or promotion — in the same
  transaction as the approval and its audit record.
- **Proposals missing information ask for it** before they can be approved, such as a promotion with no price.
- **Every change is recorded** by an ORM listener with before and after values, the person or agent responsible, and
  the run behind it — whichever code path made the change.
- **Any change can be reverted.** The undo is recorded too, and a revert that would overwrite newer edits is refused.

### Restaurant operations
- Pages for inventory, suppliers, menu, staff, and profit.
- **Recipes** link dishes to ingredients: selling a dish draws down stock, food cost is computed, and "days of cover"
  compares real consumption against supplier lead times.
- **Profit and a seven-day forecast** from actual sales, using weekday means once there are two weeks of history.
- **Promotions** that end on their own and restore the regular price.

### Platform
- FastAPI, SQLAlchemy 2.0 and Alembic migrations; **SQLite and Postgres** both supported and tested.
- React 19 and TypeScript frontend with one design system and hand-built SVG charts.
- **Public demo mode:** a demo bistro with six weeks of trading, reloaded at startup and daily.

### Quality and delivery
- 210+ backend tests run on SQLite and Postgres, Playwright end-to-end tests, and GitHub Actions CI that also builds and
  smoke-tests both Docker setups.
- **Single-container image** for free hosts, with a Render blueprint; Docker Compose with nginx and Ollama for running
  with a local model.
- `dev.py` runs every developer task the same way on Windows, macOS and Linux.

### Fixed
- Timestamps from SQLite were sent without a UTC offset, so browsers west of UTC showed recent runs as hours in the
  future.
- The profit chart's daily series summed to less than the headline revenue, because days were bucketed in local time.
- SQLite ignored foreign keys, so deleting a dish could orphan the sales that recorded it.

[1.0.0]: https://github.com/eddierzhang/FullHouse-Updated/releases/tag/v1.0.0
