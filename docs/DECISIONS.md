# DECISIONS — why, not just what

Code shows what changed. This shows why. Append here whenever a real decision is
made; six months from now this is the only thing that stops a settled argument
being re-litigated.

**Format:** date · decision · reasoning · who/what decided it. Newest last.

---

### D1 — Call graph uses stdlib Tarjan SCC, not networkx
**Chunk 3** · Decided by: Pranav (sign-off) + Claude

The roadmap in `SYSTEM_AUDIT_2026-07.md` suggested networkx. We used an
iterative Tarjan SCC from the stdlib instead.

**Why:** the whole `analysis/` layer is deliberately dependency-free, which
keeps it pure, fast to import and trivial to test. Taking a graph library for
one algorithm we can write in 40 lines reopens that property for no gain.

**When to revisit:** if a future phase genuinely needs centrality or PageRank.
Take the dependency *then*, with a concrete justification. This is not an
oversight to be "fixed".

---

### D2 — `api_guard` reads environment at call time, `celery_app` at import time
**Phase A**

`api_guard.py` reads every env var inside the function that needs it.

**Why:** it lets the test suite monkeypatch `os.environ` without reloading
`main`. `celery_app.py` reads its config at import time and is correspondingly
painful to test — that is the counter-example, not the pattern. Copy
`api_guard`.

**Exception:** `CORSMiddleware` needs a fixed origin list at construction, so
`ALLOWED_ORIGINS` is read exactly once at import. Changing it requires a process
restart, and monkeypatching it in a test has no effect on the live middleware —
test `api_guard.allowed_origins` directly instead.

---

### D3 — Auth is middleware, not a per-route dependency
**Phase A**

**Why:** the failure mode of the dependency approach is a route that ships
without one and is silently public. As middleware, a newly added route is
protected by default. The cost is that public paths need an explicit allowlist
(`PUBLIC_PATHS`), which is a smaller and more visible risk.

`OPTIONS` is exempt because the browser sends preflights without custom headers
by design — requiring `X-API-Key` on them would break every cross-origin call
before the real request was made.

---

### D4 — CORS registered after auth so it sits outside it
**Phase A**

Starlette runs the last-added middleware outermost.

**Why:** a 401 still comes back with CORS headers, so the browser can surface
"unauthorized" rather than an opaque network error. Reversing the order turns
every auth failure into an unexplainable fetch error.

---

### D5 — The SSE stream accepts a key in the query string; nothing else does
**Phase A**

**Why:** the browser's `EventSource` API cannot set request headers at all, so
an authenticated deployment would have no way to open the progress stream if
`X-API-Key` were the only channel. Scoped to this one route because query
strings are logged by proxies and land in browser history — a key there is far
less private than one in a header.

---

### D6 — The clone cache keeps full history, not a shallow clone
**Phase 6 / Chunk 5**

**Why:** a re-scan diffs the previous SHA against the new HEAD and re-analyzes
only changed files. `--depth 1` would make that impossible.

**Tradeoff accepted:** disk grows with one clone per distinct repo URL. Bounded
by `disk_guard`'s LRU eviction and per-repo size cap, not by the clone strategy.

---

### D7 — Disk eviction runs outside the scan's try block
**Phase E**

**Why:** an exception during eviction would escape before `complete_scan()` ran
and strand the scan in a non-terminal state with no error shown to the user —
unlike every other failure path, which terminates cleanly. Eviction is
housekeeping, not part of the scan's contract. If a directory is locked longer
than the retry budget, log it and scan anyway.

A reviewer caught a related inversion in the eviction ordering: an entry with no
timestamp is not the least useful, it is the one a sibling worker is writing
right now. Sorting it first would delete live clones.

---

### D8 — The LLM layer is paraphrase-only and gated off
**Phase 5**

**Why:** determinism is the product's trust property. Every finding, severity
and score is computed by AST analysis. The Anthropic layer turns those into
prose and is instructed not to invent issues. Output carries
`explanation_source` = `llm` | `deterministic` so a reader always knows.

**Consequence for copy:** marketing and README must never claim the LLM "finds"
or "reasons about" bugs. The refactor engine is likewise fully deterministic AST
transforms — never describe it as AI.

---

### D9 — Analysis prefers false negatives over false positives
**Standing principle**

**Why:** a tool that cries wolf gets ignored entirely. Dynamic dispatch and
framework entrypoints are treated as ALIVE rather than risk flagging a live
handler as dead. Ambiguous cross-file names are skipped rather than guessed.

**This principle is currently inverted in three security detectors** — see D12.
That is a defect, not a change of policy.

---

### D10 — Benchmark floors are raised the moment a defect is fixed
**Phase C**

A floor set *at* a known defect makes the gate ratify the bug instead of
catching it. Phase C raised `unsafe_deserialization` recall 0.33 → 1.0 and
`command_injection` precision 0.66 → 1.0 as the underlying bugs were fixed.

**Rule:** every floor sits at 1.0. Any regression fails the gate, and a new
sub-1.0 floor must be argued for rather than inherited.

---

### D11 — Vite pins its port; the dev origin may not drift
**2026-08-19** · Claude Opus 5, session `a55eaf1f` · commit `03dccba`

Vite had `port: 8080` without `strictPort`, so a busy port sent it silently to
8081. The page still loaded, so the app looked up — but its `Origin` was no
longer in the backend's CORS allowlist and every API call died at the preflight,
showing only a bare `OPTIONS /scan 400` in the log.

