# Deployment — job queue & containers (Phase 6 / Chunk 5)

The API no longer runs scans in-process. `POST /scan` enqueues a Celery task onto
Redis; a **separate worker container** runs the scan and writes progress + the
final result to the SQLite scan store on a **shared volume**, which the API reads
back via `GET /scan/{scan_id}`.

```
  POST /scan ─▶ api ──enqueue──▶ Redis ──deliver──▶ worker ──run scan──┐
  GET /scan/{id} ◀── api ◀────── scan_states.db  (shared /data volume) ◀┘
```

Broker/backend are **env-driven** (`CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`).
With **no** `CELERY_BROKER_URL`, Celery runs **eager** (synchronous, in-process) —
that is the fallback for the test suite / CI / a bare local run. Real async
dispatch requires a broker, i.e. Docker. **Native Windows without Docker is not a
supported *async* path** (it falls back to eager, which blocks the request for the
whole scan).

## Run the stack

```bash
docker compose up --build
```

That builds one image (used by both `api` and `worker`) and starts `redis`, `api`,
and `worker`. Phase E moved the ML stack (torch/transformers/faiss) out of this
image into `requirements-ml.lock`, which the Dockerfile does not install, so the
base lock is 174 packages and the multi-GB CPU-torch wheel is gone. Code-only
rebuilds are fast — dependencies are a cached layer.

