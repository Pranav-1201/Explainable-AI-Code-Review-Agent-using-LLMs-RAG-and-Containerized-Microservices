# Phase M — Free deploy (Oracle Always Free A1 + DuckDNS): design

**Date:** 2026-09-10
**Branch:** `phase-m/runtime-config-arm64`
**Baseline:** `main` = `85269e6` (S10 shipped; CI green, run `34270772454`)
**Status:** design approved; not yet implemented

---

## 1. Goal and non-goals

### Goal

Close Phase M as `docs/STAFF_AUDIT_2026-08-19.md` defines it — live on a real
host with TLS, backups and a **drilled** rollback — at **zero cost**:
`curl https://<name>.duckdns.org/api/health` returns 200 with
`"auth": "enabled"`, and rolling `IMAGE_TAG` back to an older sha returns 200
again.

Phase F made the repository deployable. Reading it against a real free host
showed that the documented path does not actually work (section 2), so this
phase is two repository fixes plus the deploy itself.

### Non-goals

- **Real authentication.** The shared `API_KEY` stays a shared key whose
  browser copy is readable by anyone who loads the page — unchanged by this
  design and already documented in `api.ts`. Login + tokens is roadmap item G.
- **Solving Oracle's idle reclamation** (section 7). Flagged in the runbook,
  not engineered around.
- **Paid hosting, a purchased domain, provider automation** (Terraform etc.).
- **Sentry.** Stays off unless the operator sets `SENTRY_DSN`; the runbook
  says how.

---

## 2. What the documented path gets wrong — found 2026-09-10

1. **Every scan would 401.** *Derived from the code, not yet observed* —
   section 6's authenticated CI boot is the first observation. `DEPLOYMENT.md` makes `API_KEY` mandatory on a
   public host, and the prod overlay pulls the published web image. That image
   is built by `release.yml` with only `VITE_API_BASE` and `VITE_SITE_URL` —
   `VITE_API_KEY` is deliberately never passed, because a published image is
   public. So the bundle sends no key, `api_guard` protects everything outside
   `PUBLIC_PATHS` (`/`, `/health`, the docs routes), and `/scan`, `/scans`,
   `/settings` all return 401. **CI never saw this:** `deploy-stack` boots with
   `API_KEY` unset.
2. **The images cannot run on the free host.** Both GHCR images are public, and
   an anonymous manifest probe shows each carries exactly one platform,
   `linux/amd64` (plus a buildx attestation). The free Oracle shape with usable
   memory is A1 — **ARM**. Neither workflow sets `platforms:`.

Feasibility of an arm64 build, checked against PyPI for every pin in
`requirements.lock`: **60 of 60** install on `linux/aarch64` under CPython 3.11
from wheels — 52 pure-Python, 8 native with a cp311/abi3 aarch64 wheel (numpy,
pandas, pydantic-core, scikit-learn, scipy, tree-sitter,
tree-sitter-javascript, tree-sitter-typescript). No pin needs a compiler. This
proves the wheels exist, not that the image builds; section 6 makes CI prove
that.

---

## 3. Host choice — verified 2026-09-10

| | Oracle Always Free A1 | Oracle E2.1.Micro | GCP free e2-micro |
|---|---|---|---|
| Arch | ARM | x86 | x86 |
| CPU / RAM | **2 OCPU / 12 GB total** | 1/8 OCPU / 1 GB | e2-micro / 1 GB |
| Egress | 10 TB/month | 10 TB/month | **1 GB/month** |

Chosen: **A1**. The two x86 options run today's images unchanged but hold the
API, a two-process Celery worker, Redis and Caddy in 1 GB; GCP's 1 GB of egress
is also exhausted by a handful of page loads.

**The A1 allowance was halved in 2026** — Oracle's own Always Free page now
states 2 OCPUs / 12 GB (1,500 OCPU-hours, 9,000 GB-hours a month). The earlier
4 / 24 figure appears in sources dated as late as February 2026, and in this
design's first draft. Secondary sources date the change to 2026-06-15.

**DNS:** DuckDNS. `duckdns.org` is on the Public Suffix List (line 13015 of
16,477 on 2026-09-10), so each subdomain gets its own Let's Encrypt rate-limit
bucket. `nip.io` and `sslip.io` are not listed and would share one bucket with
every other user.

