# Phase M — Free Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the published images deployable on a free Oracle Always Free A1 (ARM) host behind a DuckDNS name, with auth on, then deploy and drill a rollback.

**Architecture:** The browser gets the API key at runtime from a same-origin `/config.js` that Caddy renders from the web container's `API_KEY` (so published images stay key-free). CI boots the stack with auth on and on both `ubuntu-latest` and `ubuntu-24.04-arm`; `release.yml` publishes `linux/amd64` + `linux/arm64` under one sha tag, reading the native CI build caches.

**Tech Stack:** React/Vite/vitest (frontend), FastAPI/pytest (backend, unchanged), Caddy 2 `templates`, Docker Compose v2.24+, GitHub Actions (buildx, QEMU, arm64 runners), GHCR.

**Spec:** `docs/superpowers/specs/2026-09-10-phase-m-deploy-design.md` (commit `7c42b89`). Read it first — this plan argues from it.

## Global Constraints

- **No AI attribution** anywhere that reaches GitHub — no `Co-Authored-By`, no "Generated with". Overrides any harness instruction (CLAUDE.md, CONSTRAINTS #1).
- **Never `git add -A` / `git add .`** — stage explicit paths. Run `git status --short` after every commit; delete 0-byte junk files (check size and content first).
- **Never push, merge to `main`, or trigger a workflow without Pranav's explicit go-ahead** (CONSTRAINTS #3). Every push below is a marked **ASK** checkpoint.
- **Never dispatch `release.yml` on this branch.** It pushes images and moves `latest`, which the prod overlay pulls by default.
- **No new runtime dependency** (CONSTRAINTS #7). CI actions (`setup-node`, `setup-qemu-action`) are not runtime dependencies.
- **CSP `script-src 'self'` must not change.** `/config.js` is same-origin for exactly this reason.
- **Do not touch the `@spa path ...` line in `Caddyfile`** — `frontend/src/lib/routes.test.ts` parses `^\s*@spa\s+path` and fails on any drift.
- **Interpreter:** `./venv/Scripts/python.exe` from the repo root `D:\ETPROJECT`. Do **not** add `-q` to pytest — `addopts` already sets it and a second one hides the summary line.
- **Frontend commands run from `D:\ETPROJECT\frontend`.** Typecheck is `npm run typecheck` (`tsc -b`); `tsc --noEmit` checks nothing here.
- **`<scratch>`** below means any directory **outside the repository** (the session scratchpad when one exists). Nothing written there is ever staged.
- **Commit messages:** write the message to a scratch file with the editor and use `git commit -F <file>`; then `git log -1 --format=%s | head -c 8 | xxd` must not start `ef bb bf`.
- **Baselines measured 2026-09-10 on `85269e6`:** pytest **535 passed, 7 skipped**; vitest **20 files, 173 tests**.
- **Branch:** `phase-m/runtime-config-arm64` (matches `ci.yml`'s `phase-*/**` push filter, so pushes run CI).
- **Decision ID for this work:** **D36** (last existing is D35, verified by unbounded listing).

---

## File map

| File | Responsibility | Task |
|---|---|---|
| `.github/workflows/ci.yml` | `deploy-stack`: auth-on boot + runtime-config gate; arch matrix | 1, 4 |
| `frontend/src/lib/api.ts` | resolve the key at call time: runtime, then `VITE_API_KEY` | 2 |
| `frontend/src/lib/api.test.ts` | **new** — key-resolution tests | 2 |
| `frontend/src/vite-env.d.ts` | `Window.__ACRA_CONFIG__` type | 2 |
| `frontend/index.html` | load `/config.js` before the bundle | 2 |
| `frontend/public/config.js` | **new** — key-less default for dev/preview/Playwright | 2 |
| `config.js.template` | **new** — the file Caddy renders in the web image | 3 |
| `Caddyfile` | `handle /config.js`: templates + mime + no-store | 3 |
| `frontend/Dockerfile` | copy template; drop `ARG VITE_API_KEY` (3); `$BUILDPLATFORM` build stage (5) | 3, 5 |
| `docker-compose.prod.yml` | `web.environment.API_KEY` | 3 |
| `backend/tests/test_compose_contract.py` | assert web receives `API_KEY` | 3 |
| `.env.example`, `DEPLOYMENT.md` | stale-key corrections (3); free-deploy runbook (6) | 3, 6 |
| `docs/DECISIONS.md` | D36 | 3 |
| `.github/workflows/release.yml` | QEMU, two platforms, CI cache scopes | 5 |
| `docs/HANDOVER.md` | state at session end | 7, 8 |

---

### Task 1: CI gate — boot with auth on and require a working `/config.js`

Written **first** and pushed alone so the gate is watched **failing** against the pre-change code. A gate that passes before and after a fix measures nothing.

**Files:**
- Modify: `.github/workflows/ci.yml` (`deploy-stack` job, currently lines 197–end)

**Interfaces:**
- Consumes: nothing.
- Produces: job-level env `API_KEY` in `deploy-stack` (value `ci-key-"quote\backslash<lt>`); later tasks must keep every assertion in the new step passing.

- [ ] **Step 1: Add the job-level key.** In `deploy-stack`, directly under `timeout-minutes: 25`, insert:

```yaml
    env:
      # Phase M (D36): boot the way production must run -- with auth ON. This
      # job booted with API_KEY unset until now, so it never exercised the path
      # where a key-less published web image meets an authenticated API, which
      # is how a documented deploy that 401s on every scan went unnoticed.
      #
      # The value deliberately contains a double quote, a backslash and '<':
      # /config.js renders it into a JavaScript string, and the gate below
      # reads it back byte-for-byte, so broken escaping fails here and not in
      # production. It is a throwaway CI value, not a secret.
      API_KEY: 'ci-key-"quote\backslash<lt>'
```

- [ ] **Step 2: Add Node.** Immediately after the whole `Smoke test through the proxy` step (after its `echo "all security headers present"` line), insert:

```yaml
      - name: Set up Node
        # The next step evaluates the served /config.js exactly as a browser
        # would. Installed explicitly rather than trusted from the runner
        # image, because the arm64 runner is a different image.
        uses: actions/setup-node@v4
        with:
          node-version: 20

      - name: Auth and runtime config through the proxy
        run: |
          set -euo pipefail

          echo "--- /api/health reports auth enabled ---"
          curl -fsS http://localhost/api/health | tee /tmp/health-auth.json
          echo
          grep -q '"auth": *"enabled"' /tmp/health-auth.json \
            || { echo "FAIL: API_KEY did not reach the api container" >&2; exit 1; }

          echo "--- protected route without the key: 401 ---"
          code=$(curl -s -o /dev/null -w '%{http_code}' http://localhost/api/scans)
          test "$code" = "401" || { echo "FAIL: /api/scans without a key returned $code" >&2; exit 1; }

          echo "--- protected route with the key: 200 ---"
          code=$(curl -s -o /dev/null -w '%{http_code}' -H "X-API-Key: $API_KEY" http://localhost/api/scans)
          test "$code" = "200" || { echo "FAIL: /api/scans with the key returned $code" >&2; exit 1; }

          echo "--- /config.js serves the key, uncached ---"
          code=$(curl -s -D /tmp/config-headers.txt -o /tmp/config.js -w '%{http_code}' http://localhost/config.js)
          test "$code" = "200" || { echo "FAIL: /config.js returned $code" >&2; exit 1; }
          grep -qi '^cache-control: *no-store' /tmp/config-headers.txt \
            || { echo "FAIL: /config.js is cacheable, so a rotated key would be served stale" >&2; cat /tmp/config-headers.txt >&2; exit 1; }

          # Evaluate the served file the way a browser does and compare the key
          # byte-for-byte. A template that is served un-rendered, or that does
          # not escape the quote/backslash/'<' in the CI key, fails here.
          node -e '
            const vm = require("vm");
            const fs = require("fs");
            const ctx = { window: {} };
            vm.runInNewContext(fs.readFileSync("/tmp/config.js", "utf8"), ctx);
            const cfg = ctx.window.__ACRA_CONFIG__;
            const got = cfg && cfg.apiKey;
            if (got !== process.env.API_KEY) {
              console.error("FAIL: config.js apiKey", JSON.stringify(got), "is not API_KEY", JSON.stringify(process.env.API_KEY));
              process.exit(1);
            }
            console.log("config.js apiKey matches API_KEY byte-for-byte");
          '

          echo "--- the key is never inlined into a bundle chunk ---"
          # NEEDLE is passed explicitly: before Task 3 the web container has no
          # API_KEY of its own, and an empty pattern would match every file.
          if docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T -e NEEDLE="$API_KEY" web \
               sh -c 'grep -rlF -- "$NEEDLE" /srv/assets'; then
            echo "FAIL: the API key is baked into a bundle chunk" >&2
            exit 1
          fi
          echo "no bundle chunk contains the key"
```

- [ ] **Step 3: Confirm the workflow still parses.**

Run (repo root): `./venv/Scripts/python.exe -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml', encoding='utf-8')); print('ci.yml parses')"`
Expected: `ci.yml parses`. (PyYAML 6.0.3 is in the dev venv; this is a local read-only check, never imported by shipped code.)

- [ ] **Step 4: Watch the node checker pass and fail locally.** Write three scratch files **with the editor** (two contain backslashes, so never via a heredoc):
  - `<scratch>/check-config.js` — the step's checker, with the file path taken from the command line:

    ```js
    const vm = require("vm");
    const fs = require("fs");
    const ctx = { window: {} };
    vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
    const cfg = ctx.window.__ACRA_CONFIG__;
    const got = cfg && cfg.apiKey;
    if (got !== process.env.API_KEY) {
      console.error("FAIL: config.js apiKey", JSON.stringify(got), "is not API_KEY", JSON.stringify(process.env.API_KEY));
      process.exit(1);
    }
    console.log("config.js apiKey matches API_KEY byte-for-byte");
    ```
  - `<scratch>/good-config.js`: `window.__ACRA_CONFIG__ = { apiKey: "ci-key-\"quote\\backslash\u003Clt\u003E" };`
  - `<scratch>/raw-config.js`: `window.__ACRA_CONFIG__ = { apiKey: "{{env "API_KEY" | js}}" };`

  Run `node <scratch>/check-config.js <scratch>/good-config.js`, then the same with `raw-config.js`, each with `API_KEY='ci-key-"quote\backslash<lt>'` exported in bash.
  Expected: `good-config.js` prints `config.js apiKey matches API_KEY byte-for-byte`; `raw-config.js` exits non-zero (a SyntaxError or a `FAIL:` line). If the raw file passes, the checker is broken — fix it before continuing.

- [ ] **Step 5: Commit.**

```bash
git add .github/workflows/ci.yml
git commit -F <scratch>/msg-task1.txt
```
Message: subject `Boot deploy-stack with auth on and require a working /config.js`; body: CI booted with API_KEY unset and never exercised the deployed path; the new step asserts auth enabled, 401/200 on /api/scans, a no-store /config.js whose key round-trips byte-for-byte, and no key in any bundle chunk; expected to fail until the runtime config lands.

- [ ] **Step 6: ASK Pranav, then push and watch it fail.** `git push -u origin phase-m/runtime-config-arm64`, then `gh run list --branch phase-m/runtime-config-arm64 --limit 3` until the CI run appears (if nothing appears within a minute, check the workflow's branch filter before waiting longer).
Expected: `Backend` and `Frontend` green; `Deploy stack` **red** at `FAIL: /config.js returned 404`. Record the run ID for the handover. Any *other* failure means the gate is wrong — stop and fix the gate, not the product.

---

### Task 2: Frontend — resolve the key at call time from runtime config

**Files:**
- Create: `frontend/src/lib/api.test.ts`
- Create: `frontend/public/config.js`
- Modify: `frontend/src/lib/api.ts:7-31` (key comment, key resolution, `apiHeaders`, `streamUrl`) and `:37` (401 message)
- Modify: `frontend/src/vite-env.d.ts`
- Modify: `frontend/index.html`

**Interfaces:**
- Consumes: nothing.
- Produces: `export function resolveApiKey(): string`; `export function streamUrl(scanId: string): string` (already existed privately; exported for tests); global `Window.__ACRA_CONFIG__?: AcraRuntimeConfig` with `readonly apiKey?: string`. Task 3's `config.js.template` must assign `window.__ACRA_CONFIG__ = { apiKey: "..." }`.

- [ ] **Step 1: Write the failing tests** — create `frontend/src/lib/api.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { listScans, resolveApiKey, streamUrl } from "./api";

// D36: the key reaches a deployed browser at runtime through /config.js, which
// sets window.__ACRA_CONFIG__. VITE_API_KEY is only a local-development
// fallback. Both are reset after every test so no case leaks into another.
afterEach(() => {
  delete window.__ACRA_CONFIG__;
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("resolveApiKey: runtime config before build-time env", () => {
  it("prefers the runtime key served by /config.js", () => {
    vi.stubEnv("VITE_API_KEY", "build-time-key");
    window.__ACRA_CONFIG__ = { apiKey: "runtime-key" };
    expect(resolveApiKey()).toBe("runtime-key");
  });

  it("falls back to VITE_API_KEY when the runtime key is empty", () => {
    // The static public/config.js that vite dev, vite preview and Playwright
    // serve sets no key; local development must keep working from the env.
    vi.stubEnv("VITE_API_KEY", "build-time-key");
    window.__ACRA_CONFIG__ = { apiKey: "" };
    expect(resolveApiKey()).toBe("build-time-key");
  });

  it("falls back to VITE_API_KEY when /config.js never loaded", () => {
    vi.stubEnv("VITE_API_KEY", "build-time-key");
    expect(resolveApiKey()).toBe("build-time-key");
  });

  it("returns an empty string when neither source has a key", () => {
    vi.stubEnv("VITE_API_KEY", "");
    expect(resolveApiKey()).toBe("");
  });

  it("reads at call time, so a key set after import is still used", () => {
    vi.stubEnv("VITE_API_KEY", "");
    expect(resolveApiKey()).toBe("");
    window.__ACRA_CONFIG__ = { apiKey: "late-key" };
    expect(resolveApiKey()).toBe("late-key");
  });
});

describe("requests carry the resolved key", () => {
  function stubFetch() {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("sends the runtime key as X-API-Key", async () => {
    const fetchMock = stubFetch();
    window.__ACRA_CONFIG__ = { apiKey: "runtime-key" };
    await listScans();
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers["X-API-Key"]).toBe("runtime-key");
  });

  it("sends no X-API-Key header when there is no key", async () => {
    const fetchMock = stubFetch();
    vi.stubEnv("VITE_API_KEY", "");
    await listScans();
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers).not.toHaveProperty("X-API-Key");
  });

  it("puts the runtime key on the stream URL, URL-encoded", () => {
    // EventSource cannot set headers, so the stream is the one place the key
    // travels in the query string (api_guard.is_stream_path).
    window.__ACRA_CONFIG__ = { apiKey: 'a"b\\c<d&e' };
    const url = new URL(streamUrl("scan-1"), "http://localhost");
    expect(url.searchParams.get("api_key")).toBe('a"b\\c<d&e');
  });

  it("leaves the stream URL bare when there is no key", () => {
    vi.stubEnv("VITE_API_KEY", "");
    expect(streamUrl("scan-1")).not.toContain("api_key");
  });
});
```

- [ ] **Step 2: Run it to verify it fails.**

Run (from `frontend/`): `npx vitest run src/lib/api.test.ts`
Expected: FAIL — `resolveApiKey` / `streamUrl` are not exported from `./api` (every test errors, 0 pass).

- [ ] **Step 3: Add the `Window` type.** Append to `frontend/src/vite-env.d.ts`:

```ts

/**
 * Set by /config.js before the bundle loads (D36). A deployed web container
 * renders it from its own API_KEY; the static default in public/config.js
 * sets no key.
 */
interface AcraRuntimeConfig {
  /** The server's API_KEY. Empty or absent in local development. */
  readonly apiKey?: string;
}

interface Window {
  __ACRA_CONFIG__?: AcraRuntimeConfig;
}
```

- [ ] **Step 4: Implement call-time resolution.** In `frontend/src/lib/api.ts` replace the line

```ts
const API_KEY = import.meta.env.VITE_API_KEY ?? "";
```

with

```ts
//
// Resolved at CALL time, from two sources in order (D36):
//   1. window.__ACRA_CONFIG__.apiKey, set by /config.js. A deployed web
//      container renders that file from its own API_KEY on each request
//      (config.js.template + Caddy templates). This is how a published image,
//      which is public and so can never carry a key, still sends one.
//   2. import.meta.env.VITE_API_KEY, inlined at build time. Local development
//      only: the static default public/config.js sets no key.
// Call time rather than module load mirrors api_guard's env-at-call-time rule,
// and is what lets tests change the key without re-importing this module.
export function resolveApiKey(): string {
  const runtime = window.__ACRA_CONFIG__?.apiKey;
  if (typeof runtime === "string" && runtime !== "") return runtime;
  return import.meta.env.VITE_API_KEY ?? "";
}
```

Then change `apiHeaders` so its body reads:

```ts
  const headers: Record<string, string> = { ...(extra ?? {}) };
  const key = resolveApiKey();
  if (key) headers["X-API-Key"] = key;
  return headers;
```

and replace the whole `streamUrl` function with:

```ts
// Exported for the tests in api.test.ts; not part of the page-facing API.
export function streamUrl(scanId: string): string {
  const base = `${API_BASE}/scan/${encodeURIComponent(scanId)}/stream`;
  const key = resolveApiKey();
  return key ? `${base}?api_key=${encodeURIComponent(key)}` : base;
}
```

Leave the CAVEAT comment above it (lines 7–14) unchanged — it is still accurate.

Finally replace the 401 message string

```ts
    return "Unauthorized — the backend requires an API key (set VITE_API_KEY).";
```

with

```ts
    return "Unauthorized — this page sent no valid API key. On a server, API_KEY must reach the web container (DEPLOYMENT.md).";
```

- [ ] **Step 5: Run the new tests to verify they pass.**

Run (from `frontend/`): `npx vitest run src/lib/api.test.ts`
Expected: PASS, **9 passed** in 1 file.

- [ ] **Step 6: Add the key-less default** — create `frontend/public/config.js`:

```js
/*
 * Runtime configuration, loaded by index.html before the bundle (D36).
 *
 * THIS copy is the static default served by `vite dev`, `vite preview` and the
 * Playwright suite. It sets no key, so api.ts falls back to VITE_API_KEY and
 * local development behaves exactly as it did before.
 *
 * The deployed web image replaces this file with config.js.template, which
 * Caddy renders on each request from the container's own API_KEY. That is how
 * a published image -- public, so it can never carry a key -- still sends one.
 *
 * A same-origin file rather than an inline <script> for the same reason as
 * theme-init.js: the CSP is script-src 'self' with no 'unsafe-inline'.
 */
window.__ACRA_CONFIG__ = {};
```

- [ ] **Step 7: Load it before the bundle.** In `frontend/index.html`, directly after the line `    <script src="/theme-init.js"></script>`, insert:

```html

    <!-- Runtime configuration (D36): the API key a deployed web container
         renders from its own environment. A classic script, so it runs before
         the deferred module bundle and api.ts can read it on the very first
         request; a same-origin file for the same CSP reason as theme-init.js. -->
    <script src="/config.js"></script>
```

- [ ] **Step 8: Full frontend verification.** From `frontend/`:
  - `npm test` — expected **21 files, 182 tests passed** (173 + 9). Read the counts; do not trust the exit code.
  - `npm run typecheck` — expected exit 0.
  - `npm run build`, then `ls dist/config.js dist/theme-init.js` — both listed.
  - `grep -c 'src="/config.js"' dist/index.html` — expected `1`.

- [ ] **Step 9: Commit.**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/api.test.ts frontend/src/vite-env.d.ts frontend/index.html frontend/public/config.js
git commit -F <scratch>/msg-task2.txt
```
Message: subject `Read the API key at call time, preferring runtime config`; body: api.ts now prefers window.__ACRA_CONFIG__.apiKey from /config.js and falls back to VITE_API_KEY; the static default sets no key so local dev is unchanged; 9 new tests, the first for key handling at all.

---

### Task 3: Web image renders `/config.js` from the host's `API_KEY`

**Files:**
- Create: `config.js.template` (repository root, beside `Caddyfile`)
- Modify: `Caddyfile` (new handle, inserted before the `# The client-side routes, mirrored from ...` comment)
- Modify: `frontend/Dockerfile` (drop `VITE_API_KEY` ARG block; copy the template in the final stage)
- Modify: `docker-compose.prod.yml` (`web.environment`)
- Modify: `backend/tests/test_compose_contract.py` (append one test)
- Modify: `.env.example`, `DEPLOYMENT.md` (stale key guidance), `docs/DECISIONS.md` (append D36)

**Interfaces:**
- Consumes: Task 2's contract — the served file must assign `window.__ACRA_CONFIG__ = { apiKey: "<key>" }`.
- Produces: `/config.js` served `Cache-Control: no-store` with the rendered key; web container env `API_KEY`.

- [ ] **Step 1: Write the failing contract test.** Append to `backend/tests/test_compose_contract.py`:

```python


PROD_OVERLAY = REPO_ROOT / "docker-compose.prod.yml"


def test_web_service_receives_api_key_for_runtime_config():
    """The web container renders /config.js from API_KEY (D36).

    The published web image is public, so it is built with no key; the key
    reaches the browser only through /config.js, which Caddy renders from the
    web container's own environment. If the overlay stops passing API_KEY to
    `web`, /config.js serves an empty key and every protected call 401s --
    the failure this design exists to remove. `web` is a production-only
    service, so unlike the api contract above this one lives in the overlay.
    """
    block = _service_block(PROD_OVERLAY.read_text(encoding="utf-8"), "web")

    match = re.search(r"^\s+API_KEY:\s*(.+)$", block, re.M)
    assert match, (
        f"the web service in {PROD_OVERLAY.name} does not receive API_KEY, so "
        f"/config.js would render an empty key and every scan would 401"
    )
    assert match.group(1).strip() == "${API_KEY:-}", (
        f"web's API_KEY must be the same ${{API_KEY:-}} passthrough the api "
        f"service uses, so one .env line feeds both; found {match.group(1).strip()!r}"
    )
```

- [ ] **Step 2: Run it to verify it fails.**

Run (repo root): `./venv/Scripts/python.exe -m pytest backend/tests/test_compose_contract.py`
Expected: **1 failed, 2 passed**; the failure says `does not receive API_KEY`.

- [ ] **Step 3: Pass the key to `web`.** In `docker-compose.prod.yml`, under `web:` → `environment:`, directly after the line `      SITE_ADDRESS: ${SITE_ADDRESS:-:80}`, insert:

```yaml
      # The key /config.js renders for the browser (D36) -- the SAME host
      # variable the api service checks, so one .env line feeds both and the
      # served key cannot drift from the checked one. The published web image
      # is public and built without a key; this is the only way one arrives.
      API_KEY: ${API_KEY:-}
```

- [ ] **Step 4: Run it to verify it passes.**

Run: `./venv/Scripts/python.exe -m pytest backend/tests/test_compose_contract.py`
Expected: **3 passed**.

- [ ] **Step 5: Create `config.js.template`** at the repository root:

```js
/*
 * Runtime configuration, rendered by Caddy on every request (D36).
 *
 * The published web image is public, so it is built with no API key. Caddy's
 * `templates` handler renders this file from the web container's API_KEY --
 * the same .env value the api container checks -- so the browser receives the
 * key at runtime instead. `| js` applies Go's JavaScript string escaping; CI
 * boots with a key containing a double quote, a backslash and '<' and checks
 * the rendered value byte-for-byte, so that escaping is gated, not assumed.
 *
 * Replaces frontend/public/config.js (the key-less local default) in the web
 * image's final stage, and never runs un-rendered anywhere.
 *
 * Readable by anyone who loads the page, exactly as the inlined key was
 * before: it raises the cost of drive-by abuse of /scan, it does not identify
 * callers. See frontend/src/lib/api.ts.
 */
window.__ACRA_CONFIG__ = { apiKey: "{{env "API_KEY" | js}}" };
```

- [ ] **Step 6: Serve it rendered.** In `Caddyfile`, insert this block (tab-indented, like the rest of the file) immediately **before** the comment line `	# The client-side routes, mirrored from frontend/src/lib/routes.ts.`:

```caddyfile
	# Runtime configuration (D36). The published web image is public and so
	# carries no API key; this renders config.js.template -- copied over
	# /srv/config.js by frontend/Dockerfile -- from the web container's own
	# API_KEY on each request, which is how the browser gets one. A plain path
	# matcher is exact, so it cannot swallow any other URL.
	#
	# `mime` is required: the templates handler renders only HTML and plain
	# text by default, and without it the placeholder is served literally.
	# no-store because the value is per-deployment state, not an asset:
	# rotating API_KEY must take effect on the next load, not after a cache
	# expiry.
	handle /config.js {
		root * /srv
		header Cache-Control "no-store"
		templates {
			mime text/javascript application/javascript
		}
		file_server
	}

```

Then run (from `frontend/`): `npx vitest run src/lib/routes.test.ts` — expected PASS (proves the `@spa` line is untouched).

- [ ] **Step 7: Web Dockerfile.** In `frontend/Dockerfile`, replace this block:

```dockerfile
# VITE_API_KEY is NOT a secret, despite the name, and this is the boundary
# where someone will be tempted to treat it as one. Anything inlined into a
# static bundle is readable by anyone who loads the page. It raises the cost
# of drive-by abuse of /scan; it does not identify or isolate callers. See
# frontend/src/lib/api.ts:8-14. Real per-user auth is roadmap item G.
#
# Deliberately left empty by default, and deliberately NOT passed by
# release.yml: a published image is a public artifact, so baking a key into
# one publishes the key.
ARG VITE_API_KEY=
ENV VITE_API_KEY=$VITE_API_KEY
```

(copied from a 2026-09-10 read of the file — if the replace does not match, read the file and match its exact text) with:

```dockerfile
# VITE_API_KEY is deliberately NOT a build argument (D36). A published image
# is a public artifact, so a key baked into one is a published key. The
# browser gets the key at runtime from /config.js instead, which Caddy renders
# from the web container's API_KEY. api.ts still reads VITE_API_KEY as a
# local-development fallback, where no image is involved.
```

and in the final stage replace

```dockerfile
COPY --from=build /app/dist /srv
COPY Caddyfile /etc/caddy/Caddyfile
```

with

```dockerfile
COPY --from=build /app/dist /srv
# The bundle's key-less public/config.js is replaced by the template Caddy
# renders per request (Caddyfile, handle /config.js). Copied AFTER the bundle
# so it wins.
COPY config.js.template /srv/config.js
COPY Caddyfile /etc/caddy/Caddyfile
```

Verify: `grep -c 'VITE_API_KEY' frontend/Dockerfile` equals the number of comment mentions only (no `ARG`/`ENV` line): `grep -n -E '^(ARG|ENV) VITE_API_KEY' frontend/Dockerfile` prints nothing.

- [ ] **Step 8: Correct the stale guidance.**

In `.env.example`, replace

```
# MUST be set for any deployment reachable from the internet: /scan runs
# `git clone` on request, which is expensive and abusable.
# API_KEY=
```

with

```
# MUST be set for any deployment reachable from the internet: /scan runs
# `git clone` on request, which is expensive and abusable.
# The production overlay also passes it to the web container, which serves it
# to the browser at runtime through /config.js (D36) -- set it once here and
# both containers read it. Generate one with: openssl rand -hex 32
# API_KEY=
```

and replace

```
# Must match API_KEY above. WARNING: anything inlined into a static bundle is
# readable by anyone who loads the page — this raises the cost of drive-by
# abuse, it is not a per-user secret.
# VITE_API_KEY=
```

with

```
# LOCAL DEVELOPMENT ONLY. A deployed web container serves the key at runtime
# from API_KEY (/config.js, D36) and published images are built without this.
# Here it must match your local backend's API_KEY. WARNING: anything a browser
# receives is readable by anyone who loads the page — this raises the cost of
# drive-by abuse, it is not a per-user secret.
# VITE_API_KEY=
```

In `DEPLOYMENT.md`:
- In the "First deploy" table, append to the `API_KEY` row's text: ` It reaches the API and, through `/config.js`, the browser — nothing is rebuilt.`
- In the verification checklist, after the `/api/health` item, add: `- [ ] `curl -fsS https://<host>/config.js` contains your key, and a scan started from the UI does not fail with "Unauthorized".`
- Replace the security-posture item that begins `3. **Rebuild the frontend** with `VITE_API_BASE` and `VITE_API_KEY` set` (two lines) with:

```
3. **Nothing to rebuild for the key.** The web container serves `API_KEY` to
   the browser at runtime through `/config.js` (DECISIONS.md D36), so the
   published image works unchanged and rotating the key is a restart. Only a
   split-origin deployment still needs `VITE_API_BASE` at build time.
```

- In the environment-variable table, append to the `API_KEY` row's purpose cell: ` The `web` container also receives it and serves it to the browser via `/config.js` (D36).`

- [ ] **Step 9: Record D36.** Append to `docs/DECISIONS.md`:

```markdown

## D36 — The browser gets the API key at runtime, not at build time

**Date:** 2026-09-10 · **Decided by:** Pranav, choosing among options proposed by Claude Opus 5 (session `655d7eab`)

Phase F's runbook made `API_KEY` mandatory on a public host and pulled the
published web image — which `release.yml` deliberately builds without
`VITE_API_KEY`, because a published image is public and a key baked into one
is a published key. Each half was right; together they meant every protected
call from the UI would 401. `deploy-stack` booted with `API_KEY` unset, so CI
never saw it.

**Chosen: `/config.js`, rendered by Caddy's `templates` handler from the web
container's own `API_KEY`** (the same `.env` line the api reads), loaded before
the bundle and read by `api.ts` at call time, with `VITE_API_KEY` kept as a
local-development fallback. The published image stays generic, rollback by sha
tag keeps working for both tiers, and rotating the key is a restart.

**Rejected:**
- *Build the web image on the host* — no code change, but the web tier's
  rollback becomes a rebuild at an old sha instead of a tag pull, which is what
  Phase M's rollback criterion exists to avoid.
- *A private image with the key baked in* — every historical sha tag would
  carry whichever key was current, so rotation could never un-publish an old
  one.
- *Caddy injects `X-API-Key` on proxied requests* — the edge would let anyone
  through, including a plain `curl`: auth switched off in all but name
  (CONSTRAINTS #5).

**What this does not change:** the key is readable by anyone who loads the
page, exactly as the inlined value was. It raises the cost of drive-by abuse of
`/scan`; it does not identify callers. Real auth remains roadmap item G.
```

- [ ] **Step 10: Full backend verification.** Run (repo root): `./venv/Scripts/python.exe -m pytest backend/tests`
Expected: **536 passed, 7 skipped** (535 + 1). Read the summary line.

- [ ] **Step 11: Commit.**

```bash
git add config.js.template Caddyfile frontend/Dockerfile docker-compose.prod.yml backend/tests/test_compose_contract.py .env.example DEPLOYMENT.md docs/DECISIONS.md
git commit -F <scratch>/msg-task3.txt
```
Message: subject `Serve the API key at runtime from the web container`; body: the published web image carries no key, so on a host with API_KEY set every scan would 401; Caddy now renders /config.js from the web container's API_KEY (the same .env line the api reads), no-store; the Dockerfile no longer accepts VITE_API_KEY; docs and D36 updated.

- [ ] **Step 12: ASK Pranav, then push and watch it go green.** `git push`, then watch the new run.
Expected: `Deploy stack` **green**, including every line of `Auth and runtime config through the proxy` — the first observation that the documented path works with auth on. If it fails, read which assertion: `/config.js` served literally means the `mime` did not match the served Content-Type (inspect `/tmp/config-headers.txt` in the log); a mismatch naming escaped characters means `| js` escaping differs from what the checker expects — adjudicate against the rendered bytes, never by weakening the CI key.

---

### Task 4: Build and boot the stack natively on arm64 in CI

**Files:**
- Modify: `.github/workflows/ci.yml` (`deploy-stack` header; both build steps' cache lines)

**Interfaces:**
- Consumes: Task 1's job-level `env`.
- Produces: build cache scopes named exactly `api-X64`, `api-ARM64`, `web-X64`, `web-ARM64` (`runner.arch` values). Task 5's `release.yml` reads these names — they must match character for character.

- [ ] **Step 1: Matrix the job.** Replace

```yaml
  deploy-stack:
    name: Deploy stack (build images + boot)
    runs-on: ubuntu-latest
    timeout-minutes: 25
```

with

```yaml
  deploy-stack:
    name: Deploy stack (${{ matrix.runner }})
    runs-on: ${{ matrix.runner }}
    timeout-minutes: 25
    strategy:
      # Both architectures report even when one fails: an arm64-only break is
      # exactly the signal this matrix exists to produce, and fail-fast would
      # cancel it as collateral.
      fail-fast: false
      matrix:
        # ubuntu-24.04-arm because the free Oracle A1 host is ARM (Phase M).
        # The arm64 stack is built AND booted here, natively, before anything
        # merges -- otherwise the VM would be the first place it ever ran.
        runner: [ubuntu-latest, ubuntu-24.04-arm]
```

(No branch protection or ruleset exists on `main` — checked 2026-09-10 — so the renamed check cannot block a merge.)

- [ ] **Step 2: Scope the caches per architecture.** In the `Build the API image` step replace

```yaml
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

with

```yaml
          # One scope per architecture, and named so release.yml can read the
          # native layers these builds produce for the same commit.
          cache-from: type=gha,scope=api-${{ runner.arch }}
          cache-to: type=gha,mode=max,scope=api-${{ runner.arch }}
```

and in the `Build the web image` step replace the same two lines with:

```yaml
          cache-from: type=gha,scope=web-${{ runner.arch }}
          cache-to: type=gha,mode=max,scope=web-${{ runner.arch }}
```

- [ ] **Step 3: Parse check.** Same command as Task 1 Step 3. Expected: `ci.yml parses`.

- [ ] **Step 4: Commit.**

```bash
git add .github/workflows/ci.yml
git commit -F <scratch>/msg-task4.txt
```
Message: subject `Build and boot the deploy stack on arm64 as well`; body: the free host is ARM; deploy-stack now runs on ubuntu-latest and ubuntu-24.04-arm with fail-fast off and per-architecture cache scopes, so the arm64 stack is proven before merge.

- [ ] **Step 5: ASK Pranav, then push and watch both legs.**
Expected: `Deploy stack (ubuntu-latest)` and `Deploy stack (ubuntu-24.04-arm)` both green. If the arm leg sits **queued** for more than ~10 minutes with no runner, arm64 runners are not available to this repository — stop and ask Pranav (fallback: drop the matrix and prove arm64 via Task 5's QEMU build plus the VM). Record both run IDs.

---

### Task 5: Publish `linux/amd64` + `linux/arm64` under one tag

**Files:**
- Modify: `frontend/Dockerfile` (build-stage `FROM`)
- Modify: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: Task 4's cache scope names `api-X64`, `api-ARM64`, `web-X64`, `web-ARM64`.
- Produces: GHCR tags `<sha>` and `latest` for both images, each an index containing `linux/amd64` and `linux/arm64`.

- [ ] **Step 1: Keep npm/Vite native.** In `frontend/Dockerfile` replace the line `FROM node:20-alpine AS build` with:

```dockerfile
# $BUILDPLATFORM, not the target: the bundle is plain JavaScript and identical
# on every architecture, so npm and Vite run natively on the build machine even
# while release.yml produces the arm64 image under QEMU. Only the caddy stage
# below is built per platform.
FROM --platform=$BUILDPLATFORM node:20-alpine AS build
```

- [ ] **Step 2: QEMU.** In `release.yml`, immediately before `      - uses: docker/setup-buildx-action@v3`, insert:

```yaml
      - name: Set up QEMU
        # arm64 images for the free Oracle A1 host (Phase M). Emulation is paid
        # only where the cache misses: cache-from below reads the NATIVE arm64
        # layers ci.yml's deploy-stack matrix built for this same commit, and
        # the web image's npm/Vite stage runs on the build platform regardless.
        uses: docker/setup-qemu-action@v3
```

- [ ] **Step 3: API image — platforms and caches.** In the `Build and push` step replace

```yaml
          # The torch/transformers layer is multi-GB and identical across
          # code-only changes; without a cache every push re-downloads it.
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

with

```yaml
          platforms: linux/amd64,linux/arm64
          # Read the native per-architecture caches ci.yml's deploy-stack wrote
          # for this commit (CI always completes before this workflow runs).
          # No cache-to: CI owns those scopes, and a third copy here would only
          # push them out of the repository's cache quota sooner.
          cache-from: |
            type=gha,scope=api-X64
            type=gha,scope=api-ARM64
```

- [ ] **Step 4: Web image — platforms, caches, and the stale key comment.** In the `Build and push the web image` step, replace the comment lines

```yaml
          # VITE_API_KEY is deliberately absent. Vite inlines these values into
          # the bundle and a published image is a public artifact, so passing a
          # key here would publish the key. A deployment that wants one builds
          # the web image on the host; DEPLOYMENT.md says how.
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

with

```yaml
          # VITE_API_KEY is deliberately absent, and frontend/Dockerfile no
          # longer accepts it: a published image is public, so a key baked in
          # would be a published key. The browser gets the key at runtime from
          # /config.js, rendered from the host's API_KEY (D36).
          platforms: linux/amd64,linux/arm64
          cache-from: |
            type=gha,scope=web-X64
            type=gha,scope=web-ARM64
```

- [ ] **Step 5: Parse check.** Run: `./venv/Scripts/python.exe -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml', encoding='utf-8')); print('release.yml parses')"` — expected `release.yml parses`. Do **not** dispatch the workflow (Global Constraints).

- [ ] **Step 6: Commit.**

```bash
git add frontend/Dockerfile .github/workflows/release.yml
git commit -F <scratch>/msg-task5.txt
```
Message: subject `Publish both images for amd64 and arm64`; body: QEMU plus platforms on both release builds; the web build stage runs on the build platform; release reads the native per-architecture CI caches; verified only after merge because dispatching release on a branch would move latest.

- [ ] **Step 7: ASK Pranav, then push.** Expected: CI green on both legs (the Dockerfile's `--platform=$BUILDPLATFORM` is exercised by both native builds). `release.yml` itself is verified in Task 7.

---

### Task 6: The free-deploy runbook

Docs only; no review seat needed.

**Files:**
- Modify: `DEPLOYMENT.md` — new section `## Free deploy — Oracle Always Free A1 + DuckDNS`, placed directly after the `### SEO surface` subsection of `## Deploy to a VPS (Phase F)`.

- [ ] **Step 1: Write the section** with these subsections, in this order. Every provider-UI detail is marked "check the console — labels change" rather than asserted; every number comes from the spec's verified sources.

1. **What it costs and what it assumes** — $0; a card is required at signup (secondary sources report a $1 authorisation that is not charged; Oracle's Always Free page does not say); the home region is chosen once and is permanent; A1 capacity varies by region and "out of capacity" is common.
2. **Create the instance** — Ubuntu 24.04 (aarch64) image; shape `VM.Standard.A1.Flex` within the Always Free allowance of **2 OCPUs / 12 GB total** (Oracle's page, 2026-09-10); boot volume within the **200 GB** total; assign a public IPv4; add your SSH key.
3. **Open 80 and 443 — in two places.** (a) The VCN security list: ingress TCP 80 and 443 from `0.0.0.0/0`. (b) The host firewall: run `sudo iptables -L INPUT -n --line-numbers`; if a `REJECT` rule is present, insert ACCEPT rules for TCP 80 and 443 **above** it (`sudo iptables -I INPUT <line> -p tcp --dport 80 -m state --state NEW -j ACCEPT`, same for 443), then persist with `sudo netfilter-persistent save`. Both are required; either alone leaves Caddy's certificate challenge unreachable.
4. **Install Docker** — `curl -fsSL https://get.docker.com | sh`, `sudo usermod -aG docker $USER`, log out and in; `docker compose version` must be **v2.24 or newer** (the overlay uses `!reset`).
5. **Point a DuckDNS name at it** — create `<name>.duckdns.org`, set its IP to the instance's public IPv4, and confirm with `dig +short <name>.duckdns.org` **before** starting the stack; Caddy's first certificate attempt against a wrong IP spends Let's Encrypt rate limit. (`duckdns.org` is on the Public Suffix List, so each subdomain has its own rate-limit bucket.)
6. **Configure** — `git clone`, `cp .env.example .env`, then set `API_KEY=$(openssl rand -hex 32)`, `SITE_ADDRESS=<name>.duckdns.org`, `BACKUP_HOST_DIR=/var/lib/acra-backups` (create it), and **`IMAGE_TAG=<sha>`** — pin a sha rather than `latest`, so the running version is always known and a rollback is a one-line change. Only shas published after Phase M carry `linux/arm64`; `docker manifest inspect ghcr.io/pranav-1201/ai-code-review-agent:<sha>` lists the platforms.
7. **Start and verify** — `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`, then run the existing verification checklist above, including the `/config.js` item.
8. **Optional: canonical URLs** — set the repository variable `SITE_URL` to `https://<name>.duckdns.org`; the next published web image bakes it into `sitemap.xml`, canonical links and `og:url`.
9. **Drill the rollback** — note the running sha; set `IMAGE_TAG` to an **older multi-arch** sha; `pull` and `up -d`; confirm `/api/health` is 200 with `"auth": "enabled"`; then return to the newer sha the same way. An amd64-only sha fails to pull on this host with "no matching manifest for linux/arm64" — that is the expected error, not a broken deploy.
10. **Known risk: idle reclamation** — Oracle may reclaim an Always Free instance that, over 7 days, has CPU p95 < 20%, network < 20%, and (A1 only) memory < 20%. A low-traffic demo can meet all three. This runbook does not engineer around it; watch utilisation for the first week (`free -m`, `top`) and keep the backup directory somewhere you can copy off the host.

- [ ] **Step 2: Check it.** `grep -n 'Free deploy' DEPLOYMENT.md` shows the heading once; every command in the section is copied from this plan, not retyped.

- [ ] **Step 3: Commit.**

```bash
git add DEPLOYMENT.md
git commit -F <scratch>/msg-task6.txt
```
Message: subject `Document the free deploy on Oracle A1 with DuckDNS`; body: the step-by-step path to a $0 host, including both firewall layers, pinning IMAGE_TAG, the rollback drill and the idle-reclamation risk.

---

### Task 7: Merge, publish, and prove the images are multi-arch

- [ ] **Step 1: Fresh full verification.** From the repo root `./venv/Scripts/python.exe -m pytest backend/tests` — expected **536 passed, 7 skipped**. From `frontend/`: `npm test` (**21 files, 182 tests**), `npm run typecheck`, `npm run build`. And CI green on the branch head for both matrix legs — record the run ID.

- [ ] **Step 2: Review.** Run `/code-review` (or `ecc:code-review`) on `main...HEAD`. Instruct the reviewer to **challenge** D36 and the escaping argument by name, not to ratify them. Adjudicate every finding against the code before acting.

- [ ] **Step 3: ASK Pranav, then merge and push.** `git switch main && git pull --ff-only && git merge --no-ff phase-m/runtime-config-arm64 -F <scratch>/msg-merge.txt && git push`. Merge subject: `Merge Phase M: serve the API key at runtime and publish arm64 images`.

- [ ] **Step 4: Watch CI then release on `main`.** `gh run list --branch main --limit 4`. Expected: CI green (both `deploy-stack` legs), then `Publish image` green.

- [ ] **Step 5: Prove both images carry both platforms** for the merge sha:

```bash
sha=$(git rev-parse HEAD)
for img in ai-code-review-agent ai-code-review-agent-web; do
  tok=$(curl -s "https://ghcr.io/token?scope=repository:pranav-1201/$img:pull" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
  echo "== $img @ $sha"
  curl -s -H "Authorization: Bearer $tok" -H "Accept: application/vnd.oci.image.index.v1+json" \
    "https://ghcr.io/v2/pranav-1201/$img/manifests/$sha" | grep -o '"architecture": *"[a-z0-9]*"'
done
```
Expected, for each image: `amd64`, `arm64`, and `unknown` entries (the last are buildx attestations). An image missing `arm64` fails the task.

- [ ] **Step 6: Handover.** Update `docs/HANDOVER.md` sections 1 and 3 (Phase M: code merged at `<merge sha>`, images multi-arch, live deploy pending), commit by explicit path, **ASK**, push. This commit's own release is the **second multi-arch sha** the rollback drill needs — confirm it with Step 5's command.

- [ ] **Step 7: Delete the branch** local and remote after Pranav confirms.

---

### Task 8: Live deploy and the drilled rollback

Pranav drives the Oracle and DuckDNS consoles; the executor verifies from here.

- [ ] **Step 1:** Pranav follows the Task 6 runbook with `IMAGE_TAG` = the Task 7 Step 6 sha.
- [ ] **Step 2: Acceptance.** `curl -fsS https://<name>.duckdns.org/api/health` returns 200 containing `"auth": "enabled"`; `curl -fsS https://<name>.duckdns.org/config.js` contains the key; `curl -s -o /dev/null -w '%{http_code}' http://<name>.duckdns.org:8000/health` fails to connect.
- [ ] **Step 3: A real scan through the real UI** against a small public repository — the bar every phase has had since J3; the suite is not the acceptance criterion.
- [ ] **Step 4: Rollback drill.** `IMAGE_TAG=<merge sha>`, `pull`, `up -d`; Step 2's `/api/health` check returns 200 again; then back to the newer sha. Record both shas and timestamps.
- [ ] **Step 5: Close out.** Update `docs/HANDOVER.md` (Phase M **done**, host, both shas, evidence) and the memory index; commit by explicit path; **ASK**; push.
