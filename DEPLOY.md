# Running and deploying FullHouse

## Public demo (free)

The single-container image in the repo root is built for this: the frontend and API in one process, agents on the
scripted provider (no model, no API key), and a demo restaurant reloaded at every start and daily at 04:00 UTC. It
used about 100 MB of memory when measured locally, so a 512 MB free tier is plenty.

### Render — one click

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/eddierzhang/FullHouse-Built)

The button opens Render with this repository's [`render.yaml`](render.yaml) already read: sign in with GitHub, confirm,
and the free `fullhouse-demo` service is created. Nothing to configure and no card required.

By hand instead:

1. Sign in at [render.com](https://render.com) with GitHub and give it access to this repository.
2. **New → Blueprint**, pick the repository. Render reads [`render.yaml`](render.yaml) and proposes one free web
   service, `fullhouse-demo`. Apply.
3. The first build takes a few minutes. The site is then at `https://fullhouse-demo.onrender.com` (or a suffixed
   variant if the name is taken). Put that link at the top of the README.

Pushes to `main` redeploy automatically. The free plan sleeps after 15 idle minutes and takes about a minute to
wake; each wake starts from a fresh demo, which is what a public demo wants anyway.

### Anywhere else that runs a container

```bash
docker build -t fullhouse .
docker run -p 8080:8000 fullhouse                  # the host may assign the port via $PORT instead
```

Any host that runs one container runs this image unchanged — Fly.io, Google Cloud Run, Northflank, an EC2 box.
Set `DEMO_MODE=false` to run it as a real instance instead of a demo; no key is needed.

Be aware which of those are still *free*, as of September 2026: Fly.io and Koyeb have both retired their free
tiers, and Cloud Run's always-free grant (180,000 vCPU-seconds a month, about 50 CPU-hours) does not cover an
always-on process — and scaling it to zero throttles the CPU between requests, which stops the cron scheduler and
freezes any agent run still in flight. Render and Northflank are the free container hosts left.

**On an open demo, anyone can change anything** — that's the point, and the scheduled reset is what makes it safe.
Don't point a demo at data you care about.

---

## Hosting it for real, completely free

One FullHouse site for real restaurants, costing nothing and asking for no card anywhere. No key, no model and no
domain are needed: without any key the assistant is rule-based (it answers price questions from the invoices),
PDFs are read by rule, and the digest, the questions and the Monday mail all work. Everything below is free as
of October 2026.

| Piece | Free service | What the free plan gives | Card needed |
|---|---|---|---|
| The app | [Render](https://render.com) free web service | 512 MB; sleeps after 15 idle minutes, wakes in about a minute; 750 running hours a month per workspace | no |
| The database | [Neon](https://neon.com) Postgres | 0.5 GB per project, kept for good | no |
| The Monday mail | a Gmail address over SMTP | 500 mails a day from your own mailbox; no domain to buy | no |
| Mailing it while the site sleeps | GitHub Actions | the Monday schedule in `.github/workflows/digest.yml` | no |
| Keeping it awake (optional) | [UptimeRobot](https://uptimerobot.com) | a ping every 5 minutes; see [sleep](#what-free-costs-you) | no |
| The model (optional) | [Gemini API](https://aistudio.google.com/apikey) | a few requests a minute and a daily cap, per Google account ([rate limits](https://aistudio.google.com/rate-limit)) | no |
| Error alerts (optional) | [Sentry](https://sentry.io) | plenty for a few restaurants | no |

**One site, any number of restaurants.** A site holds one restaurant by default. With `ALLOW_SIGNUP=true` any
number can sign up on the same site, each with its own owner, team, invoices and digest, kept apart by the server
on every request (`tests/test_restaurant_separation.py` has a second restaurant try every route against the
first's). That is the free way to host several: one Render service, one Neon database, the 750 hours spent once.
A site per restaurant (the steps below, repeated) keeps their data in separate databases and lets each bring its
own model key, at the cost of sharing the workspace's hours between them.

### Setting it up (about 10 minutes)

1. **Database.** Sign in at [neon.com](https://neon.com), **New project**. Copy the connection string
   (`postgresql://...`). The app migrates it on first start.
2. **A mailbox for the Monday digest, if you want it mailed.** In a Google account with 2-Step Verification on,
   go to Security → 2-Step Verification → **App passwords** and make one for "FullHouse". Keep the 16-character
   password. (Any other mailbox works too: its SMTP host and port go under Environment in step 3.) Skip this and
   the digest is printed to the service log and read in the app.
3. **App.** At [render.com](https://render.com): **New → Blueprint**, pick this repository, and set **Blueprint
   Path** to `render.customer.yaml`. Rename the service (e.g. `fullhouse-joes`). Paste the Neon string into
   `DATABASE_URL`, the Gmail address into `SMTP_USER` and the app password into `SMTP_PASSWORD`. Leave
   `LLM_API_KEY` and `LLM_MODEL` blank for the rule-based assistant, or see [a model](#adding-a-model-still-free)
   below. Apply. Render generates `SECRET_KEY` (encrypts two-step sign-in secrets; never change it) and
   `CRON_SECRET`. The first build takes a few minutes; the service log then says what it runs on
   (`assistant: ...`, `invoice reader: ...`, `digest mail: ...`).
4. **Hand-over.** Open the service URL and have the restaurant owner **sign up straight away**. The
   first person to sign up becomes the owner; after that, nobody can sign up without an invite. For several
   restaurants on this site, add `ALLOW_SIGNUP=true` under **Environment** instead, and each owner signs up from
   the landing page. The owner sets up two-step sign-in (an authenticator app on their phone), goes through
   setup, then invites the team from **Profile → Team access**.
5. **Mondays.** A free service is asleep at six on Monday, so its own schedule may never fire. In this repository,
   add two **Actions secrets** (Settings → Secrets and variables → Actions): `DIGEST_URL` (the service's
   address; several, separated by spaces, for several sites) and `CRON_SECRET` (copied from the service's
   Environment tab). Every Monday [`.github/workflows/digest.yml`](.github/workflows/digest.yml) wakes each site
   and asks it to mail every restaurant's digest, once a week however often it is asked. Run it once by hand
   (Actions → Monday digest mail → Run workflow) to check; the service log shows each mail.
6. **Bill.com, if the restaurant uses it.** Set `BILLCOM_USERNAME`, `BILLCOM_PASSWORD`, `BILLCOM_ORG_ID`,
   `BILLCOM_DEV_KEY` (and `BILLCOM_ENVIRONMENT=production`) under **Environment**; "Pull from Bill.com" appears on
   the Invoices page. See the README's limitations: the client has run against a stub only.
7. **Backups.** See [Backups](#backups) below: two repository secrets turn on a nightly, restore-tested backup,
   also free.

Every push to `main` redeploys the site, and migrations run automatically.

### Adding a model, still free

A model adds free-form answers in the conversation and the reading of invoice photos. At
[aistudio.google.com/apikey](https://aistudio.google.com/apikey), **Create API key**, and set `LLM_API_KEY` to it
and `LLM_MODEL` to a model that reads images (`gemini-2.5-flash`; anything from Gemini's
[model list](https://ai.google.dev/gemini-api/docs/models)). The provider follows the key: an `LLM_API_KEY` means
the OpenAI-compatible provider, an `ANTHROPIC_API_KEY` means Claude (paid). For another OpenAI-compatible API, add
`LLM_BASE_URL`: Groq (`https://api.groq.com/openai/v1`, also free), OpenRouter, or a paid one. Photos are read
only by a model that takes images; PDFs read on any model. `INVOICE_READER_MODEL` pins a different model for
reading than for the assistant.

### What "free" costs you

- **Sleep.** After 15 idle minutes the first visit takes about a minute to wake the service. A run in progress
  when it sleeps is marked failed, and the archived agents' schedules only fire while it's awake; the Monday mail
  doesn't depend on it (step 5). To keep it awake, point [UptimeRobot](https://uptimerobot.com)'s free plan at
  `/health` every 5 minutes: that uses about 720 of the workspace's 750 hours, so it works for **one** site per
  Render workspace, which with `ALLOW_SIGNUP=true` is still any number of restaurants.
- **Rate limits.** Free Gemini keys allow only a few requests a minute. The app waits out a 429 up to four
  times before failing the run with a message saying so. Restaurants sharing a site share its key, so for a busy
  site give each restaurant its own site, with its own key from its own Google account.
- **Privacy.** On Gemini's free tier, Google may use prompts to improve its products; the paid tier doesn't.
  Agents send stock, menu, staff and sales data. Tell the restaurant, or use a paid key for them.
- **Mail from a personal mailbox.** The digest comes from the Gmail address you set up, so use one made for the
  purpose. Gmail's limit of 500 mails a day is far beyond a weekly digest to a handful of owners.

---


## Local, with Docker and a local model

Needs [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```bash
cp .env.docker.example .env      # adjust if you like
docker compose up -d --build
docker compose logs -f ollama-pull   # first run downloads the model (~5 GB for qwen2.5:7b)
```

Open http://localhost:8080. Four containers start:

| Service | Role |
|---|---|
| `frontend` | nginx serving the built React app, proxying `/api` to the backend |
| `backend` | FastAPI; migrates and seeds the database on every start (both are idempotent) |
| `ollama` | the local model server |
| `ollama-pull` | one-shot job that downloads `OLLAMA_MODEL`, then exits |

The SQLite database lives in the `app-data` volume and survives restarts and rebuilds.
`docker compose down -v` deletes it along with the downloaded models.

To reuse models already on this machine instead of downloading, set `OLLAMA_MODELS_DIR` in
`.env` (see the example file).

**One process is enough; two kinds are possible.** Every side effect (agent runs, Bill.com pulls,
digest rebuilds) is a job in the `jobs` table. By default the web process runs the worker threads
itself (`WORKER_INPROCESS=true`, `WORKER_THREADS=2`), which is right for one free container. To
scale the two apart, set `WORKER_INPROCESS=false` on the web service and run `python -m app.worker`
as a second service on the same database; any number of workers share the queue. Live run streams
come from the process that executes the run, so with a separate worker the web app shows runs when
they finish rather than as they go. `LOG_FORMAT=json` prints one JSON object per log line with the
restaurant, job and run on each.

---

## Free cloud deployment

The constraint that decides everything is the model. A 7B model needs several GB of RAM, and
no free *platform* tier comes close — Render's free web service is 512 MB. So there are three
routes, and only the first runs the agents on a model for free.

### Route 1 (recommended): Oracle Cloud Always Free VM

One free ARM VM runs the entire compose stack, model included.

- **Limits as of June 2026:** 2 OCPUs and 12 GB RAM for Ampere A1 instances. Oracle halved
  this from 4 OCPU / 24 GB and began terminating instances over the new limit on
  18 August 2026, so older tutorials are wrong.
- 12 GB fits `qwen2.5:7b`, but on 2 CPU cores with no GPU **expect a run to take minutes**.
  Use `OLLAMA_MODEL=qwen3.5:4b` there; it is noticeably faster.
- Signup asks for a card to verify identity; Always Free resources are not charged.

**Steps**

1. **Create the VM.** Compute → Instances → Create. Image: Ubuntu 24.04. Shape:
   `VM.Standard.A1.Flex`, 2 OCPU, 12 GB. If it says "out of host capacity", retry later or pick
   another availability domain — free ARM capacity is often exhausted.
2. **Open port 80 twice.** Once in the subnet's security list (Networking → VCN → Security
   List → ingress TCP 80 from 0.0.0.0/0), and once on the machine itself, because Oracle's
   Ubuntu images ship a restrictive firewall:
   ```bash
   sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
   sudo netfilter-persistent save
   ```
   Skipping the second step is the most common reason the site "doesn't load".
3. **Install Docker.**
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER && newgrp docker
   ```
4. **Get the code.**
   ```bash
   git clone https://github.com/eddierzhang/FullHouse-Built.git
   cd FullHouse-Built
   ```
5. **Configure and start.**
   ```bash
   cp .env.docker.example .env
   # edit .env: WEB_PORT=80, PUBLIC_URL=http://<vm-public-ip>, OLLAMA_MODEL=qwen3.5:4b
   docker compose up -d --build
   docker compose logs -f ollama-pull
   ```
6. Open `http://<vm-public-ip>`.

**Updating later:** `git pull && docker compose up -d --build`. Migrations run automatically.

### Before you share the link: lock it down

Every page and API route needs a signed-in account, and once the restaurant is set up, nobody can
create one without an invite link (see [Security](#security)). Set a long random `SECRET_KEY` in
`backend/.env` (or the host's environment) before anyone sets up two-step sign-in, and never change it.

Sign-in cookies are marked secure automatically when the site is served over HTTPS. Serve it
over HTTPS whenever it's reachable from the internet; on plain HTTP a password crosses the
network readable. For a second layer, or to hide the login page itself:

- **Cloudflare Tunnel + Cloudflare Access (free)** — the best fit. The tunnel gives you an
  HTTPS URL without opening port 80 at all, and Access puts an email login in front of it.
- **nginx basic auth** — one password for everyone, a few lines in `frontend/nginx.conf`.
- **Restrict port 80 to your own IP** in the Oracle security list, if only you need it.

### Route 2: free platforms, on a hosted model

No free platform can run Ollama, but agents can still run on a real model for free through a hosted API. The
full recipe is [Hosting it for real, completely free](#hosting-it-for-real-completely-free) above. The pieces:

- **The whole thing, one container:** Render's free web service — 512 MB, spins down after 15
  idle minutes. Spinning down kills any run in flight; the backend marks those runs failed on
  its next start. Northflank's free tier (two services) is the nearest alternative left.
- **Frontend alone:** Cloudflare Pages, Netlify or Vercel, all permanently free. Build with
  `VITE_API_BASE` set to the backend's URL, and set `FRONTEND_ORIGIN` on the backend to the
  frontend's URL for CORS.
- **Model:** there is no free host for Ollama at this size. No key is needed (the rule-based assistant runs);
  a free Gemini or Groq key (`LLM_API_KEY`) adds a model, and an Anthropic key (paid) Claude.
- **Database:** Render's free disk is wiped on every deploy, so SQLite won't persist, and
  Render's free Postgres is deleted after 30 days. Neon's free tier (0.5 GB) is permanent, and
  Postgres is supported: point `DATABASE_URL` at it (CI runs the whole suite on Postgres 16).

### Route 3: a free VM on one of the big clouds

Both of these run the compose stack the way the Oracle route does, and neither has the RAM for
a local model — run with no key, or add a free Gemini key (`LLM_API_KEY`).

**Google Compute Engine** keeps a genuinely permanent free e2-micro in `us-west1`, `us-central1`
or `us-east1`, with 30 GB of disk. Its 1 GB of RAM holds the single-container image comfortably
(about 100 MB measured) but not Ollama. The limit that bites first is egress: 1 GB a month out
of North America.

**AWS is no longer free in any lasting sense.** Accounts created after 15 July 2025 get $100 in
credits — up to $200 after the onboarding tasks — and six months, after which everything bills;
the old 12-month tier survives only on accounts older than that. To spend the credits here, a
`t4g.small` runs the compose stack for about $13 a month including its disk, so the balance
covers the whole window, and staying on the *Free* plan means AWS stops the resources instead
of charging you when the credits run out. Keep it to one instance whatever you use: App Runner
and ECS both default to settings that will run two copies of the backend, which splits agent
runs from their live streams and gives you two schedulers. A GPU instance for Ollama
(`g5.xlarge`, about $1/hour) would exhaust the credits in under a week.

---

## Security

What the app enforces, and what's left to you.

**Accounts and access**

- Everyone has their own account. After setup, new people join only through an invite link an owner or admin
  creates (Profile → Team access); nobody joins a restaurant by signing up. `ALLOW_SIGNUP=true` opens the site to *new restaurants*: anyone can
  create an account from the landing page, which makes them the owner of a new, separate restaurant.
- Roles, checked by the server on every request (agent tools included):

  | | Owner / admin | Member |
  |---|---|---|
  | Agent: run, approve, reject, edit | yes | view only |
  | Menu, recipes, suppliers | edit | view |
  | Profit figures and forecast | yes | no |
  | Staff profiles and the schedule | manage | own profile, published shifts |
  | Inventory | everything | add items, record stock changes |
  | Undo from the change history | yes | no |
  | Team, invites, security log | yes (admins manage members only) | no |

- Owners and admins must use **two-step sign-in** (`REQUIRE_MFA`, never in demo mode); members may turn it on.
- **Turning access off** (Team access) signs someone out everywhere at once. **Reset password** makes a one-time
  link, valid for an hour, for you to hand them; **Reset two-step** is for a lost phone.
- Ten failed sign-ins in 15 minutes block that address, and separately that account, for 15 minutes.
- **Profile → Security log** records sign-ins, failures, lockouts, sign-ins from new devices, refused actions,
  invites, resets and access changes.

**Data**

- Every record belongs to a restaurant, and the server only ever looks things up inside the signed-in person's
  own, so another restaurant's ids read as missing. `tests/test_restaurant_separation.py` has a second
  restaurant try every route against the first's ids.
- The change history records who (or which agent run) changed what, before and after, when, and for which restaurant.
- Passwords are hashed with scrypt; session, invite and reset tokens are stored as hashes; two-step secrets are
  encrypted with `SECRET_KEY`.
- Security headers on every response: a content security policy, no framing, no content sniffing, and HSTS over HTTPS.

**If an owner is locked out**

From a machine with the site's `DATABASE_URL` (and `SECRET_KEY`), in `backend/`:

```bash
python scripts/reset_link.py owner@example.com https://your-site.onrender.com   # prints a one-hour reset link
python scripts/reset_mfa.py owner@example.com                                  # clears two-step sign-in
```

**Left to you**

- **Secrets** live only in Render's Environment tab: never in the repository or the frontend.
- **The database**: Neon databases are reachable from the internet with the connection string, which is the
  password. Keep it only in Render and GitHub secrets. A Neon role with fewer rights can run the backups.
- **Updates**: Dependabot opens weekly update PRs; CI fails if a dependency has a known vulnerability.

## Backups

`.github/workflows/backup.yml` runs every night: it dumps the database, **restores the dump into a scratch
Postgres and checks the restaurant is there**, then keeps it, encrypted, as a workflow artifact for 30 days.
Turn it on with two repository secrets (Settings → Secrets and variables → Actions):

- `BACKUP_DATABASE_URL`: the Neon connection string.
- `BACKUP_PASSPHRASE`: a long random passphrase. Keep a copy somewhere safe: without it, no backup can be opened.

Run it once by hand (Actions → Nightly backup → Run workflow) to check it works.

To restore one: download the artifact from its run, then

```bash
gpg --decrypt fullhouse-2026-10-01.dump.gpg > backup.dump
pg_restore --no-owner --no-privileges --clean --if-exists -d "postgresql://...new-or-emptied-database..." backup.dump
```

Point `DATABASE_URL` at the restored database. Neon also keeps a short point-in-time history of its own
(Neon console → Branches → Restore); the nightly dump covers anything older.

## Error alerts

With `SENTRY_DSN` set, every unhandled error goes to [Sentry](https://sentry.io), which emails you the first
time each new kind happens. That covers an API request that fails, an agent run that crashes, and a crash in
someone's browser (the app reports those to its own server, which passes them on). The free plan is plenty
for a few restaurants.

1. Create a free Sentry account and a project of type **Python** (FastAPI).
2. Copy the project's DSN (Settings → Client Keys).
3. Set it as `SENTRY_DSN` on the service (Render: Environment → Add). Optionally set `SENTRY_ENVIRONMENT`
   (for example `pilot`) to tell restaurants apart.
4. Redeploy. The log line `error alerts: sending to Sentry` confirms it's on.

What is never sent: cookies, request bodies (so no passwords or two-step codes), the Authorization and
Idempotency-Key headers, query strings, one-time invite and reset tokens, and people's emails or IP
addresses. Errors are named by route (`/api/v1/auth/invite/{token}`), never the real link. With no DSN,
nothing leaves the server, and errors still appear as single lines in the service's logs.

Also worth turning on:

- **Render's own emails** (Account → Notifications): failed deploys, and the service going unhealthy.
  `/health` now checks the database, so a lost database connection counts as unhealthy.
- **An uptime check** on `/health` (UptimeRobot's free plan, every 5 minutes): it tells you when the site
  is down, and keeps a free instance awake.

## Rolling back

Something went wrong after a deploy? In order of how much they undo:

**1. Go back to the previous version of the app.** Render: the service → **Events** (or **Deploys**) → the
last good deploy → **Rollback**. It's live again in a minute or two. Turn off **Auto-Deploy** while you work
out what happened, so the next push doesn't redeploy the broken version.

**2. If that deploy also changed the database.** Rolling the app back doesn't undo database migrations.
Every migration here can be reversed, one step at a time. First find the revision the good version expects:
it's the `revision = ...` of the newest file in `backend/alembic/versions/` *in that version*. Then, from a
checkout of the **newer** code (which knows how to undo its own migrations):

```bash
cd backend
DATABASE_URL="postgresql://...production..." python -m alembic current      # where it is now
DATABASE_URL="postgresql://...production..." python -m alembic downgrade <good-revision>
```

Take a backup first (Actions → Nightly backup → Run workflow), and do it while the app is rolled back
(step 1), so nothing writes in between. Downgrades drop what the newer version added: for example, rolling
back past pack sizes forgets each item's pack size, but not its stock.

**3. If data itself is wrong.** For a few records, use **History & undo** in the app: every change can be
undone, including ones the Assistant Manager made. For something bigger, restore last night's backup (see
Backups above), or use Neon's point-in-time restore for a moment within the last day.

Before each deploy to a restaurant that's using it, check: tests pass in CI, the migration was tried on a
copy of real data (restore a backup into a scratch Neon branch and run `alembic upgrade head`), and you
know the revision you'd roll back to.

## Support contact and user guide

Set `SUPPORT_EMAIL` (and optionally `SUPPORT_URL`) and the Help page shows them to owners and staff. The
Help page also has a short guide to everyday tasks (the longer version is
[docs/USER_GUIDE.md](docs/USER_GUIDE.md)), a **Send feedback** button (also in the sidebar), and, for owners
and admins, **Download all data**: a zip of CSV files with everything the restaurant keeps here. Each export
is recorded in the security log.

Feedback is kept with the restaurant (owners and admins see it on the Help page) and logged as
`fullhouse.feedback` warnings, so it also turns up in the service's logs.