**Why `strictPort` rather than widening CORS:** widening the allowlist would
weaken a Phase A security control to paper over a dev-server problem. Failing
loudly on a busy port is the smaller, more honest fix. `start.bat` now names the
PID holding the port, since a stale server from a previous run is the usual
cause.

Two tests pin the Vite port to the backend allowlist — they live in different
languages with nothing else connecting them, which is exactly how they drifted.

---

### D12 — Reposition around explanation, not detection
**2026-08-19** · Claude Opus 5, session `a55eaf1f` · recommendation, awaiting
Pranav's confirmation

An independent cross-check (bandit / radon / pyflakes / vulture) over three
pinned repositories found **5 of 5 security findings on `pallets/flask` were
false positives**, and 2 of 4 on an unrelated RL project.

**Why reposition:** Semgrep, CodeQL and Sonar are free on public repos and
materially more precise. Competing on raw detection is a losing position. The
durable differentiator is the explanation and trust model — deterministic
findings, labelled sources, trust boundaries, confidence values, and honestly
documented limits. Semgrep hands you a rule ID; this hands a junior developer a
lesson.

**Decision:** lead the product with repository health, complexity, structure,
dependencies and duplication, where it is measurably competent. Demote security
to a clearly labelled "candidates to triage" section with the precision numbers
published openly. Fix the three worst detectors anyway (Phase G) — shipping
known-wrong High-severity findings is not acceptable regardless of positioning.

**Alternative considered and not chosen:** delegate raw detection to
Semgrep/bandit and keep only the explanation layer. Best product, largest
rewrite. Revisit after Phase G if precision remains a problem.

---

### D13 — Deployment target is a single small VPS
**2026-08-19** · Claude Opus 5, session `a55eaf1f` · recommendation

**Why:** the Caddy + compose + GHCR stack already exists and needs no
re-architecture. `/scan` needs persistent disk for the clone cache and its LRU
eviction, and runs long CPU-heavy jobs — which is precisely what rules out
Vercel/Netlify/Lambda, and what makes Render/Railway's ephemeral disk a poor
fit. Fly.io is workable but scaling past one machine breaks the in-process rate
limiter and the shared-SQLite assumption.

Rough cost ~$5–7/month. **Not verified during the audit** — check current
pricing before committing.

---

### D14 — Phase G departs from its own written plan in two places
**2026-08-19** · Claude Opus 5, session `0f899c51` · implemented, verified

`HANDOVER.md` specified the three detector fixes. Two were implemented
differently from the text, deliberately, and the reasons need to outlive the
commit messages.

