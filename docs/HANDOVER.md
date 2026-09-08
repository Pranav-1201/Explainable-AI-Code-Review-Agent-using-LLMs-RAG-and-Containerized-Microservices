# HANDOVER — read this first, every session

**Purpose:** this file is the entry point. If you have been told something as
short as *"finish my project"*, this file is the whole brief. Read it, then
`docs/CONSTRAINTS.md`, then start at the next unfinished phase below.

**Last updated:** 2026-09-08 · **Updated by:** Claude Opus 5 session
`9b583d0e` · **Branch at handover:** `s10/redis-rate-limiting` — **pushed**,
**CI GREEN** (run `34263975530`, all 3 jobs), **3 commits, NOT MERGED**.
`main` is unchanged at `5fa6b6e`.
**S10 is complete** — section 1d. B1 is complete (1c); phases L and K are
complete and F1 is complete (1a–1b). **The unassigned backlog is now empty.**
The only thing left in the roadmap is **M — deploy**.

> **Read this before touching CI on a new branch.** `s10/redis-rate-limiting`
> got **no CI run at all** when it was pushed, silently. `ci.yml` triggers on
> `branches: ["main", "phase-*/**", "prelaunch/**"]` and nothing else, so a
> branch outside those families pushes successfully and runs nothing. The
> workflow's own comment warns about this and it still cost a step. It was run
> via `gh workflow run CI --ref <branch>` (`workflow_dispatch` is enabled).
> Either dispatch manually, open a PR to `main`, or name the branch to match a
> family — but do not read a clean `git push` as a green build.

> **J1 is COMPLETE, reviewed and MERGED into `main` at `37f0060`.** Branch
> deleted. `main` = `origin/main` = `e857e0e`, and **CI is green on it** (run
> `32357489001`). An earlier version of this file claimed `main` was 16 commits
> ahead of `origin/main` with CI never run — both halves were false by the time
> anyone read them, and the correction cost a session's first ten minutes.
>
> **J2 is COMPLETE and MERGED into `main` at `568bf4e`** (PR #3, 10 commits,
> branch deleted). All 6 tasks landed, each individually review-clean; the final
> whole-branch review (Opus) returned **SHIP WITH FIXES**, and the two Important
> findings plus three minors were closed in one fix wave at `e88f255`. CI is
> green on the merge commit (run `32699574335`), and `main` = `origin/main`.
>
> **Verified in that session, every command run fresh:** pytest **432 passed /
> 0 failed**, vitest **70 passed / 11 files**, `tsc -b` exit 0, `npm run build`
> succeeds, Playwright **23 passed**. Plus the acceptance row that actually
> matters: sweeping every first-party `.py` under `backend/` through the shipped
> analyzer gave **31 findings across 14 files, 31 carrying real source, 0
> placeholders**.
>
> The full task-by-task record — every ruling, every deferred minor, and three
> reviewer seats that died without reporting — is in
> `docs/superpowers/records/2026-08-24-j2-execution-record.md`.
>
> **Note for future phases:** that record lives under `docs/` on purpose.
> Execution ledgers are written to `.superpowers/sdd/<plan>/progress.md`, which
> `.superpowers/sdd/.gitignore` ignores with a bare `*` — so they never enter
> git and vanish on a clean. J1's handover pointed at its ledger and the file
> was already gone by the next session. Copy the ledger into
> `docs/superpowers/records/` before deleting the workspace.
>
> **J3 is DONE and merged** at `2004639`. Its execution record — six defects
> the reviews found in J3's own plan, and the ruling on each — is at
> `docs/superpowers/records/2026-08-25-j3-execution-record.md`. Phase J is
> finished.
>
> **J3's last open bar is now CLOSED (2026-08-26).** It read: *nobody has run a
> real scan and opened the File Analysis page; every J3 number came from
> fixtures and demo data, so the change-record line numbers are unproven
> against a real clone.* Done this session, end to end: a real clone of
> `pypa/sampleproject` through the running backend, then the real page driven
> in a real browser. All **11 change records across 4 files land on the exact
> right lines**, including `noxfile.py` with 6 insertions where an off-by-one
> would accumulate — hand-checked against `improved_code` line by line. The
> page reported *"21 changed lines highlighted"*, which is 3x6 docstring +
> 3x1 return-hint, and the highlight band sat on improved lines 24–30 exactly.
> `ast.parse` accepted all 10 code payloads (5 files x original/improved).
> Zero console errors. **There is no longer an unproven J3 claim.**
>
> **Phase L is DONE (2026-09-04)** — S8, S9, F11, F12, F13, F14, H1, H2.
> **Phase K is DONE** — B6, F10. **F1 is DONE** — light mode, closing Phase I.
> **Backlog B2, B3, B4, B5 and F3 are DONE.** All unpushed. Sections 1a, 1b.
>
> **B1 is DONE (2026-09-05)** — section 1c. **S10 is DONE (2026-09-08)** —
> section 1d. **What is left: Phase M** (deploy). Nothing else is open.

---

## 1a. Phase L — done 2026-09-04, unpushed