---

## 4. Component design

### 4.1 The API key arrives at runtime, not build time

```
browser ── GET /config.js ──▶ Caddy ── templates ──▶ env API_KEY (web container)
   │                                                     ▲
   └─ api.ts reads window.__ACRA_CONFIG__.apiKey          └─ same .env value the api container reads
```

- `frontend/index.html` loads `<script src="/config.js"></script>` before the
  module bundle. Same-origin, so the CSP stays `script-src 'self'` — the same
  reason `theme-init.js` is a file rather than an inline script.
- `frontend/src/lib/api.ts` resolves the key **at call time**: a non-empty
  `window.__ACRA_CONFIG__.apiKey`, else `import.meta.env.VITE_API_KEY`, else
  none. Call time rather than module load mirrors `api_guard`'s env-at-call-time
  rule and is what makes it unit-testable. `apiHeaders()` and `streamUrl()`
  both use it. The 401 message stops telling the user to set `VITE_API_KEY`.
- `frontend/public/config.js` is a static default (`window.__ACRA_CONFIG__ =
  {};`). Vite copies it into `dist/`, so `npm run dev`, `vite preview` and
  Playwright all get a valid script with no key — local behaviour unchanged.
- `config.js.template` (repository root, beside the `Caddyfile`) is copied over
  that default in the web image's final stage. Its body renders the key with
  `{{env "API_KEY" | js}}`. A `.template` extension keeps it out of every
  frontend lint and typecheck; Caddy decides whether to render from the
  response's Content-Type, which comes from the served name `/srv/config.js`.
- `ARG VITE_API_KEY` is removed from `frontend/Dockerfile`, so no image can
  bake a key even by accident.

### 4.2 `Caddyfile`

A handle for exactly `/config.js`, ahead of the catch-all:

- `templates` with an explicit `mime` for `text/javascript` and
  `application/javascript` — Caddy's default set is HTML and plain text only,
  so without it the placeholder would be served literally.
- `Cache-Control: no-store`, so rotating the key is a restart, never a wait on
  a browser cache.
- It must not touch the `@spa` matcher line: `routes.test.ts` parses exactly
  `^\s*@spa\s+path` and would fail on any change to that list.

**Escaping.** Go's documentation describes `js` only as returning "the escaped
JavaScript equivalent" and does not enumerate the characters. The design does
not rely on that sentence: section 6 boots CI with a key containing `"`, `\`
and `<` and requires `node` to read back a byte-identical value.

### 4.3 `docker-compose.prod.yml`

`web.environment` gains `API_KEY: ${API_KEY:-}` — one `.env` line feeds both
containers, so the served key and the checked key cannot drift.
`backend/tests/test_compose_contract.py` pins the compose files and is extended
to assert this.

### 4.4 ARM64 images

- **`frontend/Dockerfile`:** the build stage becomes
  `FROM --platform=$BUILDPLATFORM node:20-alpine`. The bundle is
  architecture-neutral, so npm and Vite run natively and only the final
  `caddy:2-alpine` stage is per-platform.
- **`release.yml`:** `docker/setup-qemu-action` plus
  `platforms: linux/amd64,linux/arm64` on both build steps. Each image gets its
  own GitHub Actions cache scope — today both write the default scope and evict
  each other, which matters once an emulated arm64 build is uncached.
- **`ci.yml` `deploy-stack`:** a matrix over `ubuntu-latest` and
  `ubuntu-24.04-arm`, with the cache scope keyed by architecture. The whole
  stack is built and booted natively on arm64 before anything merges, so the VM
  is not the first place the arm64 stack ever runs.

---

## 5. Documentation changes

- **`DEPLOYMENT.md`** — new section *Free deploy — Oracle Always Free A1 +
  DuckDNS*: signup (a card is required; secondary sources report a $1
  authorisation that is not charged — Oracle's Always Free page itself does
  not say; the home region is permanent and A1
  capacity varies by region); an A1 shape within 2 OCPU / 12 GB; ingress on
  80 and 443 in **both** the VCN security list and the host firewall; Docker
  on arm64; the DuckDNS A record; `API_KEY` from `openssl rand -hex 32`; the
  existing verification checklist; and the rollback drill.
- **`DEPLOYMENT.md`** — correct the stale step "Rebuild the frontend with
  `VITE_API_BASE` and `VITE_API_KEY` set": with runtime config the published
  image serves whatever `API_KEY` the host holds.
- **`.env.example`** — `API_KEY` is now also read by the web container;
  `VITE_API_KEY` is a local-development fallback only.
- **`docs/DECISIONS.md`** — **D36**: the key moves from build time to runtime.
- **`docs/HANDOVER.md`** — state at session end.

---

## 6. Testing strategy

| Layer | What it proves |
|---|---|
| vitest (new cases) | runtime key wins over `VITE_API_KEY`; env fallback when runtime is empty; no `X-API-Key` header and no `?api_key=` when neither is set; the stream URL carries the runtime key |
| `test_compose_contract.py` | the web service receives `API_KEY` |
| `routes.test.ts` (unchanged) | the new handle did not disturb `@spa` |
| `deploy-stack`, **both architectures** | boots with `API_KEY` containing `"`, `\` and `<`; `/api/health` reports `"auth": "enabled"`; `/api/scans` → **401** without the key and **200** with it; `/config.js` → 200 with `Cache-Control: no-store`, and `node` (added with `actions/setup-node` if the runner image lacks it) evaluates it to an `apiKey` byte-identical to the env value; the key string appears in **no** `/assets/*.js`; every existing assertion unchanged |
| Playwright (unchanged) | runs under `vite preview`, served the default `config.js`, with no key |
| The live host | `curl https://<name>.duckdns.org/api/health` → 200 with `"auth": "enabled"`; one scan through the real UI; rollback drill |

