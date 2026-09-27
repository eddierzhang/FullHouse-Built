# Changelog

All notable changes to FullHouse. The full story, phase by phase, is in [docs/devlog.md](docs/devlog.md).

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