All eight Phase L IDs are closed. Design at
`docs/superpowers/specs/2026-09-04-phase-l-design.md`, plan at
`docs/superpowers/plans/2026-09-04-phase-l.md`.

**The finding that shaped the whole phase:** S8 was a *wiring* defect, not a
detector defect. The analyzer found 30 dead imports and 462 dead functions in
this repository and the report layer discarded every one. But the 462 broke
down as 374 in `backend/tests` (372 named `test_*`), 53 `backend/validation`,
25 `backend/benchmark`, 7 `backend/app`, 2 `backend/database`, 1 `rag/data` —
only 10 production, and one of those 10 was a false positive. Wiring naively
would have injected ~452 known-noise findings, which is exactly the
false-positive generator `CONSTRAINTS.md` section 21 forbids. **That made S9 a
prerequisite for S8, not hygiene beside it.**

| ID | Commit | What landed |
|---|---|---|
| S9 | `4ebeede` | New fine role `fixture`, assigned by path, mapping to coarse `test`. Fixture files emit no findings. Every finding now carries the `file_type` of its file. |
| S8a | `e30da45` | Two FP exemptions in `call_graph.py`: pytest's `test_*` naming, and methods overriding a base class not defined in the scanned sources. |
| S8b | `910d6fd` | `dead_import` / `dead_function` wired into `formatted_issues`, with lines and snippets resolved at the report layer. Cache bumped to v3.7. |
| F12 | `a2e1a8a` | Deleted the unrouted `Index.tsx` placeholder. |
| F13 | `97a3e61` | A language under 1% renders `<1%`, not `0%`. |
| H1 | `adf52d7` | Untracked `390.52`, deleted the junk, added named `.gitignore` entries. |
| F11 | `e9129be` | `Visualizations` route chunk **432.04 kB → 4.43 kB** via `React.lazy` on the chart panel. |
| F14 | `e6503ee` | Health Score page states the composite's weights; fourth dimension renamed Performance → Simplicity. |

### Measured acceptance, this session, on a real scan of this repository

| | Before | After |
|---|---|---|
| `dead_import` records in the report | 0 | **28** |
| `dead_function` records in the report | 0 | **62** (naive wiring: 462) |
| Records sourced from `benchmark/corpus/fixtures/` | 35 | **0** |
| `security` records | 55 | 14 |
| `complex_function` records | 27 | 25 |
| Dead-code findings carrying `line: 0` | — | **0** |
| `observability.py::format` reported dead | yes | **no** |

The security and complex_function drops are exactly the 33 + 2 fixture records
S9 removed. **pytest 468 passed**, benchmark **GATE PASSED** with
`dead_function` and `dead_import` both 1.00/1.00, **vitest 106 passed**,
`tsc -b` exit 0, `npm run build` succeeds, working tree clean.

### Two defects found along the way that were not in the plan

1. **The analysis cache ignored the file's role.** The key was
   `(version, content, imports)`, so two files with identical content and
   different roles collided and whichever was analysed first won. Already wrong
   — the role drives `is_test`, which drives the security pass — and it made
   S9's suppression unsound, because a fixture whose content matched a
   production file would have been served the production result and leaked its
   planted findings. Fixed in `910d6fd` by folding the role into the version
   string.
2. **`F11`'s audit criterion measures the wrong thing.** `Visualizations` was
   *already* a lazy route chunk, so a `manualChunks` vendor split would have
   satisfied the literal wording ("Visualizations chunk < 250 kB") while making
   the page no faster to open. Deferring recharts *within* the page is what
   shortens time to first paint. Recorded in `DECISIONS.md`.

### Still not done for Phase L

**Nothing is outstanding, but nothing has been driven through the real UI.**
Per section 3 the bar for shipping is a real clone through the running app, and
that has not been done for these changes. The 28/62/0 numbers above come from
the shipped analyzer and engine invoked directly, which is stronger than the
suite but weaker than the app.

---

## 1. Where things stand right now

The project is an AI code review agent: you give it a public repository URL, it
clones it, runs a deterministic AST analysis, and returns a health report.

**Status: strong MVP. Phases G, H, I-partial, J1 and J2 done; F2 (the last open
defect) fixed; not yet deployed.** Graded
**6.5/10** in `docs/STAFF_AUDIT_2026-08-19.md`, before either. The engineering
around the product was already good (security 8, tests 8, docs 8); the
analyzer's precision was the failing grade (correctness 4), and G fixed the
detectors while H fixed the dependency reporting.

The fact that drove that grade:

> On `pallets/flask`, **all five** security findings the tool produced were
> false positives. Measured, not estimated — `docs/ANALYZER_ACCURACY_2026-08.md`.

**That is now fixed and re-measured.** flask produces **zero** security
findings; the 623 findings that remain are all `dead_function`, `dead_import`
and `high_complexity`. An unrelated local RL project went from 3 security
findings to 1, and the survivor is correct (its `argv[0]` is `str(PYTHON)`, so
the program being launched is not statically knowable).