**S3 — not "list argv with no shell is safe".** Taken literally that clears
`subprocess.run(["sh", "-c", user])`, which is a live injection and is exactly
what Phase C had fixed after finding the analyzer describing it to the reader
as a "safe invocation pattern". The rule implemented instead is: a list argv
with `shell` not True is cleared when **either** every element is a literal
(Phase C's rule, kept) **or** argv[0] is a string literal naming a non-shell
program with no `-c` style flag present. Under `shell=False` the list goes to
execve as-is, so nothing after argv[0] can begin a new command — which is why
`["git", *args]` is inert and was being reported.

The all-constant half was not in the first implementation and had to be put
back. Rescanning a real repository found `subprocess.run(["powershell",
"-NoProfile", "-Command", "<literal>"])` still flagged, because argv[0] names a
shell. Every element was constant. **The lesson is that the real-repo rescan,
not the unit tests, caught it** — the tests only knew the cases their author
had thought of.

**S2 — SQL shape only, not sink reachability.** HANDOVER asked for statement
shape "and, better, that the value reaches a cursor/execute sink". Shape alone
is what shipped. The corpus fixture builds its query on one line and executes
it on the next (`q = "SELECT ... " + uid` / `db.execute(q)`), so gating on sink
adjacency would trade this false-positive class for a false-negative one, and
the taint layer's dataflow is not wired into these two visitors. Shape is
gated as: the string must **begin** with a SQL verb and contain a clause
keyword. Requiring the leading verb rather than matching `select ... from`
anywhere is what separates "Please select an option from the menu" from a
query.

**A fourth defect, not in the plan.** `visit_BinOp` carried the identical
substring bug to `visit_JoinedStr` — `log("Deleted user: " + name)` was a High
SQL Injection finding. Fixed in the same commit as S2.

**Known residual, accepted:** prose that genuinely begins with a SQL verb and
contains a clause word — `f"Select a template from {folder}"` — still matches.
Narrower than the class removed, and tightening further starts to cost real
queries.

**Verified:** pytest 373 passed, fixture gate 1.00 across all 11 types, flask
**0** security findings (was 5, all false positives), RLPROJECT **0** SQL
Injection and command injection 3 → 1. The new corpus fixture was checked
against the pre-Phase-G analyzer and fails it at command_injection precision
0.38 / sql_injection 0.50, so the gate is load-bearing rather than decorative.

---

### D15 — "unknown" is a first-class dependency answer
**2026-08-20** · Claude Opus 5, session `0f899c51` · implemented, verified

Phase H's three items turned out to be one decision: **the report may not
invent a version, and it may not imply a safety claim it did not earn.**

Every Python manifest parser had been stripping the operator off a specifier
and keeping the digits, so `flask>=2.0` was stored as version `2.0` — and that
invented number was then the OSV query key, producing CVEs against a version
the project may not install. The node side had already rejected this in Phase C
(`_exact_npm_version`); Python simply never got it.

**Decision:** `version` holds a concrete version or `"unknown"`, never a
constraint. Three fields carry what used to be conflated into one:

| field | meaning |
|---|---|
| `version` | a concrete version, or `unknown` |
| `constraint` | the specifier as written, e.g. `>=2.0` |
| `version_source` | `pinned` / `lockfile` / `unpinned` / `unspecified` |
| `vuln_lookup` | `checked` / `unreachable` / `skipped` |

**Why `vuln_lookup` matters more than it looks.** An empty vulnerability list
meant three different things and rendered as one green tick: OSV answered zero,
OSV was unreachable, or nothing was ever asked. A security report that cannot
distinguish "clean" from "the lookup was down" is worse than one that says
nothing, because it is believed. A failed lookup is also no longer cached — it
was being written into a 24-hour cache as an empty result, so a single timeout
reported a package clean for the rest of the day.

**Consequence accepted:** pinned `pallets/flask` now shows `unknown` for all 8
dependencies, where it used to show confident numbers. That is not a
regression. Flask pins nothing and ships no lockfile, so the honest answer is
that its installed versions are not knowable from the repository — and no CVE
claim is made in either direction. The lockfile readers (`requirements.lock`,
`uv.lock`, `poetry.lock`) mean any project that *does* pin gets real answers.

**Two bugs found on the way, neither in the plan.** The `setup.cfg` reader
decided where `install_requires` ended by testing `stripped[0].isspace()` on a
string it had already stripped — always False — and since every versioned
requirement contains `=`, the section closed on its own first line; no
setup.cfg dependency carrying a version had ever been recorded. And the PyPI
"latest release" lookup sat behind the unknown-version guard, so making
versions honest silently removed the upgrade target from every unpinned
dependency. **The acceptance criterion caught the second one, not the tests** —
the same pattern as Phase G, where the real-repo rescan caught what the unit
tests could not.

**Verified:** pytest 417 passed, `npm run typecheck` 0 errors, vitest 39
passed; against pinned flask, `latest_version` resolved **8/8**, zero
constraints in any version field, zero dependencies missing a lookup status.

---

### D16 — `tsc --noEmit` is not the frontend typecheck
**2026-08-20** · Claude Opus 5, session `0f899c51` · correction to the record

`frontend/tsconfig.json` is solution-style: `"files": []` plus `references` to
`tsconfig.app.json` and `tsconfig.node.json`. `tsc --noEmit` therefore compiles
an **empty program** and exits 0 having checked nothing — `--listFiles` prints
zero lines.

`ci.yml` has always run `tsc -b` and carries a comment explaining exactly this,
so the shipped gate was never wrong. What was wrong was the local ritual:
`HANDOVER.md` recorded `tsc --noEmit exit 0` as evidence, and this session
believed it before `tsc -b` found six real type errors in the same tree.

**Decision:** `npm run typecheck` (added, runs `tsc -b`) is the only frontend
typecheck anyone should type. A green result from a command that checks nothing
is worse than no result, because it is recorded as evidence.

---

### D16a — one breakpoint, one query, read from the query itself

> **Numbering:** this entry and the J1 record below were both written as "D17"
> by different sessions. `HANDOVER.md` cites D17 meaning the J1 record, so that
> one keeps the number and this one takes a letter suffix — renumbering it to
> D20 instead would have placed a Phase-I ruling after two Phase-J ones in a
> log that is otherwise chronological.
**2026-08-20** · Claude Opus 5, session `848e92a5` · root cause of BUG-001

`useIsMobile()` listened to `(max-width: 767px)` and stored
`window.innerWidth < 768`. Two spellings of one breakpoint, equivalent only at
whole-pixel viewport widths. Windows display scaling makes the width
fractional; `window.innerWidth` reports it rounded. The band
`767 < w < 768` matches neither query, and crossing it either rendered the
desktop sidebar inside a `display: none` box or latched the hook on
`isMobile = true` until a page reload.

**Decision:** a JS breakpoint check must use the **same media query string**
its CSS counterpart compiles to, and must take its value from
`event.matches` — never from a re-read of `window.innerWidth`. The hook now
listens to `(min-width: 768px)`, which is exactly what Tailwind's `md:` emits.

**Standing consequence:** do not introduce a second place where a breakpoint is
expressed. If a component needs the answer, it calls the hook.

**Also recorded:** whole-pixel tooling cannot see this class of bug. Headless
Chromium was correct at every integer width 320–1440 across all 15 routes;
jsdom and Playwright viewports are integers too. Reproduction needed a headed
window at `devicePixelRatio` 1.25 swept one pixel at a time. The gate is
therefore a unit test that models fractional widths directly
(`src/hooks/use-mobile.test.ts`), not the Playwright spec.

**Verified:** the two fractional cases watched failing against the old hook
first; then vitest 42/42, `npm run typecheck` exit 0, Playwright 17/17, and the
headed browser walk re-run against the rebuilt app.

---

## D17 — J1 is an extraction, not a new explanation layer

**Date:** 2026-08-20 · **Decided by:** Claude Opus 5 session `cc9e8871`
**Branch:** `phase-j/j1-explanation-parity` (9 task commits + fixes)

Phase J bundles seven ideas across six pages and budgets three sessions, so it
was split into J1 (explanation parity), J2 (wayfinding: F6, F9's expand,
`snippet`, F16's a11y pass) and J3 (the code panes: F4, F5). This records J1.

**The finding the design rests on:** every field Phase J wanted to surface was
already computed by the backend and already rendered correctly — on exactly one
page. `FileAnalysis.tsx` showed `why_it_matters`, `how_to_fix` and `confidence`;
`SecurityReport` and `IssueExplorer` never read them. So J1 extracted the
treatment that worked (`lib/findings.ts` normalizers + `components/FindingCard.tsx`)
rather than designing a new one. No backend change, no LLM involvement, no new
dependency.

**F15 changed direction mid-design, and that is the decision worth keeping.**
The production-only security count looked like a frontend labelling bug. It is
not: the filter lives in `repository_review_engine.py:512-514` and feeds
`health_score` at 25% weight (`:570`, `:574`), and **S9 (fixture exclusion) is
still unstarted**, so widening the count would pull this repository's own
deliberately-vulnerable `benchmark/corpus/fixtures/` into the headline number.
The scoping stays; the page now matches it and accounts for what it excluded
("N further findings in test/non-code files") instead of silently disagreeing
with the dashboard tile.

**Two latent bugs surfaced and were fixed by the extraction.** The old inline
block guarded numeric fields on truthiness (`issue.confidence &&`), so a
confidence of exactly `0` and a line of `0` rendered nothing. `FindingCard`
tests `!== undefined`. A test pins the `0% Match` case.

**Two copy claims were corrected as honesty fixes**, both flagged by the final
review: the AI Suggestions subtitle said "AI-generated" directly above badges
reading "Rule-based" (the LLM layer is off by default — CONSTRAINTS 18), and the
Security Report all-clear said "No security vulnerabilities detected"
unqualified while the same page could render "N further findings in test files".

**Carried forward to J3, so it is not re-litigated:** F5's prose comes from a
structured change list replacing the string-counting at
`heuristic_refactor_engine.py:265-281` (which counts `"""` occurrences ÷ 2 and
miscounts single-line docstrings), and F4's empty state says what was actually
checked rather than implying a clean bill of health.

**Verified this session:** vitest **59 passed / 11 files** (baseline 42/6 at plan
start, measured not inherited), `npm run typecheck` (`tsc -b`) exit 0,
`npm run build` succeeds, Playwright **17 passed** matching baseline. A 3-failure
Playwright run was investigated rather than assumed flaky: the failing project
passed 6/6 alone on both the merge-base and this branch, and the full suite then
returned 17 — parallelism under concurrent projects, not a regression.

**Process note worth keeping:** sequencing the reference-page swap last paid off.
Because Task 7 was a pure swap against an already-green tree, the latent bugs in
the old block surfaced as observations rather than as a conflict between
"extract the reference" and "fix the reference". The gap was that no task owned
the closing documentation obligations, and Task 7's brief deferred them to a
browser step its implementer could not run — a plan-shape issue, not an
implementer one.

---

## D18 — The `snippet` field is made real rather than rendered as-is

**Date:** 2026-08-24 · **Decided by:** Pranav, on analysis by Claude Opus 5
(session `b5a36f9d`) · **Phase:** J2

J2's brief said "wire the unused `snippet` field". Before writing any code we
measured what the field actually contained, across all 523 cached scans in
`backend/app/.cache/`:

```
total snippet fields: 271
163  ''
  4  'Line 481 indicates: Command Injection'
  4  'Line 151 indicates: SQL Injection'
  ... every remaining non-empty value has this same shape
```

**60% empty; zero contain source code.** The producers were
`security_analyzer.py:433` emitting `f"Line {line} indicates: {issue_type}"` and
`repository_review_engine.py:356,364` emitting `f"Line {f.line}"`. Rendering
that field would have printed the line number a second time beside the existing
`Line 42` badge and restated the finding's own title.

**Decision: make the field real at both producers rather than render the
placeholder.** A new stdlib-only `backend/app/services/snippet.py` extracts the
flagged line ±2, numbered and dedented, returning `""` — never a sentence — when
there is no source. Both producers already had the source in scope, so no
plumbing was needed: `SecurityAnalyzer.__init__` already stored `_source_lines`,
and `apply_interprocedural_taint` already built a `sources` map.

**Consequence that had to be handled:** the 523 cached scans predate this and
replay through the UI, so `lib/findings.ts` filters `/^Line \d+( indicates: .+)?$/`
before the snippet reaches a code pane. Without that filter, shipping J2 would
have given every historical report a code pane full of restated line numbers.

**A real bug this surfaced, caught in final review:** both producers originally
built their line array with `str.splitlines()`, which breaks on `\x0c`, `\x0b`,
`\x85` and others that **Python's tokenizer does not treat as line breaks**. A
form feed above a flagged line shifted the snippet *and* its printed numbers, so
the evidence pane would confidently show the wrong code under the right number —
precisely the trust failure this phase exists to remove. Both sites now use
`.split("\n")`, which is exactly AST-aligned because `repo_analyzer.py:242` reads
with universal newlines. Regression test uses a form feed.

**Out of scope, recorded not fixed:** the code-quality `issues` path
(`repository_review_engine.py:195`) has no snippet upstream and still emits `""`.
Those findings are file- and function-scoped, so there is often no single line to
show.

---

## D19 — Security Report shows five severity tiers, not four

**Date:** 2026-08-24 · **Decided by:** Pranav, on analysis by Claude Opus 5
(session `b5a36f9d`) · **Phase:** J2

The audit's F6 says "the 4 severity tiers". `Severity` in `frontend/src/lib/types.ts`
has **five** values, and its own doc comment records why `Info` is distinct: a
code-exec sink that taint analysis proved is reachable only from local operator
input, "kept distinct so the UI does not collapse it to Low".

The page rendered four tiles. An `Info` finding therefore appeared in the list
below while **no tile counted it**, and the tiles did not sum to the headline —
the same defect class as audit item F14 (`healthScore` 54 vs `avg_score` 90.3).

**Decision: five tiles, `md:grid-cols-5`, corrected rather than preserved.**
Deviating from the audit's wording is deliberate and recorded here so it is not
"fixed" back later.

The tiles also stopped being decoration: each non-zero tier scrolls to its
severity group **and moves focus to that group's heading**. Scrolling alone
leaves a keyboard or screen-reader user where they were — moving focus is what
makes a tier a navigation control. A zero-count tier renders as plain text, not a
button, and its group renders nothing.

**Why the sum is now safe:** `mapSeverity` (`response-mapper.ts:269-277`) is
total — every incoming string funnels into one of the five values with `Low` as
fallback — and it is the only path constructing a `SecurityVulnerability` from
API data, cached replays included. So no finding can fall outside all five
buckets. This matters because the grouped list renders a finding **only** if it
lands in a group, where the old flat list rendered everything unconditionally.

---

## D20 — The change list is one source for three consumers

**Date:** 2026-08-25 · **Phase:** J3 (F4, F5) · **Model:** Claude Opus 5,
session `e4ebf578`

The refactor engine applies exactly two AST transforms — a placeholder
docstring for anything undocumented, and `-> None` on a function that never
returns a value. It knew precisely what it inserted and threw that away. Three
things downstream then had to re-derive it, and each got it wrong differently.

The engine now returns a change record per edit — kind, target, symbol name,
1-based line **in the improved file**, and how many lines it spans — and that
one list feeds the summary sentence, the highlighting in the suggested-edits
pane, and the prose in the what-changed pane. The prose and the highlighting
cannot disagree, because they read the same array.

**What the old summary actually did.** It counted lines containing `"""` in the
improved file, subtracted the count in the original, and divided by two. That
assumes every docstring spans two such lines. The engine emits a *single-line*
docstring for classes and for parameterless functions, so `1 // 2 == 0`: a file
whose only gaps were parameterless functions reported "Added docstrings to 0
undocumented function(s)/class(es)" while the pane beside it displayed the
docstrings it had just inserted. This was observed failing before the fix, not
argued from reading.

**Why the summary sentence was rebuilt and not deleted.** J1's record called for
"replacing the string-counting", which reads as *delete that block*. It writes
into `explanation`, which turned out to have three readers — the Explanation
card, `AISuggestions`, and `ExportReport` — so deleting it would have silently
stripped content from two pages J3 was not asked to touch. It is rebuilt from
the change list instead, which fixes the miscount everywhere it appears.

**Why the pane stopped saying "Improved".** Nothing is measurably improved: a
placeholder docstring naming the symbol records a gap, it does not describe
behaviour, and none of it is applied to the repository. The tab is "Suggested
edits", and when the engine has nothing to add the pane names the two checks
that ran and states that no other transform was attempted — an unchanged file
is not a clean bill of health. Same lineage as the J1 copy corrections
(CONSTRAINTS 18).

**The normalizer is a trust boundary, and its first version was not.** As
originally specified it accepted `line: 1.5` and an unbounded `line_count`,
where the pane expands each record into a Set of line numbers — one corrupt or
hostile record would have frozen the browser. Now `line` must be an integer in
[1, 1e6] or the record is dropped, and `lineCount` must be an integer in
[1, 10000] or it degrades to 1 and the record is kept. The two fields are
treated differently on purpose: `line` says *where*, and a nonsense location
makes the record unusable; `lineCount` says *how many*, and a nonsense span can
safely fall back to the minimum.

**Scans recorded before this change** carry a `patch` and no list. The
what-changed pane says so and shows the diff outright, rather than
reverse-engineering prose from a rendered artifact.

**Still true, and deliberately not fixed here:** `generate_improved_code` in
`settings_manager.py` is stored and rendered as a switch in Settings, but no
code reads it — the transforms run unconditionally. Changing that alters scan
behaviour and belongs in its own change.

---

## D17 — A cache is trusted only if the tool that will use it accepts it

Decided 2026-08-26 (Claude Opus 5 session `d0a60ab7`).

The re-scan branch gated on `os.path.isdir(repo_dir/".git")`. That asks the
filesystem a question only git can answer, and the two disagree in exactly the
case that bites: an interrupted eviction leaves `.git` with `objects/`, `refs/`,
`logs/` and `index` but no `HEAD` and no `config`. `git fetch` then exits 128
and the raw `CalledProcessError` repr reaches the user.

**Why it mattered more than a normal bug:** the failure was permanent, not
transient. Nothing cleared the broken directory, so every later scan of that
repo took the same branch and died the same way. Three of twelve cached clones
on the dev machine were in that state.

**The rule taken from it:** when a cheap, self-healing path already exists,
validate the fast path with the real tool and fall through on any doubt. The
full-clone branch already cleared a stale directory before cloning, so the fix
was to route a rejected cache into it rather than to add repair logic.

Rejected: deleting the corrupt directories by hand. That fixes three repos on
one machine and leaves the defect in place.

## D18 — A launcher's worst case is a user-facing feature

Decided 2026-08-26, same session.

`start.bat` opened the browser only after its readiness loop finished, and the
loop ran 60 iterations at a measured 3.25s each. A backend that never answered
therefore held the browser for around three minutes behind one unchanging
"Waiting..." line, and the operator opened the page by hand — the exact outcome
the step exists to prevent.

The probe was not wrong to refuse: a backend was observed with its port bound
and accepting (connect 0.02s) and no HTTP response in 60s. What was wrong was
spending minutes in silence on that refusal. Now 12 iterations (39s measured), a
dot per iteration, and the browser opens either way with the backend warning
still printed.

Probes now use `127.0.0.1` rather than `localhost`: uvicorn binds IPv4 loopback
only, and `localhost` can resolve to `::1` first, which would make a healthy
backend look dead.

**Not fixed, and named so it is not mistaken for done:** the reported "Cannot
reach backend at http://localhost:8000 (Failed to fetch)" was NOT reproduced
this session. Scans of `pypa/sampleproject`, `pallets/click`, `pallets/flask`
and `Pranav-1201/churnlens-dashboard` all succeeded through a real browser. The
leading unproven candidate is the same IPv4/IPv6 split — the page's `API_BASE`
is `http://localhost:8000` while uvicorn listens only on `127.0.0.1`, so a
browser that resolves `localhost` to `::1` gets a refusal that surfaces with
exactly that wording. Proving it needs the failure captured on the machine that
shows it.

---

## D21 — S9 is a prerequisite for S8, not hygiene beside it

**Date:** 2026-09-04 · **Decided by:** Claude Opus 5 (session `3d35d767`)

The audit lists S8 (wire dead-code findings into the report) and S9 (stop
reporting fixture corpora) as two independent Phase L items. Measured against
this repository they are not independent.

The analyzer finds 462 dead functions. 374 of them are in `backend/tests`, 372
of those named `test_*`. 25 more are in `backend/benchmark`. Only 10 are
production code, and one of those 10 is a false positive. Wiring S8 without
first excluding the noise would have put roughly 452 known-bad findings into
the report — precisely the false-positive generator `CONSTRAINTS.md` section 21
forbids, and the same failure the audit graded the analyzer 4/10 for.

So S9 landed first, and S8's scope was narrowed: dead imports everywhere, dead
functions in production files only. Result on a real scan: 28 dead imports and
62 dead functions, 0 findings from fixtures, down from 35.

## D22 — The dead-function false-positive guard lives in the detector

**Date:** 2026-09-04 · **Decided by:** Claude Opus 5 (session `3d35d767`)

Two exemptions were missing from `call_graph.find_dead_functions`: pytest
collects `test_*` by name with no decorator to key off, and a method overriding
a base class defined outside the scanned sources is called by whatever owns
that base (`JsonFormatter(logging.Formatter).format` is the measured case).

Both could have been filtered at the report layer instead, which would have
been a smaller change. Rejected: the benchmark gate measures the detector, so a
downstream filter would leave the gate reading precision 1.00 on an analyzer
that still calls `Formatter.format` dead. That is a gate ratifying a known
defect, which is the failure mode section 11 exists to prevent.

Recall was checked before the change, not after: the `f6_dead_functions`
fixture's two true positives are `_never_called` in `main.py` and `orphan` in
`helpers.py`. Neither is named `test_*` and neither is a method of any class,
so neither guard can reach them. Gate re-run confirms 1.00/1.00.

## D23 — Dead-code locations are resolved at the report layer

**Date:** 2026-09-04 · **Decided by:** Claude Opus 5 (session `3d35d767`)

`dead_code_detector` returns bare names with no line numbers, and J2's
acceptance bar was "every finding carries real source, zero placeholders".
Three options:

- **Chosen:** resolve names back to lines in `analyze_single_file`, which
  already holds the file's source. No detector contract change.
- **Rejected:** widen the detector to return `{name, lineno}` dicts. The
  benchmark already tolerates that shape, but it breaks six assertions across
  `backend/tests/test_dead_code.py` and `backend/validation/phase4_validation.py`
  for nothing the chosen option does not deliver.
- **Rejected:** emit `line: 0` with an empty snippet. Cheapest, and it regresses
  the placeholder-free property J2 was built to establish.

## D24 — The analysis cache key must include the file's role

**Date:** 2026-09-04 · **Decided by:** Claude Opus 5 (session `3d35d767`)

Found by a test written for S8b, not by review. `CacheManager`'s key is
`(version, content, imports)`. The file's role was never part of it, so two
files with identical content and different roles collided and whichever was
analysed first won.

This was already wrong before Phase L — the role drives `is_test`, which drives
the security pass — but S9 made it unsound rather than merely inaccurate: a
fixture whose content matched a production file would have been served the
production result and leaked its planted findings straight through the
suppression.

Fixed by folding the role and coarse type into the version string rather than
changing `CacheManager`'s signature, which two other call sites depend on.

## D25 — F11's audit criterion measures the wrong thing

**Date:** 2026-09-04 · **Decided by:** Claude Opus 5 (session `3d35d767`)

The audit's F11 criterion is "Visualizations chunk < 250 kB". `Visualizations`
was already a lazy route chunk, so its 432 kB downloaded only when that page
was opened — and a `manualChunks` rule moving recharts into a vendor chunk
would have satisfied the criterion literally while making the application no
faster at all.

The cost worth removing is time to first paint on that route. So the charts
were moved into their own component and loaded with `React.lazy` inside the
page: the shell and the dependency graph now render from a 4.43 kB chunk while
the 428 kB chart bundle streams in behind a skeleton sized to the chart grid.

Recorded because the next person to read the audit will read the criterion, not
this reasoning.

## D26 — F14 is answered by explanation, not by reconciliation

**Date:** 2026-09-04 · **Decided by:** Claude Opus 5 (session `3d35d767`)

A scan reports `health_score` 53 and `average_quality_score` 90.56 in the same
summary. Investigating them showed no bug: the average is a mean of per-file
quality scores, and the composite weights that average at 35% alongside
security 25%, documentation 20% and simplicity 20%. The frontend's apparent
third computation is a fallback that mirrors the backend formula exactly and
runs only when the field is absent.

Reconciling them would have destroyed information — they answer different
questions. What was actually missing was any statement of the relationship, so
the Health Score page now shows each dimension's weight and says the ring is
their blend.

The fourth dimension was also renamed from "Performance" to "Simplicity". Its
value is `100 - min(avgCC * 3, 80)`, which measures complexity and says nothing
about runtime performance; the backend already calls the same number
`simplicity_score`. A label that misstates what was measured is a correctness
problem, not a cosmetic one.

---

## D27 — B6 rejects at discovery, not at submit

**Date:** 2026-09-04 · **Decided by:** Claude Opus 5 (session `3d35d767`)

The audit asked for a 422 on an unsupported repository. That is not
reachable: `POST /scan` returns a scan id and the clone happens in the
background, so at submit time nothing is known about the repository's
contents beyond its URL.

So the rejection lives where the knowledge is — discovery, in `run_pipeline`
— and travels the async failure path that already exists: `complete_scan`
emits `status='error'` with the message and the frontend poller already
reads it.

What made this worth doing at all was measuring the current behaviour. A
MATLAB-only repository did not produce a generic error. It produced a
*successful* scan reporting health_score 45 on zero files, because the
composite defaults security and simplicity to 100 when there is nothing to
measure. Replacing a confident wrong number was the actual win; the error
message was the easy part.

`survey_extensions` is deliberately separate from `analyze_repository`,
which prunes everything it cannot read — that pruning discards exactly the
information needed to tell the user what they have.

## D28 — F10's duplicated language list is guarded, not fetched

**Date:** 2026-09-04 · **Decided by:** Claude Opus 5 (session `3d35d767`)

The scanner states the supported languages before any request is made, so
fetching them would mean either a request the page does not otherwise need
or a loading state on static copy. The list is duplicated in
`frontend/src/lib/languages.ts` instead.

Duplication of a promise drifts, so a backend test parses the TypeScript
file and fails when the two lists disagree. The guard was verified by making
them disagree and watching it fail, rather than assumed to work. A second
test asserts every advertised language maps to an extension the walker
actually collects, so the promise cannot outrun the scanner.

## D29 — F1 uses the `.dark` class, not a `[data-theme]` attribute

**Date:** 2026-09-04 · **Decided by:** Claude Opus 5 (session `3d35d767`)

The audit left this open and it had to be settled before writing anything,
because retrofitting the other choice touches every file again.

`.dark` wins on evidence already in the repository: `tailwind.config.ts`
declares `darkMode: ["class"]`, and every component under `components/ui`
was generated against that convention. Choosing the attribute would have
meant changing that config and silently breaking any shadcn snippet copied
in later.

`:root` is light and `.dark` is dark, the same way round as shadcn, so a
component pasted from their docs works unmodified.

Two consequences worth recording. The pre-paint script is a same-origin
file, not an inline `<script>`, because the Caddyfile's CSP is
`script-src 'self'` and its comment says that directive must not gain an
`'unsafe-inline'` — a separate file needs no CSP change at all. And every
`localStorage` access is wrapped, because reading it *throws* in some
privacy modes and a browser that refuses storage must still render.

## D30 — A contrast audit that reads the stylesheet

**Date:** 2026-09-04 · **Decided by:** Claude Opus 5 (session `3d35d767`)

The dark palette was tuned over months of looking at it. The light palette
was written in one sitting, which is the exact situation that ships a
foreground nobody else can read.

So F1 also added a test that parses `index.css` and checks 24 real token
pairs per theme against WCAG 2.1 — reading the stylesheet rather than
hardcoding the values, so a token edited without a matching edit to the test
is still checked against what is actually on screen.

It immediately found a defect in the *shipped* dark theme, not the new one:
`--destructive-foreground` on `--destructive` was 3.91:1, below AA for text,
on every destructive button in the app. Fixing it needed both white text and
two points off the red's lightness — 4.60:1 for the text while holding
3.87:1 for the red against the card, which `text-destructive` needs.
Darkening further fixes the button and starts failing the text.

## D31 — B2 and B5 were both investigated before being changed

**Date:** 2026-09-04 · **Decided by:** Claude Opus 5 (session `3d35d767`)

Both backlog items named a number and asked for it to move. Measuring first
changed what the right move was in both cases.

**B2** asked why the analyzer reported 34 where radon reported 58. The two
functions the audit named disagreed in *opposite* directions: ours was 25
low on `review_repository` (comprehensions and ternaries never counted) and
7 high on `analyze_dependencies` (a nested function's branches charged to
its parent). Fixing only the first would have made the second worse. Both
now match radon exactly.

The comprehension omission came from a comment that justified skipping them
to protect *nesting depth* — a different metric sharing the same visitor.
The two are now separated, and there is a test pinning the O(n^2) false
positive that comment was written to fix.

**B5** asked to raise the 35% duplicate threshold. The literal was 30, its
comment said "> 30%" while the code said ">= 30", and — measured — 30 was
already too high to ever fire: across two real repositories the highest pair
of any two files scored 19%, so the detector had never reported a block
duplicate at all. The floor moved *down*, to 15%, which admits the one real
copy-pasted pair and excludes the shadcn boilerplate below it.

The general lesson, recorded because it applies to the rest of the backlog:
an audit item that names a number is a hypothesis, not a finding. Two of the
five checked this session had their premise backwards.

## D32 — B1 was deliberately not started

**Date:** 2026-09-04 · **Decided by:** Claude Opus 5 (session `3d35d767`)

`analyze_dependencies` and `review_repository` measure 385 and 398 lines.
Decomposing them is a restructure, which `CONSTRAINTS.md` section 16
requires additive checkpoints for, and these two functions produce the
entire report shape — a subtle regression in either would be invisible to a
green suite, which is exactly the failure mode this project has hit before.

Started-and-abandoned is worse than not started: it leaves the codebase
mid-restructure with no checkpoint to return to. Left whole, with the
complexity numbers now accurate enough (D31) to judge the result against.

## D33 — B1 was gated on byte-identical output, not on the test suite

**Date:** 2026-09-05 · **Decided by:** Claude Opus 5 (session `81d7c85b`)

D32 left B1 unstarted because a subtle regression in either function would be
invisible to a green suite. That is still true — 517 passing tests do not
prove a report is unchanged. So the decomposition was gated on something
else: a harness that runs both functions over a frozen `git archive` export of
`ff69de2` (247 files), with the two network calls stubbed, and compares
5,429,674 bytes of canonical JSON. Each of the five extraction commits had to
leave `cmp` clean.

That gate is what made the restructure safe enough to do at all, and it earned
its keep immediately: it is what proved the `vulns` control-flow move in
`f4e26a6` was faithful, and what proved the removed `in_degrees` accumulation
in `d9c6e76` was genuinely dead rather than merely unread by the tests.

**The gate was wrong on its first design and that is the transferable part.**
It scanned the live working tree, so adding one test file to this branch moved
`file_reports` from 247 to 248 and the comparison failed for a reason with
nothing to do with the refactor. A regression gate whose target moves while
the code moves proves nothing in either direction — it can fail on innocent
changes and, worse, a compensating change could make it pass on a real
regression. Freeze the input.

Two limits, recorded so nobody over-reads the guarantee:

* The harness serialises with `sort_keys`, so it cannot see a key **order**
  change. Insertion order for `repository_summary` and the top-level report
  was checked separately.
* Byte-identical over one 247-file Python repository is not byte-identical
  over every repository. The manifest parsers in particular are exercised by
  whatever manifests this repo happens to have; `test_b1_contract.py` covers
  all six parsers precisely because the gate cannot.

## D34 — The rate limiter degrades to in-process rather than failing open or closed

**Date:** 2026-09-08 · **Decided by:** Claude Opus 5 (session `9b583d0e`)

S10 moved the rate-limit window into the Redis the stack already runs for
Celery, because the in-process counter grants the full budget once per
replica — the effective limit was N x `RATE_LIMIT_PER_MINUTE`, silently.

The real decision was not the store, it was what happens when Redis is
unreachable. Three options, all defensible:

* **Fail open** — allow the request. Rejected: this deletes the control during
  exactly the incident where shedding load matters, and `/scan` runs
  `git clone` on request. A Redis blip would restore the unlimited-clone
  surface Phase A closed.
* **Fail closed** — 429 everything. Rejected: it converts a broker hiccup into
  a total API outage. Redis is already a hard dependency of the queue, so this
  is arguable, but the queue degrades to eager mode rather than refusing, and
  the limiter should not be stricter than the thing it protects.
* **Degrade to the in-process window** — chosen. The limit stays real, just
  per-replica instead of global, which is precisely the pre-S10 behaviour and
  was considered acceptable then. The code costs nothing extra because the
  in-process store is kept anyway for single-container deployments.

Two details fell out of testing rather than design, and both were bugs the
first test run caught:

* Without a cool-down, every request during an outage re-paid the connect
  timeout — measured at ~1s per call against a closed port — and wrote one log
  line each. A dead Redis would have added a second of latency to every
  request and flooded the log. A 30s cool-down makes an outage one probe per
  30s and one warning per outage.
* `/health` reporting must **ping**. Constructing a redis-py client performs no
  I/O — `register_script` only computes a SHA locally — so a client pointed at
  a dead server looks healthy until the first real command. Reporting `redis`
  there would have made `/health` assert the exact thing an operator is
  checking for.

The window is a sorted set consumed by one Lua script, not `INCR`/`EXPIRE`.
A fixed window admits a double burst across the boundary and cannot produce a
true time-to-oldest-hit for `Retry-After`; the ZSET preserves the sliding
window the three existing `api_guard` tests already assert, so they were not
touched. The script's member is unique per hit rather than the timestamp,
because a sorted set is a set and two hits in one millisecond would otherwise
collapse into one element and undercount.

**What is and is not verified here.** This machine has no Docker, no
`redis-server` and no WSL distribution, so the Redis path could not be run
locally at all. It is tested by a `redis:7-alpine` service container added to
the CI backend job, in a step that greps for a nonzero passed count — an
all-skipped run exits 0, so the exit code alone could not distinguish "the
limiter works" from "nothing ran". The local half of the suite covers the
in-process store, the fallback, and the delegation.