This is the **development** stack. For a real deployment, see
[Deploy to a VPS](#deploy-to-a-vps-phase-f) below, which adds TLS, a web tier,
backups, and a rollback.

## Deploy to a VPS (Phase F)

The development stack above has no TLS, no frontend host, and no backup. This
section is the production path: one host, Caddy at the edge, images pulled by
tag so a rollback is a redeploy rather than a rebuild.

Everything the application serves lives on **one origin** — Caddy serves the
built React bundle at `/` and reverse-proxies `/api/*` to the API container. A
same-origin request has no CORS preflight to fail, which is why
`ALLOWED_ORIGINS` can stay unset in production.

### Requirements

- A host with **Docker Engine** and **Docker Compose v2.24 or newer**. The
  overlay uses `!reset` to remove the API's published port; older Compose
  versions reject the file rather than ignoring the tag.
- **Ports 80 and 443 both open.** 80 is required even though only 443 serves
  traffic: Let's Encrypt's HTTP-01 challenge uses it.
- A **DNS A record** pointing at the host, if you want TLS. Without one the
  stack still runs, on plain HTTP.

### First deploy

```bash
git clone https://github.com/Pranav-1201/AI-Code-Review-Agent.git
cd AI-Code-Review-Agent
cp .env.example .env
```

Edit `.env` and set at minimum:

| Variable | Why |
|---|---|
| `API_KEY` | **Not optional here.** `/scan` runs `git clone` on request. Leaving it unset publishes that to anyone who finds the host. It reaches the API and, through `/config.js`, the browser — nothing is rebuilt. |
| `SITE_ADDRESS` | Your hostname, e.g. `scan.example.com`. Unset means plain HTTP on `:80`. |
| `BACKUP_HOST_DIR` | Where snapshots land on the host, e.g. `/var/lib/acra-backups`. |

Then bring it up:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Caddy obtains a certificate on first boot. Nothing else is needed for TLS.

### Verification checklist

Confirm each of these yourself. None of them is asserted by this repository.

- [ ] `docker compose -f docker-compose.yml -f docker-compose.prod.yml ps`
      shows `api` **healthy**, and `web`, `worker`, `redis`, `backup` running.
- [ ] `curl -fsS https://<host>/api/health` returns JSON containing
      `"auth": "enabled"`. **If it says `disabled`, stop** — `API_KEY` did not
      reach the container, and the deployment is open.
- [ ] `curl -fsS https://<host>/config.js` contains your key, and a scan
      started from the UI does not fail with "Unauthorized".
- [ ] The app loads at `https://<host>/`.
- [ ] A deep link such as `https://<host>/history/x` survives a browser
      refresh (proves Caddy's `try_files` is serving the SPA shell).
- [ ] A scan's live progress updates continuously rather than arriving all at
      once at the end (proves SSE is not being buffered).
- [ ] `curl http://<host>:8000/health` **fails to connect.** Success means the
      API is exposed beside the proxy instead of behind it, which skips the
      `X-Forwarded-For` normalisation the rate limiter depends on.
- [ ] After one `BACKUP_INTERVAL_SECONDS`, `ls $BACKUP_HOST_DIR` contains a
      `scan-*.db`.

### Upgrade

```bash
IMAGE_TAG=<sha> docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
IMAGE_TAG=<sha> docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### Rollback

The same two commands with an earlier commit sha. Both the API and web images
carry the same tag and are published on the same run, so they move together —
rolling back only one would leave a bundle talking to an API it disagrees with.

Available tags are listed under the repository's Packages on GitHub.

### Restore the scan store

Stop the stack first. `scripts/restore-sqlite.sh` refuses a snapshot that
fails `integrity_check`, and deletes the `-wal`/`-shm` sidecars, which
otherwise get replayed over the restored file and quietly bring the old data
back.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
./scripts/restore-sqlite.sh /var/lib/acra-backups/scan-20260819T000000Z.db
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### What CI proves, and what it does not

The `deploy-stack` job in `.github/workflows/ci.yml` runs on every push. It
builds both images, renders the merged compose configuration, boots the whole
stack, and drives it through Caddy — asserting that `/api/health` is reachable
(so the `/api` prefix is being stripped), that a known route returns the SPA
shell, that an **unknown** path returns a real 404 whose body is still the
application, that static assets and `/favicon.svg` are served, that the
security headers are present, that port 8000 is **not** published, and that a
backup snapshot is actually written.

So "the images build", "the stack boots", and "the proxy routes correctly" are
gated claims, not assurances.

**Not covered by CI, and genuinely unverified until you deploy:** TLS
certificate issuance, real DNS, and behaviour under load. The CI job runs on
`:80` with no hostname, so it never exercises ACME.

**The CSP is asserted present, not proven non-breaking.** A header check cannot
tell you that a chart rendered or that a popover opened. The Playwright suite
runs against `vite preview`, which does not go through Caddy and therefore has
no CSP at all, so nothing in this repository currently exercises the app *under*
the policy. If something visual breaks after deploying, open the browser console
first: a CSP violation names the directive it blocked. `style-src` already
carries `'unsafe-inline'` because Radix and vaul set inline style attributes,
which is the failure this would otherwise have caused — but `script-src` is
strict deliberately and must stay that way.

### SEO surface

Set `VITE_SITE_URL` **at image build time** (see `.env.example`) once a domain
exists. Until then, `sitemap.xml`, the `robots.txt` sitemap pointer, canonical
links and `og:url` are all omitted on purpose rather than emitted with a
placeholder origin — a crawler acts on whatever URLs it is given, so wrong ones
are worse than absent ones.

The list of client-side routes lives in **two** places: `frontend/src/lib/routes.ts`
and the `@spa` matcher in the `Caddyfile`. Caddy cannot import TypeScript, so
this duplication is deliberate; `frontend/src/lib/routes.test.ts` reads the real
Caddyfile and fails if the two diverge. **Adding a page means editing both.**

Once a domain is live: submit `https://<domain>/sitemap.xml` in Google Search
Console, and prefer a DNS TXT record over `VITE_SEARCH_CONSOLE_TOKEN` for
verification — the TXT record survives a rebuild.

To harden HSTS after the domain has been stable on HTTPS for a while, add
`includeSubDomains` (and only then consider `preload`) to the
`Strict-Transport-Security` value in the `Caddyfile`. Both are close to one-way
doors, which is why neither ships by default.

## Verification checklist — please confirm on your Docker-capable machine

> Status: the **in-process (eager)** path and a **real out-of-process broker
> round-trip** are verified in-repo (see "Evidence" below). The **docker-compose
> boot is NOT verified by me** — Docker is not installed in the build environment.
> Treat the boxes below as unchecked until you run them.

1. **All three containers up**
   - `docker compose ps` → `redis`, `api`, `worker` all `Up` (redis `healthy`).
2. **Redis reachable**
   - `docker compose exec redis redis-cli ping` → `PONG`.
3. **Worker registered the task and is ready** (worker logs)
   - `docker compose logs worker` shows:
     - `celery@... ready.`
     - the task in the registered list: `. scan.run`
     - (if a scan was interrupted previously) `[worker] recovered N interrupted scan(s)...`
4. **API is serving**
   - `curl http://localhost:8000/` → `{"message":"AI Code Review Agent Running"}`.
5. **Enqueue a scan → worker picks it up out-of-process**
   - `curl -X POST http://localhost:8000/scan -H "Content-Type: application/json" -d '{"repo_path":"https://github.com/pallets/flask.git"}'`
     → returns `{"scan_id":"<uuid>"}` **immediately** (does not block for the scan).
   - `docker compose logs worker` then shows `Task scan.run[...] received` and,
     when finished, `Task scan.run[...] succeeded`.
6. **Status flows back through the shared volume**
   - `curl http://localhost:8000/scan/<uuid>` → status advances
     `starting → cloning → analyzing → finalizing → complete`
     (or `error` with a message). The API reads this from the same
     `scan_states.db` the worker wrote — proving the shared-volume round-trip.

## Environment variables

| Var | api | worker | Purpose |
|-----|-----|--------|---------|
| `CELERY_BROKER_URL` | ✅ | ✅ | Broker. Unset ⇒ eager mode. Compose: `redis://redis:6379/0`. |
| `CELERY_RESULT_BACKEND` | ✅ | ✅ | Optional; scan results persist in SQLite, not here. Compose: `redis://redis:6379/1`. |
| `SCAN_DB_PATH` | ✅ | ✅ | Scan store path. **Must be the same shared volume path in both** (`/data/scan_states.db`). |
| `API_KEY` | ✅ | — | Shared secret required in `X-API-Key`. **Unset ⇒ the API is open.** Worker serves no HTTP, so it has none. The `web` container also receives it and serves it to the browser via `/config.js` (D36). |
| `ALLOWED_ORIGINS` | ✅ | — | Comma-separated CORS origins. Default: localhost dev ports. Never `*`. |
| `ALLOWED_GIT_HOSTS` | ✅ | — | Comma-separated cloneable hosts. Setting it **replaces** the defaults (github/gitlab/bitbucket). |
| `RATE_LIMIT_PER_MINUTE` | ✅ | — | Per client, per route. Default 60. |
| `REDIS_URL` | ✅ | — | Shares the rate-limit window across API replicas. **Unset ⇒ per-replica limiting.** Point it at the broker the stack already runs, on its own database: `redis://redis:6379/2`. Worker serves no HTTP, so it needs none. |

Full annotated list, including the frontend and LLM variables: **`.env.example`**.

### Security posture before exposing this to the internet

`POST /scan` runs `git clone` on a caller-supplied URL. That is expensive and
abusable, so before the API is reachable publicly:

1. **Set `API_KEY`** to a long random value. `GET /health` reports
   `"auth": "enabled"` — check it, because an unset key fails open, not closed.
2. **Set `ALLOWED_ORIGINS`** to the real frontend origin.
3. **Nothing to rebuild for the key.** The web container serves `API_KEY` to
   the browser at runtime through `/config.js` (DECISIONS.md D36), so the
   published image works unchanged and rotating the key is a restart. Only a
   split-origin deployment still needs `VITE_API_BASE` at build time.
4. **Keep the reverse proxy in front.** `X-Forwarded-For` is trusted for the
   first hop when identifying clients for rate limiting; that header is
   spoofable if the app is exposed directly.

Repository URLs are validated before any clone: https-only, no embedded
credentials, host must be allowlisted, and literal private/loopback/link-local/
reserved IPs are rejected — which is what keeps `169.254.169.254` (cloud
metadata) and internal hosts out of reach even if the allowlist is widened.

### Dependency pinning

`requirements.lock` is a **pip-compile resolution** of `requirements.txt` — 76
pinned packages including transitives, with the `--extra-index-url` for the CPU
torch wheel baked in so the file is self-contained. CI and the Docker image both
install from it (`pip install -r requirements.lock`) so a transitive release
cannot silently change what ships between two builds of the same commit.
`requirements.txt` remains the loose human-edited declaration of intent.

It is deliberately **not** a `pip freeze`. Freezing this project's dev
virtualenv captured 131 packages including `chromadb`, `langchain`, `langgraph`,
`kubernetes` and `onnxruntime` — none declared, none imported — which would have
been baked into the production image. Regeneration command is in the lock header.

Two known gaps, both tracked for Phase E (image slimming): `pandas` and
`python-multipart` are declared in `requirements.txt` but imported nowhere, and
`torch` (~2 GB even as the CPU wheel) is pulled in for `llm_service.py` alone
despite the LLM path being gated off by default.

## Evidence already gathered (in-repo, no Docker)

- **Eager wiring** — `backend/tests/test_celery.py` (in the default suite): eager
  default without a broker; the task defers to `main.run_scan_pipeline`; worker
  owns zombie recovery; the API skips recovery in broker mode.
- **Real out-of-process dispatch** — `python backend/queue_roundtrip.py` spawns a
  real `celery worker` subprocess against a filesystem broker (no Redis), enqueues
  a task, and confirms it ran in a **different pid**. This is the Docker-free stand-in
  for step 5 above.
  (The script needs `pywin32` on Windows for the filesystem transport; it is a
  dev-only dep for the evidence run and is deliberately **not** in requirements.txt —
  production uses the Redis broker.)

## CI/CD (`.github/workflows/`)

**`ci.yml`** runs on every push to `main` or a `phase-*` branch and on every PR.
Two parallel jobs, running the same four commands that were previously only ever
run by hand on Windows:

| Job | Steps |
|-----|-------|
| `backend` | `pip install -r requirements.lock` → `pytest -q` → `run_benchmark.py --gate` |
| `frontend` | `npm ci` → `npx tsc --noEmit` → `npm run build` |

No environment variables are set, on purpose: every optional integration is off
by default, `API_KEY` unset exercises the open-API path, and `CELERY_BROKER_URL`
unset makes Celery run eagerly in-process — so **CI needs no Redis service**.

There is no `npm test` step yet. `package.json` wires vitest but the frontend has
zero test files, and vitest exits non-zero when it finds none — a red CI that
says nothing. The step lands with the tests (roadmap D/F5).

**`release.yml`** publishes the container image to GHCR. It is a separate
workflow gated on `workflow_run` of CI concluding `success`, because a published
image that never passed the tests is worse than no image — the tag implies it
did. It checks out `workflow_run.head_sha`, not whatever `main` points at when
it fires, so it cannot ship a different commit than the one CI validated.

### Rollback

Every image is tagged twice: the immutable **commit SHA** and `latest`. Rolling
back is re-deploying an explicit sha tag —

```
docker pull ghcr.io/<owner>/<repo>:<sha>
```

— rather than hoping a rebuild reproduces the previous state. Pair it with the
SQLite snapshot for data. The image name is lowercased in the workflow because
GHCR rejects uppercase paths and this repository's owner has capitals.

## Known caveats / deferred hardening

- **Single worker by design.** Zombie recovery reconciles *all* non-terminal scans
  at worker startup, which is only safe with one worker. Multiple workers/replicas
  need heartbeat-scoped recovery instead of reconcile-all. Don't set `deploy.replicas`
  on `worker` without that change.
- **SQLite on a shared volume** is fine on a single host (WAL is enabled), but is
  not a multi-host answer. Postgres is the path if the API and worker ever run on
  different hosts.
- **Rate limiting is in-process unless `REDIS_URL` is set** (S10). Left unset,
  the counter lives in the API container's memory — exactly accurate for the
  single-API-container deployment this compose file describes, and silently
  N × `RATE_LIMIT_PER_MINUTE` if you scale **api** to N replicas, because each
  replica counts only its own traffic. Set `REDIS_URL` to the broker the stack
  already runs and the window moves into Redis, shared by every replica, which
  is the prerequisite for scaling the API.

  Check which one is live rather than assuming: `GET /health` reports
  `"rate_limit": "redis"` or `"in-process"`. It **pings** Redis rather than
  merely checking that a client was constructed, so `in-process` on a
  deployment where you set `REDIS_URL` means Redis is genuinely unreachable.

  If Redis fails, the limiter degrades to the in-process window rather than
  failing open (which would delete the control during an incident) or failing
  closed (which would turn a broker hiccup into a total outage). It logs one
  warning per outage and stops retrying for 30s, so a dead Redis does not add
  a connect timeout to every request. See DECISIONS.md D34.
- **The API key is a single shared secret**, not per-user auth, and the frontend
  copy ships inside a public static bundle. It raises the cost of drive-by abuse
  of `/scan`; it does not identify or isolate callers. A login + short-lived
  token flow is the real answer if this ever serves more than its owner.
- **Disk ceilings bound the caches, not the whole host.** Phase E added LRU
  eviction across both caches (`MAX_CACHE_MB`) and a clone-size watchdog
  (`MAX_REPO_MB`); see "Operations (Phase E) → Disk ceilings" below for the
  values and how they interact. What remains true is that those ceilings cover
  the *analysis caches* only — the backup directory added in Phase F sits
  outside them and is bounded separately by `BACKUP_KEEP`.

## Operations (Phase E)

### Disk ceilings

Two caches grow as repositories are scanned: a full git clone per distinct
repository URL, and a small JSON prior per repository used for incremental
re-analysis. Both are now bounded.

| Variable | Default | Effect |
|---|---|---|
| `MAX_CACHE_MB` | `5120` | Combined ceiling across both caches. When exceeded, least-recently-scanned repositories are evicted — clone and prior together — at the start of the next scan. |
| `MAX_REPO_MB` | `1024` | Ceiling on a single repository. A clone growing past it is killed mid-flight, the partial tree deleted, and the scan fails with a clear error. |

Eviction runs inline at the start of a scan, not on a timer. An idle
deployment therefore reclaims nothing until the next scan arrives — which is
also the moment it next needs the space. If a host is tight on disk, lower
`MAX_CACHE_MB` rather than adding a cron job.

Eviction is best-effort: if it fails (a directory locked longer than the
delete retry budget), it is logged and the scan proceeds. It is housekeeping,
not part of the scan's contract.

A clone and its prior are always evicted as a pair. An orphaned prior would be
the dangerous direction — the pipeline gates incremental analysis on both
existing, so a prior without its clone falls back to a full scan correctly,
while a clone without its prior would silently stop diffing history.

`MAX_REPO_MB` is the guard against a hostile or accidental giant repository.
The clone timeout is not a substitute: a fast link moves many gigabytes well
inside 300 seconds. A watchdog thread polls the clone directory and kills git
on breach, because git has no native size limit.

### Logs

Containers emit one JSON object per line — the Dockerfile sets
`LOG_FORMAT=json`. Every record carries `timestamp`, `level`, `logger`,
`message`, and `scan_id`, plus `exc_info` when an exception is attached.

`scan_id` is `null` outside a scan, so it is always present and filtering a
whole scan out of an aggregator is a single query. The root logger is
configured, so uvicorn and celery records are included.

| Variable | Default | Effect |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `LOG_FORMAT` | `text` | `text` or `json`. The Dockerfile overrides this to `json`. |

Set `LOG_FORMAT=text` when tailing a container by hand and you want it
readable.

### Error reporting

Sentry is off unless `SENTRY_DSN` is set — with no DSN configured, nothing is
sent anywhere. To turn it on, set the DSN and optionally `SENTRY_ENVIRONMENT`
(default `development`), then restart the API and the worker.

`SENTRY_TRACES_SAMPLE_RATE` defaults to `0.0`; raise it only if you want
performance tracing and accept the cost.

### Optional ML stack

The production image does **not** install `torch`, `transformers`,
`sentence-transformers`, or `faiss-cpu`. No shipped configuration could reach
that code: no FAISS index is built into the image, so retrieval takes its
fallback path, and `ENABLE_CODEBERT` defaults to `false`. Removing them took
the base lock from 282 pinned packages to 163 and dropped the multi-gigabyte
CPU-torch wheel.

A deployment that genuinely wants CodeBERT scoring or FAISS retrieval needs
both locks, and an index built into the image:

```
pip install -r requirements.lock -r requirements-ml.lock
```

Without the extra installed, the retrieval and CodeBERT paths degrade quietly
and log at warning level. They do not fail the scan.
