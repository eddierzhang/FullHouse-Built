# Pilot with the first restaurant

A checklist for running FullHouse with one restaurant before adding more. The aim is to watch real people do real
work, find what gets in their way, and fix it while it's still one restaurant.

## Before the first day

- [ ] Deploy their instance ([DEPLOY.md](../DEPLOY.md), "A restaurant's own instance"), with `ALLOW_SIGNUP=false`,
      `REQUIRE_MFA=true` and a generated `SECRET_KEY`.
- [ ] Turn on the nightly backup and run it once by hand; check a restore works (DEPLOY.md, Backups).
- [ ] Set `SENTRY_DSN` and `SENTRY_ENVIRONMENT=pilot`; set `SUPPORT_EMAIL` to an address someone reads daily.
- [ ] Turn on Render's failure emails and an uptime check on `/health` (DEPLOY.md, Error alerts).
- [ ] Write down the current alembic revision: the one you'd roll back to (DEPLOY.md, Rolling back).
- [ ] Sit with the owner for setup: details, hours, roles and rules; invite their team; import their stock list.
- [ ] Check with them that the imported stock matches their last count.

## Watch them work (first week)

Sit with the owner and one or two staff for a real shift each. Don't help unless they're stuck; note where they
hesitate. Ask them to do, on their own devices:

- [ ] Log a delivery as it comes in (including one bought by the case).
- [ ] Record waste at the end of a shift.
- [ ] Do a stock count of a few items.
- [ ] Find their shifts for next week (staff); publish next week's schedule (owner).
- [ ] Ask the Assistant Manager for a stock check, and approve or reject what it suggests.
- [ ] Undo something from History & undo.
- [ ] Use it on a phone in the kitchen, including somewhere with poor signal.

For each task note: did they finish it, how long it took, where they hesitated, and anything they said.

## Collecting feedback

- **Feedback button**: read it daily (Help → Feedback from your team, or the `fullhouse.feedback` log lines).
- **Sentry**: every new error emails you. Reply to each within a day, even just to say it's being looked at.
- **A short check-in** at the end of weeks 1 and 2: What did you stop doing on paper? What still feels slow? What
  would make you stop using it?

## What "ready for more restaurants" looks like

- [ ] Staff log deliveries and waste in FullHouse without being reminded, for at least a week.
- [ ] The stock figures match a physical count, within what they'd accept.
- [ ] No lost or doubled stock changes reported.
- [ ] No unexplained errors in Sentry for a week.
- [ ] The owner has approved at least a few recommendations and would miss them.
- [ ] Every problem found is fixed, or written down with a reason it can wait.

## Fixing things during the pilot

- Small fixes: deploy after hours, and tell the owner what changed.
- Anything touching the database: rehearse the migration on a restored backup first (DEPLOY.md, Rolling back).
- If a deploy goes wrong: roll back first, investigate second.