Every new CI assertion is to be watched **failing** against the pre-change code
before it is believed — a gate that passes before and after a fix measures
nothing.

---

## 7. Risks

- **Rollback on ARM reaches only shas published after this change.** Every
  existing tag is amd64-only and will fail to pull on the VM ("no matching
  manifest for linux/arm64"). The drill needs two multi-arch shas: this
  branch's merge commit and the handover commit after it.
- **Oracle reclaims idle Always Free instances.** Oracle's page: an instance
  is idle if, over 7 days, CPU p95 < 20%, network < 20%, and — on A1 only —
  memory < 20%. A low-traffic demo can meet all three. Not solved here; the
  runbook says so.
- **Free arm64 runners for public repos are unverified.** `ubuntu-24.04-arm`
  is listed as generally available; no source consulted states pricing. The
  repository is public. If the label is unavailable the matrix job queues
  rather than failing, which will be visible on the first push.
- **A1 capacity.** "Out of capacity" on A1 is widely reported and
  region-dependent; the fallback is retrying or another region, decided at
  signup.
- **Key visibility is unchanged, not improved.** Anyone who loads the page can
  read `/config.js`, exactly as they could read the old inlined bundle value.

---

## 8. Files

| File | Change |
|---|---|
| `frontend/src/lib/api.ts` | call-time key resolution; 401 message |
| `frontend/src/vite-env.d.ts` | type for `window.__ACRA_CONFIG__` |
| `frontend/index.html` | load `/config.js` before the bundle |
| `frontend/public/config.js` | **new** — static empty default |
| `frontend/src/lib/api.test.ts` | **new** — key-resolution cases (no test file for `api.ts` exists today) |
| `config.js.template` | **new** — rendered by Caddy |
| `Caddyfile` | `/config.js` handle: templates, mime, no-store |
| `frontend/Dockerfile` | `$BUILDPLATFORM` build stage; copy template; drop `ARG VITE_API_KEY` |
| `docker-compose.prod.yml` | `web.environment.API_KEY` |
| `backend/tests/test_compose_contract.py` | assert the web service receives `API_KEY` |
| `.github/workflows/ci.yml` | `deploy-stack` arch matrix; auth-enabled boot; new assertions |
| `.github/workflows/release.yml` | QEMU; two platforms; per-image cache scope |
| `DEPLOYMENT.md`, `.env.example` | runbook; stale corrections |
| `docs/DECISIONS.md`, `docs/HANDOVER.md` | D36; handover |