`ANALYZER_ACCURACY_2026-08.md` still describes the PRE-Phase-G behaviour. It is
a dated study, not a live status page — read it as history and read this
section for current state.

### Git state at handover

| | |
|---|---|
| Current branch | `main` — B1 merged at `fb29705` (`--no-ff`), pushed 2026-09-05 |
| Pushed? | **YES** — level with `origin/main`. |
| CI | **GREEN on the B1 merge commit `fb29705`**, run `33968513343`, all 3 jobs: backend + detector gate, frontend typecheck/build, and deploy-stack (builds both images and boots the compose stack). |
| `phase-b/b1-decompose-report-functions` | merged at `fb29705` (`--no-ff`), CI green on the branch head too (run `33954546383`), branch deleted local and remote |
| CI | **GREEN on `260ead9`**, run `33836451073`, all 3 jobs. |
| CI | **GREEN on `6ca9ae9`**, run `32900870668`, all 3 jobs: backend + detector gate, frontend typecheck/build, and deploy-stack (builds both images and boots the compose stack). Playwright 26 passed. `6ca9ae9` is **the last commit on this branch carrying code** — anything after it is documentation only. |
| `fix/broken-clone-cache` | merged at `33429f8` (`--no-ff`), branch deleted |
| Careful | the CI run for the merge commit `33429f8` shows **cancelled**, not failed — the docs push superseded it via the concurrency group. `6ca9ae9` is the run that matters and it contains all the code. |
| `phase-j/j3-code-panes` | merged (`2004639`), CI green (run `32855931620`, all jobs), branch deleted |
| Working tree | Clean |
| `fix/dev-launcher-cors` | merged and deleted |
| `phase-j/j1-explanation-parity` | merged (`37f0060`) and deleted |
| `phase-j/j2-finding-detail` | merged (`568bf4e`) and deleted, remote ref pruned |

---

## 1b. Phase K, F1 and the backlog — done 2026-09-04, unpushed

| ID | Commit | What landed |
|---|---|---|
| B6 | `664e9a0` | A repository with nothing analysable is rejected with a readable message naming what it contains and what this tool reads. |
| F10 | `27d0a22` | The scanner states the six supported languages before the clone. A backend test parses the frontend list and fails on drift. |
| F1 | `09847ab` | Light mode. `:root` is light, `.dark` is dark, three-state toggle, pre-paint script, and a WCAG audit of both palettes. |
| B4 | `2bdeccf` | `most_reused_module` reports first-party code instead of `os`. |
| B3+F3 | `2f8a077` | The file table sorts stably and by name; the complexity ranking says it counts production files only. |
| B2 | `f7e87cf` | Cyclomatic complexity at exact parity with radon. |
| B5 | `590c980` | The duplicate-similarity floor calibrated against measured data. |

### What the measurements actually showed

**B6 was worse than the audit described.** A MATLAB-only repository did not
error. It completed as a *successful* scan reporting **health_score 45** on
zero files analysed — the composite defaults security and simplicity to 100
when there is nothing to measure. The tool was telling users their
unanalysable repository scores 45/100. A confident wrong answer, not a
generic error.

**B2 disagreed with radon in both directions, and both were defects.**
Measured with radon 6 in a throwaway venv:

| Function | Ours before | radon | Gap |
|---|---|---|---|
| `review_repository` | 33 | 58 | **+25** |
| `analyze_dependencies` | 75 | 68 | **-7** |

The +25 is 14 comprehension generators + 6 comprehension filters + 5
ternaries, skipped because a comment justifying it for *nesting depth* was
being applied to *cyclomatic complexity* too. The -7 is exactly the seven
decision points of the nested `_add_dep`, whose branches were being charged
to its parent. Both fixed; both functions now match radon exactly.
**Cache is at v3.8** because complexity numbers moved.

