# Running and deploying FullHouse

## Public demo (free)

The single-container image in the repo root is built for this: the frontend and API in one process, agents on the
scripted provider (no model, no API key), and a demo restaurant reloaded at every start and daily at 04:00 UTC. It
used about 100 MB of memory when measured locally, so a 512 MB free tier is plenty.

### Render — one click

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/eddierzhang/FullHouse-Updated)

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
Set `DEMO_MODE=false` and a different `LLM_PROVIDER` to run it as a real instance instead of a demo.

Be aware which of those are still *free*, as of September 2026: Fly.io and Koyeb have both retired their free
tiers, and Cloud Run's always-free grant (180,000 vCPU-seconds a month, about 50 CPU-hours) does not cover an
always-on process — and scaling it to zero throttles the CPU between requests, which stops the cron scheduler and
freezes any agent run still in flight. Render and Northflank are the free container hosts left.

**On an open demo, anyone can change anything** — that's the point, and the scheduled reset is what makes it safe.
Don't point a demo at data you care about.

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

**Keep the backend at one process.** Agent runs execute on its thread pool and stream through
an in-process broker, so scaling it to two replicas would split runs from their live streams.

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
   git clone https://github.com/eddierzhang/FullHouse-Updated.git
   cd FullHouse-Updated
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

The app has no login. Anyone who finds the URL can approve proposals, delete staff, change
prices and revert history. Pick one before sharing it:

- **Cloudflare Tunnel + Cloudflare Access (free)** — the best fit. The tunnel gives you an
  HTTPS URL without opening port 80 at all, and Access puts an email login in front of it.
- **nginx basic auth** — one password for everyone, a few lines in `frontend/nginx.conf`.
- **Restrict port 80 to your own IP** in the Oracle security list, if only you need it.

### Route 2: free platforms, on the scripted provider

Useful for showing the app, but agent runs won't reach a model for free:

- **The whole thing, one container:** Render's free web service — 512 MB, spins down after 15
  idle minutes. Spinning down kills any run in flight; the backend marks those runs failed on
  its next start. Northflank's free tier (two services) is the nearest alternative left.
- **Frontend alone:** Cloudflare Pages, Netlify or Vercel, all permanently free. Build with
  `VITE_API_BASE` set to the backend's URL, and set `FRONTEND_ORIGIN` on the backend to the
  frontend's URL for CORS.
- **No model:** there is no free host for Ollama at this size. Agents would need
  `LLM_PROVIDER=anthropic` and a paid API key.
- **Database:** Render's free disk is wiped on every deploy, so SQLite won't persist, and
  Render's free Postgres is deleted after 30 days. Neon's free tier (0.5 GB) is permanent, and
  Postgres is supported: point `DATABASE_URL` at it (CI runs the whole suite on Postgres 16).

### Route 3: a free VM on one of the big clouds

Both of these run the compose stack the way the Oracle route does, and neither has the RAM for
a local model — use `LLM_PROVIDER=scripted`, or `anthropic` with a key.

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