**B5's premise was backwards.** The audit worried 30% was too low. Measured
across this repo (246 files) and the one other usable clone (43 files), the
highest-scoring pair of any two files was **19%** — so at a 30% floor the
block detector reported **nothing at all, ever**. The 19% pair is a real
copy-pasted harness (`phase4_validation.py` / `phase5_validation.py`, 29% of
the smaller file's unique lines). Floor now 15%; this repo reports 1 pair
where it reported 0. Calibration rests on two repositories and the constant
is named and documented so the next move needs evidence.

**F1's contrast audit found a shipped accessibility defect.** The new light
palette passes all 24 checks, and the audit caught the *dark* theme:
`--destructive-foreground` on `--destructive` was **3.91:1**, below WCAG AA
for text, on every destructive button in the app. White text alone was not
enough (4.38:1 at 55% lightness), so the red moved two points darker —
4.60:1 for the text while holding 3.87:1 against the card, which
`text-destructive` needs.

### Verified this session, every command run fresh

| Check | Result |
|---|---|
| `venv/Scripts/python.exe -m pytest backend/tests -q` | **500 passed** |
| `venv/Scripts/python.exe backend/benchmark/run_benchmark.py --gate` | **GATE PASSED**, no threshold lowered |
| `npm run typecheck` (`tsc -b`) | exit 0 |
| `npx vitest run` | **173 passed**, 20 files |
| `npm run build` | succeeds |
| `npx playwright test` | **26 passed** (desktop, tablet-768, mobile-375) |
| `git status --short` | clean |

### Pushed and CI-verified

All 21 commits are on `origin/main` at `260ead9`, and CI is **green on the
pushed head** — run `33836451073`, all three jobs including `deploy-stack`,
which builds both images and boots the compose stack. No commit carries an
AI-attribution trailer; authorship is Pranav's alone.

### What is genuinely left

| ID | Why it was not done |
|---|---|
| ~~**B1**~~ | **DONE 2026-09-05** — see section 1c. |
| **S10** | Rate limiting to Redis. Needs Redis running and is deploy-adjacent — it belongs with M, not before it. |
| **M** | Deploy. Outward-facing and not reversible by editing a file. Needs explicit go-ahead. |

**And the standing caveat: none of this session's work has been driven
through the real UI.** The numbers above come from the shipped analyzer and
engine invoked directly, plus 26 Playwright tests against the real rendered
app. That is stronger than a unit suite and still weaker than a real clone
driven by hand — which is the bar section 3 sets.

---

## 1c. B1 — done 2026-09-05

Both functions decomposed. Plan at
`docs/superpowers/plans/2026-09-05-b1-decompose.md`.

| | Before | After |
|---|---|---|
| `analyze_dependencies` | 385 lines, **CC 68** | **31 lines, CC 4** |
| `review_repository` | 398 lines, **CC 58** | **111 lines, CC 7** |

| Commit | What landed |
|---|---|
| `f3f97f4` | Characterization tests for both functions (17), watched failing against deliberately broken copies before being kept. |
| `e394dfe` | Six manifest parsers + `_DependencyCollector` out of `analyze_dependencies`. |
| `f4e26a6` | Lockfile resolution and version enrichment out of `analyze_dependencies`. |
| `0374275` | The per-file report pipeline out of `review_repository`. |
| `9a8394a` | Score averaging and the health composite. |
| `d9c6e76` | Issue grouping, centrality, warnings, insights, duplicates, architecture. |

### How "no behaviour change" was actually proven

Not by the suite. A harness runs both functions over a **frozen `git archive`
export of `ff69de2`** — 247 files — with the two network calls stubbed, and
dumps canonical JSON. Every extraction commit was gated on `cmp` returning
clean over **5,429,674 bytes**. All six do.

**The harness was wrong at first and the gate caught it.** It originally
scanned the live working tree, so the moment this branch added a test file
`file_reports` went 247 → 248 and the comparison failed for a reason that had
nothing to do with the refactor. A gate whose target moves while the code
moves proves nothing. It now scans a fixed export.

Two runs of the harness against unchanged code were confirmed byte-identical
before any of this was trusted.

**That gate alone was not enough, and the gap was found by asking what it
actually covered.** This repository has a `requirements.txt` and a
`package.json` and none of the other four manifests — so `pyproject.toml`,
`Pipfile`, `setup.py` and `setup.cfg` were extracted without any byte-level
check at all, covered only by shape assertions. Closed with a second gate that
imports ff69de2's `dependency_analyzer` from git alongside the current one and
runs both over a synthetic repository carrying all six manifests plus both
lockfiles: **27 packages, identical including order.** A count is only a
measurement of the thing you scoped it to.

The byte gate serialises with `sort_keys`, so it cannot see a key **order**
change. Insertion order for `repository_summary` (15 keys) and the top-level
report (10 keys) was therefore checked directly and is unchanged.

### Two defects found while writing the characterization tests

Neither is fixed here — B1 is gated on byte-identical output and closing
either would change the bytes.

1. **The two file-report builders have drifted.** A non-code row ships without
   `patch`, `refactor_changes` or `time_complexity`. Pinned exactly as
   `KNOWN_NON_CODE_KEY_GAP` in `backend/tests/test_b1_contract.py`, so *new*
   drift fails while the existing gap stays documented.

   **This is latent, not a live defect, and the first version of this note
   said otherwise.** Checking the consumer rather than assuming one settled
   it: `response-mapper.ts` defends all three. `complexity` resolves through
   `f.complexity || f.time_complexity || "O(1)"` and a non-code row carries
   `complexity: "N/A"`, which is truthy, so the missing field is never
   reached; `patch` falls back to `null`; `normalizeRefactorChanges(undefined)`
   returns `[]`. Nothing renders wrong today.

   So it is **deliberately not fixed**. Adding the three keys would change the
   report bytes for no user-visible gain. The test is the deliverable: the day
   a consumer stops defending itself, or a fourth key drifts, it fails.
2. **An empty repository scores health 45, not 0.** Quality and documentation
   are 0, but security and simplicity have nothing to subtract from and default
   to 100. This is the B6 finding seen at the layer that produces it; B6
   rejects unanalysable repositories upstream, and the test now pins 45 so a
   bypassed rejection fails loudly here instead of silently in front of a user.

### Verified this session, every command run fresh

| Check | Result |
|---|---|
| `venv/Scripts/python.exe -m pytest backend/tests -q` | **517 passed**, 0 failed |
| Byte gate, frozen 247-file snapshot | **`cmp` clean**, 5,429,674 bytes |
| `run_benchmark.py --gate` | **GATE PASSED**, no threshold lowered |
| Real-repo reality check | precision **0.60**, recall **1.00** (TP 6, FP 4, FN 0) — unchanged |
| `npm run typecheck` (`tsc -b`) | exit 0 |
| `npx vitest run` | **173 passed**, 20 files |
| `npm run build` | built in 7.09s |

### What the tool says about its own decomposition

Honest answer: **both files still carry a `complex_function` warning**, because
a different function now holds the maximum in each.

| File | max_cc before | max_cc after | Severity |
|---|---|---|---|
| `dependency_analyzer.py` | 68 (`analyze_dependencies`) | **16** (`_npm_locked_versions`) | high → **medium** |
| `repository_review_engine.py` | 58 (`review_repository`) | **26** (`analyze_single_file`) | high → high |

B1's two targets are fixed. `analyze_single_file` (342 lines, CC 26) and
`apply_interprocedural_taint` (CC 20) were never in B1's scope and are the
obvious next candidates if this is ever revisited.

**Not claimed: none of this was driven through the real UI.** It is a pure
refactor gated on byte-identical output over a real 247-file repository, which
is a different and in some ways stronger guarantee — but it is not the bar
section 3 sets for feature work.

---

## 1d. S10 — Redis rate limiting, done 2026-09-08

Branch `s10/redis-rate-limiting`, 3 commits, **pushed, CI green (run
`34263975530`, all 3 jobs), NOT merged.** `main` is untouched at `5fa6b6e`.

The limiter kept its window in a process-local dict, so each API replica
granted the full budget and the effective limit was N x
`RATE_LIMIT_PER_MINUTE`. Exact on one container, silently wrong the moment
`api` is scaled — which is why this was deploy-adjacent rather than optional.

| Commit | What landed |
|---|---|
| `b9b4ffa` | `backend/app/rate_limit_store.py` (in-process + Redis stores), `api_guard` delegates storage and keeps policy, `/health` reports the live store, 19 tests, `redis` declared directly |
| `d0e4c21` | `redis:7-alpine` service on the CI backend job + a guarded step that runs the Redis tests |
| `29a74f9` | `DEPLOYMENT.md`, `DECISIONS.md` D34, `.env.example` |

`check_rate_limit`'s signature did not change, so neither call site
(`main.py` `/scan` and `/feedback`) moved and the three pre-existing
rate-limit tests were not touched. `REDIS_URL` unset keeps the exact previous
behaviour; set, the window moves into the Redis already running for Celery.

**Design points worth not re-litigating** (full reasoning in `DECISIONS.md`
D34): one Lua `EVAL`, not a pipeline, because a read-then-write lets two
replicas both observe `count < limit`; a sorted set, not `INCR`/`EXPIRE`,
because a fixed window double-bursts across the boundary and cannot give a
true `Retry-After`; Redis `TIME`, not `time.monotonic()`, because monotonic
clocks have no shared origin across processes; a unique member per hit,
because a ZSET is a set and two hits in one millisecond would collapse.

A Redis failure **degrades to the in-process window** — not fail-open, which
would delete the control during the incident where shedding load matters, and
not fail-closed, which would make a broker hiccup a total outage.

### Measured, every command run fresh that session

| Check | Result |
|---|---|
| `venv/Scripts/python.exe -m pytest` before any change | **517 passed, 0 skipped** |
| same, after | **529 passed, 7 skipped** (+12 local store tests; the 7 skips are the Redis file) |
| CI main suite | **529 passed, 7 skipped** — matches local exactly |
| CI Redis step | **7 passed in 0.09s** against real `redis:7-alpine` |
| Lock diff after declaring `redis` directly | provenance annotation only; pin already `redis==6.4.0` |

### Two bugs the first test run caught, both in code written that session

1. **The degraded path logged once per request and re-paid the connect
   timeout each time** — measured ~1s per call against a closed port. A dead
   Redis would have added a second of latency to every request and flooded the
   log. Fixed with a 30s cool-down: one probe per 30s, one warning per outage.
2. **`/health` would have reported `redis` against a dead server.** Building a
   redis-py client performs no I/O — `register_script` only computes a SHA
   locally — so the client looks healthy until the first real command. It now
   pings.

### What is NOT verified, and cannot be here

This machine has **no Docker, no `redis-server`, no Memurai, and WSL has no
installed distribution**, so the Redis path cannot run locally at all — all 7
of those tests skip. The only evidence is the CI step, which is why that step
greps for a nonzero passed count rather than trusting the exit code: an
all-skipped pytest run exits 0, so a green job alone could not distinguish
"the limiter works" from "nothing ran". If you change that env block, the
grep is what stops it failing silently.

**The open bar for whoever merges this:** it has never been exercised by two
real API replicas against one Redis. The property is asserted by two store
objects with separate connections, which is the right unit-level proxy, but
Phase M is where it meets an actual scaled deployment.

---

## 2. What is DONE (verified by running it, not by reading changelogs)

Phases A–H shipped. **The session column matters** — a row is evidence only for
the session that ran it, and anything older is testimony to re-check.

| Area | Evidence | Run in |
|---|---|---|
| Backend suite | `440 passed`, 0 failed | `e4ebf578` (J3) |
| Frontend suite | `98 passed`, 15 files | `e4ebf578` (J3) |
| Typecheck | `npm run typecheck` (`tsc -b`) exit 0 | `e4ebf578` (J3) |
| End-to-end | `26 passed`, 0 failed, 3 projects | `e4ebf578` (J3) |
| Backend suite | `417 passed` in 101.64s | `0f899c51` |
| Fixture gate | `GATE PASSED`, 11/11 types at precision/recall 1.00 | `0f899c51` |
| Real-repo benchmark | precision 0.60, recall 1.00 (TP 6, FP 4, FN 0) | `0f899c51` |
| Frontend suite | `42 passed`, 6 files | `848e92a5` |
| Typecheck | `npm run typecheck` (`tsc -b`) exit 0, 0 errors | `848e92a5` |
| Playwright e2e | `17 passed` across 3 projects | `848e92a5` |
| Production build | built in 4.49s | `a55eaf1f` |
| Live API | `OPTIONS /scan` 200, `POST /scan` 200 with a real `scan_id` | `a55eaf1f` |
| CI on disk | `.github/workflows/ci.yml` (15.9 KB) + `release.yml` | `a55eaf1f` |
| Deploy files on disk | `Caddyfile`, `docker-compose.prod.yml`, both Dockerfiles | `a55eaf1f` |
| Backend suite | `432 passed, 0 failed` — **on merged `main`** | `b5a36f9d` |
| Frontend suite | `70 passed`, 11 files — **on merged `main`** | `b5a36f9d` |
| Typecheck | `npm run typecheck` (`tsc -b`) exit 0 — **on merged `main`** | `b5a36f9d` |
| Playwright e2e | `23 passed` across 3 projects | `b5a36f9d` |
| Production build | built in 5.85s | `b5a36f9d` |
| Snippets carry real source | swept every first-party `.py` under `backend/`: 14 files with findings, **31 findings, 31 with real source, 0 placeholders** | `b5a36f9d` |
| Backend suite | `444 passed, 0 failed` in 39.58s (440 + 4 new) | `d0a60ab7` |
| **J3 bar: real scan → real page** | real clone of `pypa/sampleproject`, driven in a real browser: **11/11 change records on the exact right lines**, "21 changed lines highlighted" = 3x6+3x1, highlights on improved lines 24–30, 0 console errors | `d0a60ab7` |
| Improved code is valid Python | `ast.parse` on all 10 payloads (5 files x original/improved): **10 ok, 0 bad** | `d0a60ab7` |
| Broken-cache fix, real data | flask went from `git fetch` exit 128 to a clean **41s** scan through the browser; its cache came back with `HEAD` + `config` | `d0a60ab7` |
| `start.bat` wait loop | new loop measured **39s** worst case against dead ports (was 60 iterations at the same 3.25s each) | `d0a60ab7` |

**Phase G shipped in session `0f899c51`** — five commits, merged to `main`:

| Commit | What |
|---|---|
| `9691e0c` | S1 — resolve the call target; `app.run()`, `self.run()` no longer Command Injection |
| `193ff23` | S2 — SQL detectors gate on statement shape, not a bare verb |
| `f54df2c` | S3 — list argv judged by `argv[0]`; `["git", *args]` safe, `["sh","-c",user]` not |
| `01b0585` | corpus fixture `f9_detector_precision` + `command_injection` promoted to the no-FP list |
| `35c044b` | kept Phase C's all-constant rule alongside `argv[0]` |

Two departures from the plan as written, plus a fourth defect that was not in
it — all recorded in `DECISIONS.md` **D14**. Read D14 before touching these
detectors; the reasoning is not obvious from the code.

Earlier that session, the dev launcher (commit `03dccba`). Vite had
`port: 8080` but no `strictPort`, so a busy port sent it silently to 8081, which
put the browser on an origin outside the CORS allowlist — every API call died at
the preflight with a bare `OPTIONS /scan 400` in the log. `strictPort: true` now
makes that a loud failure, `start.bat` pre-checks both ports, and two tests pin
the Vite port to the backend allowlist.

---

## 3. What is NEXT — the phase order

Full detail, including acceptance criteria and idea IDs, is in
`docs/STAFF_AUDIT_2026-08-19.md` Phase 4. Summary:

| Phase | Scope | Blocks |
|---|---|---|
| ~~**G**~~ | ~~Detector truth~~ — **DONE**, session `0f899c51` | unblocked M, L |
| ~~**H**~~ | ~~Dependency truth~~ — **DONE**, session `0f899c51` | — |
| ~~**I**~~ | ~~Sidebar defect (F2)~~ `848e92a5` · ~~F1 light/dark theming~~ — **F1 DONE 2026-09-04** (`09847ab`), Phase I complete | — |
| ~~**J**~~ | Explanation UX — **COMPLETE and merged**. J1 (F7, F8, F9-detail, F15), J2 (F6, F9, `snippet`, F16) at `568bf4e`; **J3 (F4, F5) merged 2026-08-25 at `2004639`**, CI green | — |
| ~~**K**~~ | ~~Language contract — B6, F10~~ — **DONE 2026-09-04**, section 1b | — |
| ~~**L**~~ | ~~Dead-code wiring (S8), fixture exclusion (S9), bundle split (F11), F12-F14, H1-H2~~ — **DONE 2026-09-04**, section 1a | — |
| **M** | Deploy | now unblocked |

**Not claimed by any phase in the audit's plan table.** These fall out of the
roadmap entirely and will be missed if nobody looks for them:

| ID | What | Effort | State |
|---|---|---|---|
| ~~S10~~ | ~~Rate limiting → Redis~~ | | DONE, section 1d |
| ~~B1~~ | ~~Decompose `analyze_dependencies` and `review_repository`~~ | | DONE, section 1c |
| ~~B2~~ | ~~CC algorithm vs radon~~ | | DONE `f7e87cf` |
| ~~B3~~ | ~~Declare what the complexity ranking counts~~ | | DONE `2f8a077` |
| ~~B4~~ | ~~`most_reused_module` excludes stdlib~~ | | DONE `2bdeccf` |
| ~~B5~~ | ~~Duplicate-similarity threshold~~ | | DONE `590c980` |
| ~~F3~~ | ~~Sort the file list~~ | | DONE `2f8a077` |

**S10 is done (section 1d) and B1 is done (section 1c). The unassigned
backlog is empty.** What remains is **M** (deploy), which nothing blocks but
which is outward-facing and needs an explicit go-ahead.

### Known open, none of them blocking

| Item | Where | Note |
|---|---|---|
| `generate_improved_code` is a dead switch | `settings_manager.py` | stored and rendered in Settings, read by nothing; the transforms run unconditionally. Changing it alters scan behaviour — its own change. See `DECISIONS.md` D16-area note. |
| `WhatChangedPane`'s `return null` is unreachable | `frontend/src/components/WhatChangedPane.tsx` | found by J3 review, recorded, judged not worth a change on its own. |
| "Cannot reach backend … (Failed to fetch)" | `frontend/src/lib/api.ts` `startScan` | **NOT reproduced** on 2026-08-26 across four repos in a real browser. Leading unproven candidate: `API_BASE` is `http://localhost:8000` while uvicorn binds `127.0.0.1` only, so a browser resolving `localhost` → `::1` gets a refusal with exactly that wording. `DECISIONS.md` D18. Needs the failure captured on the machine that shows it — do not "fix" it blind. |
| Orphan listener on 8000 | — | a socket answering HTTP 200 whose PID `taskkill` cannot find. `start.bat`'s port-clash message will suggest a `taskkill` that fails. Reboot clears it. |

**~~One J3 bar was never met~~ — MET 2026-08-26, session `d0a60ab7`.** It read:
nobody has run a real scan and opened the File Analysis page, so the
change-record line numbers are unproven against a real clone. Done: real clone
of `pypa/sampleproject`, real backend, real browser. 11/11 change records on the
exact right lines across 4 files, `noxfile.py` (6 insertions) included. See the
evidence rows in section 2. **Nothing about J3 is unproven any more.**

**The equivalent bar for whatever ships next:** run it against a real clone
through the real UI before believing it. Two of this session's three findings —
the exit-128 broken cache and the launcher's three-minute stall — were invisible
to a fully green 440-test suite and surfaced the moment the app was actually
driven. The suite is not the acceptance criterion; the running app is.

Phase H shipped in the same session as G:

| Commit | What |
|---|---|
| `3f062f7` | S7 — a constraint is never stored in a version field |
| `3f0b6d6` | S6 — `requirements.lock` / `uv.lock` / `poetry.lock` resolve unpinned deps |
| `44e3456` | S5 — every dependency carries `checked` / `unreachable` / `skipped` |
| `e147e38` | latest-release lookup no longer requires a known installed version |

**`DECISIONS.md` D15 explains why flask now shows `unknown` for all 8
dependencies and why that is correct, not a regression.** D16 records that
`tsc --noEmit` checks nothing here.

### Phase G's acceptance criteria, and what they measured

Kept here because they are the template for judging the phases that follow.

| Criterion | Result |
|---|---|
| `pytest backend/tests -q` ≥ 329 | **373 passed**, 0 failed |
| re-scan flask → 0 security findings | **0** (was 5, all false positives) |
| re-scan the RL project → 0 SQL Injection | **0**; command injection also 3 → 1 |
| fixture gate holds | `GATE PASSED`, 11/11 types at 1.00 |

The new fixture was additionally run against the pre-Phase-G analyzer and fails
it (`command_injection` 0.38, `sql_injection` 0.50, exit 1). **Do this for every
future gate you add** — a fixture that passes both before and after a fix is
measuring nothing, which is precisely how the gate read 1.00 across the board
while every security finding on flask was wrong.

---

## 4. Traps that already cost time — do not rediscover these

- **Interpreter is `venv\Scripts\python.exe` at the repo root**, not
  `backend/venv`. Global Python 3.13 has no fastapi and dies at collection.
- **A cached clone can exist and still not be a git repository.** Fixed
  2026-08-26: an interrupted eviction leaves `.git` holding `objects/`, `refs/`,
  `logs/` and `index` but no `HEAD` and no `config`. The old gate was
  `os.path.isdir(.git)`, so the incremental branch ran `git fetch` on it and
  died with exit 128 — **permanently** for that repo, because nothing ever
  cleared the directory. Three of twelve cached clones were in that state, one
  of them flask. `main._usable_cached_clone` now asks git via `rev-parse
  --git-dir` and a rejected cache falls through to the self-healing full clone.
  If you see a raw `CalledProcessError` repr reach the UI, this is the shape.
- **`start.bat` reporting a port clash can hand you a dead end.** It prints
  `taskkill /F /PID <pid>` from netstat's PID column; on 2026-08-26 that PID had
  no process (`taskkill` said "not found") while the socket still answered HTTP
  200. Not diagnosed further — if it recurs, do not trust the suggested command.
- **A backend can hold port 8000 and never answer HTTP.** Measured 2026-08-26
  from a `start.bat`-launched backend: connect in 0.02s, no response in 60s.
  Probing with curl is right to call that not-ready; the old wait loop then held
  the browser ~3 minutes. Now bounded to 12 iterations (39s measured).
- **No Docker on this machine.** The GitHub runner has it and `ci.yml` has a
  `deploy-stack` job that boots the compose stack. Container claims get verified
  in CI, never locally.
- **The Bash tool's working directory persists between calls.** A `cd frontend`
  silently breaks a later repo-root command. Use absolute paths.
- **Zero-byte junk files keep appearing in the repo root.** Unquoted `(` or `)`
  in a bash command is one cause — the stray `390.52` is a captured Vite bundle
  size. But session `0f899c51` produced `1.0`, `bool`, `str` and `analyzer`
  without any unquoted parens, so something else in the toolchain also does it.
  **Run `git status --short` after every commit** and delete what appears. This
  is survivable only because explicit-path staging is mandatory here; `git add
  -A` would have committed all four.
- **Run `npx vite` from `frontend/`, never the repo root** — the root resolves a
  different Vite major than the pinned 5.4.21.
- **`tsc --noEmit` typechecks NOTHING here and exits 0.** `frontend/tsconfig.json`
  is solution-style — `"files": []` plus `references` — so the command compiles
  an empty program and reports success. Use **`npm run typecheck`** (`tsc -b`),
  which is what `ci.yml` runs. Session `0f899c51` was handed a green
  `tsc --noEmit` and `tsc -b` then found 6 real type errors in the same tree.
- **Exclude `backend/app/.cache/` from greps.** It holds cached scan JSON and a
  careless recursive grep returns megabytes.
- **Never `pip install` analysis tools into the project venv.** Use a throwaway
  venv; changing dependency resolution invalidates every later test result.
- **`git add -A` is banned here** — the tree carries gitignored `.env` and stray
  junk. Always stage explicit paths.

---

## 5. BUG-001 — CLOSED 2026-08-20

**The sidebar disappeared in a half-width browser window.** Root-caused, fixed
and verified in session `848e92a5` (`8ce9a3e`). Full write-up, including the
measurement table, in `docs/bugs/BUG-001-sidebar-split-view.md`.

One line of it: `useIsMobile()` listened to `(max-width: 767px)` but stored
`window.innerWidth < 768` — one breakpoint written two ways, and viewport
widths are fractional under Windows display scaling while `innerWidth` is
rounded. The hook now uses `(min-width: 768px)`, the exact query Tailwind's
`md:` emits, and reads `event.matches`.

**The transferable lesson:** headless Chromium was correct at every integer
width 320–1440 on all 15 routes. jsdom, Playwright viewports and the devtools
device toolbar are all whole-pixel. A layout bug that lives at fractional
widths is invisible to all of them — it took a headed window at
`devicePixelRatio` 1.25 swept one pixel at a time. If a UI bug will not
reproduce, check whether your tooling can even express the condition.

---

## 6. Strategic direction (decided, revisit only with the user)

Do **not** compete on raw detection — Semgrep, CodeQL and Sonar are free on
public repos and more precise. The durable differentiator is the **explanation
and trust model**: deterministic findings, labelled sources, trust boundaries,
confidence values, and honestly documented analysis limits.

Reposition as an **explainable repository health report**. Lead with health,
complexity, structure, dependencies and duplication. Demote security to a
clearly labelled "candidates to triage" section with the precision numbers
published openly. Phase J exists to serve this.

---

## 7. Session-end ritual

Before ending any session, update this file's section 1 and section 3, append to
`docs/DECISIONS.md` if a real decision was made, and note which model made it.
Five lines is enough. This is the cheapest habit available and the one that
stops the next session starting cold.
