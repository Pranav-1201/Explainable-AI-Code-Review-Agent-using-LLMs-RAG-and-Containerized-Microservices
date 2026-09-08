# ==========================================================
# File: main.py
# Purpose: Entry point for AI repository code review
# Supports CLI + FastAPI + background scanning
# ==========================================================

import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import subprocess
import tempfile
import hashlib
import asyncio
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

from backend.app import api_guard
from backend.app import disk_guard
from backend.app import observability

from backend.app.services.scan_manager import (
    create_scan,
    update_scan,
    complete_scan,
    get_scan,
    list_scans,
    recover_interrupted_scans,
)

from backend.app.services.repository_review_engine import RepositoryReviewEngine
from backend.app.analysis.dependency_graph import build_dependency_graph
from backend.app.analysis.call_graph import build_call_graph
from backend.app.services.repo_analyzer import (
    analyze_repository,
    describe_unsupported,
    UnsupportedRepositoryError,
)
from backend.app.services import incremental
from backend.app.services.settings_manager import load_settings, save_settings, reset_settings
from backend.database.review_repository import record_feedback, get_precision_estimate

# Real job queue (Phase 6 / Chunk 5). run_scan_task.delay() enqueues a scan onto
# the Celery broker for a separate worker; with no CELERY_BROKER_URL configured
# it runs eagerly in-process (see celery_app.py), so this import is safe with or
# without a broker present.
from backend.app.services.celery_app import celery_app
from backend.app.services.tasks import run_scan_task

# Structured logging is configured before anything else so startup records
# are formatted too.
observability.configure_logging()
# After configure_logging, never before: Sentry's logging integration
# attaches to handlers that already exist.
observability.init_sentry()
logger = observability.get_logger("etproject.api")


# ----------------------------------------------------------
# FastAPI App
# ----------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Zombie-scan recovery (Phase 6 / Chunk 5). In EAGER mode there is no
    # separate worker — the API process runs scans itself — so any scan left
    # non-terminal by a previous process died with it and the API reconciles it
    # here, exactly as before the queue existed. In BROKER mode the worker is a
    # separate process that owns scan execution, so recovery moves to worker
    # startup (celery_app._recover_on_worker_ready); running it here too would
    # wrongly mark a scan a live worker is still processing as 'error' whenever
    # the API restarts. So this path is gated to eager mode only.
    if celery_app.conf.task_always_eager:
        try:
            recovered = recover_interrupted_scans()
            if recovered:
                print(f"[startup] recovered {recovered} interrupted scan(s) from a previous run")
        except Exception as e:  # never block startup on recovery
            print(f"[startup] scan recovery skipped: {e!r}")
    yield


app = FastAPI(
    title="AI Repository Code Review Agent",
    description="AI-powered repository analysis",
    version="1.0",
    lifespan=lifespan,
)

# ----------------------------------------------------------
# Middleware (Phase A — security hardening)
# ----------------------------------------------------------
#
# ORDER MATTERS. Starlette runs the LAST-added middleware outermost, so CORS is
# registered after auth on purpose: a 401 still comes back with CORS headers,
# which is what lets the browser surface "unauthorized" instead of an opaque
# network error.


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    """Reject unauthenticated calls when API_KEY is configured.

    Applied as middleware rather than a per-route dependency so a newly added
    route is protected by default — the failure mode of the dependency approach
    is a route that silently ships without one.

    CORS preflight (OPTIONS) is exempt: the browser sends it without custom
    headers by design, so requiring X-API-Key on it would break every
    cross-origin call before the real request was ever made.

    The SSE progress route additionally accepts ?api_key= because EventSource
    cannot send headers — see api_guard.is_stream_path for the tradeoff.
    """
    path = request.url.path
    supplied = request.headers.get("X-API-Key")
    if not supplied and api_guard.is_stream_path(path):
        supplied = request.query_params.get("api_key")

    if (request.method != "OPTIONS"
            and not api_guard.is_public_path(path)
            and not api_guard.api_key_ok(supplied)):
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing or invalid API key."},
        )
    return await call_next(request)


# Unlike the auth check above, this reads ALLOWED_ORIGINS exactly ONCE, at
# import: CORSMiddleware wants a fixed origin list at construction time. Changing
# the variable therefore needs a process restart, and monkeypatching it inside a
# test has no effect on the live middleware (test api_guard.allowed_origins
# directly instead).
app.add_middleware(
    CORSMiddleware,
    allow_origins=api_guard.allowed_origins(),
    # No cookie/session auth exists — the API key travels in a header — so
    # credentials are off. The previous "*" + allow_credentials=True pairing is
    # rejected by browsers anyway.
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
)


# ----------------------------------------------------------
# Request Models
# ----------------------------------------------------------

class RepoRequest(BaseModel):
    repo_path: str
    explanation_depth: str = "senior"   # PHASE 5: junior | senior


class FeedbackRequest(BaseModel):
    review_id: int
    finding_key: str        # identifies the finding, e.g. "file.py:42:eval"
    vote: str               # "up" (true positive) | "down" (false positive)


# ----------------------------------------------------------
# Core Pipeline
# ----------------------------------------------------------

def run_pipeline(repo_path: str, scan_id: str = None, explanation_depth: str = "senior",
                 since_sha: str = None, prior_files=None):

    if scan_id:
        update_scan(scan_id, "analyzing", 20,
                    stage="discovery", stage_detail="Scanning repository files...")

    print("Starting repository analysis...")

    # PHASE 6 (Chunk 5): when since_sha + prior_files are supplied, the engine
    # re-analyzes only git-diff-changed files and reuses the rest.
    files = analyze_repository(repo_path, since_sha=since_sha, prior_files=prior_files)

    print("Scanning repository at:", repo_path)
    print("Files found:", len(files))

    # B6: a repository we cannot read must say so, not score itself.
    # Without this the pipeline ran to completion on zero files and reported
    # health_score 45 — the composite defaults security and simplicity to 100
    # when there is nothing to measure, so an unanalysable repository came
    # back looking like a mediocre one. A confident wrong number is worse
    # than the generic error B6 asked to replace.
    if not files:
        raise UnsupportedRepositoryError(describe_unsupported(repo_path))

    if scan_id:
        update_scan(scan_id, "analyzing", 30,
                    stage="discovery", stage_detail=f"Found {len(files)} files",
                    total_files=len(files))

    # Prevent extremely large repos
    settings = load_settings()
    max_files = settings.get("analysis", {}).get("max_files", 2000)
    if len(files) > max_files:
        files = files[:max_files]

    if scan_id:
        update_scan(scan_id, "analyzing", 35,
                    stage="dependencies", stage_detail="Building dependency graph...")

    dependency_graph = build_dependency_graph(files)
    call_graph = build_call_graph(files)

    if scan_id:
        update_scan(scan_id, "analyzing", 40,
                    stage="analysis", stage_detail="Running AI code review...")

    print("Running AI repository review...")

    engine = RepositoryReviewEngine()
    result = engine.review_repository(repo_path, files, explanation_depth=explanation_depth)

    return {
        "repository_summary": result["repository_summary"],
        "file_reports": result["file_reports"],
        "issues": result["issues"],
        "dependencies": result["dependencies"],
        "duplicates": result["duplicates"],
        "visualizations": result["visualizations"],
        "insights": result.get("insights", {}),
        "dependency_graph": dependency_graph,
        "call_graph": dict(call_graph),
        # Internal: raw per-file analysis for the incremental prior-store.
        # run_scan_pipeline pops this before persisting the scan result.
        "_files_data": files,
    }


# ----------------------------------------------------------
# Background Scan Pipeline
# ----------------------------------------------------------

# Persistent per-repo clone cache. Unlike a throwaway shallow clone, a cached
# full clone lets a re-scan diff the previous commit against the new HEAD so
# only changed files are re-analyzed (PHASE 6 / Chunk 5). Tradeoff: disk grows
# with one clone per distinct repo_url (bounded by the number of repos scanned).
CLONE_CACHE = os.path.join(tempfile.gettempdir(), "etproject_clones")


def _usable_cached_clone(repo_dir: str) -> bool:
    """True only if git itself will accept repo_dir as a repository.

    `os.path.isdir(repo_dir/".git")` is a question about the filesystem, not
    about git, and the two disagree in exactly the case that bites: an eviction
    (disk_guard.force_rmtree) that deletes part of the tree and then stops
    leaves a .git holding objects/, refs/, logs/ and index but no HEAD and no
    config. That passes an isdir check; `git fetch` on it exits 128.

    The failure was permanent rather than transient — nothing cleared the
    broken directory, so every later scan of that repo took the same branch
    and died the same way, surfacing a raw CalledProcessError repr to the user.
    Asking git instead sends a broken cache down the full-clone branch, which
    already clears a stale directory before cloning.
    """
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        return False
    try:
        probe = subprocess.run(
            ["git", "-C", repo_dir, "rev-parse", "--git-dir"],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        # An unreadable or locked directory is not a cache worth trusting.
        return False
    return probe.returncode == 0


def run_scan_pipeline(scan_id: str, repo_url: str, explanation_depth: str = "senior"):

    with observability.scan_context(scan_id):
        _run_scan_pipeline(scan_id, repo_url, explanation_depth)


def _run_scan_pipeline(scan_id: str, repo_url: str, explanation_depth: str):

    os.makedirs(CLONE_CACHE, exist_ok=True)
    repo_dir = os.path.join(CLONE_CACHE, hashlib.md5(repo_url.encode("utf-8")).hexdigest())

    # Bound the disk before adding to it. `keep` is this scan's own key so a
    # sweep can never delete the cached clone this run is about to reuse.
    #
    # Best-effort on purpose. This call sits OUTSIDE the main try block below,
    # so an exception here would escape before complete_scan() ever runs and
    # strand the scan in a non-terminal state with no error surfaced to the
    # user -- unlike every other failure path in this function. Eviction is
    # housekeeping, not part of the scan's contract: if a directory is locked
    # for longer than force_rmtree's retry budget, log it and scan anyway.
    try:
        disk_guard.evict_caches(
            CLONE_CACHE,
            incremental.CACHE_DIR,
            keep=os.path.basename(repo_dir),
        )
    except Exception as e:
        logger.warning("cache eviction failed, continuing with the scan: %s", e)

    since_sha = None
    prior_files = None

    try:

        prior = incremental.load_prior(repo_url)

        if prior and _usable_cached_clone(repo_dir):
            # Re-scan: refresh the cached clone and diff against the last scan.
            update_scan(scan_id, "cloning", 5, stage="cloning",
                        stage_detail="Refreshing repository (incremental)...")
            # Defect G: per-invocation config via `git -c`, never global.
            subprocess.run(
                ["git", "-C", repo_dir, "-c", "http.postBuffer=524288000",
                 "fetch", "origin"],
                check=True, timeout=180,
            )
            subprocess.run(
                ["git", "-C", repo_dir, "reset", "--hard", "FETCH_HEAD"],
                check=True, timeout=60,
            )
            since_sha = prior.get("sha")
            prior_files = prior.get("files")
        else:
            # First scan (or cache miss): full clone WITH history so future
            # re-scans can diff. Not shallow (no --depth 1) for that reason.
            update_scan(scan_id, "cloning", 5, stage="cloning",
                        stage_detail="Cloning repository...")
            # clone_with_limit clears any stale/partial dir itself, using the
            # rmtree that copes with git's read-only packfiles — the plain
            # ignore_errors=True call this replaces could leave the directory
            # behind and make the clone fail with exit 128.
            disk_guard.clone_with_limit(repo_url, repo_dir, timeout=300)

        update_scan(scan_id, "analyzing", 15,
                    stage="cloning", stage_detail="Repository ready")

        result = run_pipeline(repo_dir, scan_id=scan_id,
                              explanation_depth=explanation_depth,
                              since_sha=since_sha, prior_files=prior_files)

        # Pop the internal per-file payload before persisting the scan result.
        files_data = result.pop("_files_data", None)

        update_scan(scan_id, "finalizing", 90,
                    stage="finalizing", stage_detail="Computing health score...")

        # Persist this scan's HEAD + per-file results for the next incremental run.
        new_sha = incremental.head_sha(repo_dir)
        incremental.save_prior(repo_url, new_sha, files_data)

        complete_scan(scan_id, result)

    except Exception as e:

        logger.exception("scan failed: %s", e)
        complete_scan(scan_id, {"error": str(e)})


# ----------------------------------------------------------
# API Routes
# ----------------------------------------------------------

@app.get("/")
def root():
    return {"message": "AI Code Review Agent Running"}


@app.get("/health")
def health():
    """Component-level liveness for load balancers, compose and uptime monitors.

    Reports what is actually true rather than a bare 200: `queue` distinguishes
    a real broker from the in-process eager fallback, and `auth` makes an
    accidentally-unauthenticated deployment visible instead of silent.
    """
    database = "ok"
    try:
        from backend.database.connection import get_connection
        conn = get_connection()
        conn.execute("SELECT 1").fetchone()
    except Exception as e:
        database = f"error: {e.__class__.__name__}"

    return {
        "status": "ok" if database == "ok" else "degraded",
        "version": app.version,
        "database": database,
        "queue": "eager" if celery_app.conf.task_always_eager else "broker",
        "auth": "enabled" if api_guard.auth_enabled() else "disabled",
        # "in-process" on a multi-replica deployment means the limit is being
        # applied N times over. That is invisible without this field.
        "rate_limit": api_guard.rate_limit_backend(),
    }


# Start Scan

@app.post("/scan")
def start_scan(payload: RepoRequest, request: Request):

    # Rate limit BEFORE validation so a flood of malformed URLs is shed just as
    # cheaply as a flood of valid ones.
    retry_after = api_guard.check_rate_limit("scan", api_guard.client_identity(request))
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many scan requests. Try again shortly.",
            headers={"Retry-After": str(retry_after)},
        )

    # A scan clones whatever it is given, so the URL is the trust boundary.
    try:
        repo_url = api_guard.validate_repo_url(payload.repo_path)
    except api_guard.RepoUrlError as e:
        raise HTTPException(status_code=422, detail=str(e))

    scan_id = create_scan(repo_url)

    # Enqueue onto the Celery broker for a separate worker. With no broker
    # configured this runs eagerly in-process (see celery_app.py), preserving
    # the previous single-process behaviour. The task defers to
    # run_scan_pipeline, so a monkeypatch of main.run_scan_pipeline is honoured.
    run_scan_task.delay(
        scan_id,
        repo_url,
        payload.explanation_depth,
    )

    return {"scan_id": scan_id}


# Get Scan Progress

@app.get("/scan/{scan_id}")
def scan_status(scan_id: str):

    scan = get_scan(scan_id)

    if not scan:
        return {"error": "Scan not found"}

    return scan


# Live Scan Progress via Server-Sent Events (Chunk 6 / Item E)
#
# Streams progress over a single long-lived connection instead of the frontend
# polling GET /scan/{id} every 2s. This endpoint is ADDITIVE — the polling
# endpoint above is unchanged and remains the fallback for clients without
# EventSource or behind a proxy that strips streaming responses.
#
# The scan store is SQLite (written by update_scan, possibly from a separate
# Celery worker), so there is no in-process event bus to subscribe to. The
# generator reads the store server-side on a tight interval and emits an SSE
# frame only when the observable state changes — far cheaper than a client HTTP
# round-trip per tick, with instant terminal delivery. Each frame's payload is
# the same dict shape GET /scan/{id} returns, so the client handles both paths
# identically.

_SSE_POLL_INTERVAL = 0.5       # seconds between server-side store reads
_SSE_HEARTBEAT_EVERY = 15.0    # seconds; comment ping keeps intermediaries open
_SSE_MAX_DURATION = 6 * 60     # hard cap so a hung scan can't hold a socket open


def _sse_frame(payload: dict) -> str:
    # One compact JSON line. json.dumps escapes embedded newlines, so the SSE
    # framing (data: <line>\n\n) is never broken by result content.
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _progress_signature(scan: dict) -> tuple:
    # The observable fields a client renders; used to emit only on real change.
    return (
        scan.get("status"),
        scan.get("progress"),
        scan.get("stage"),
        scan.get("stage_detail"),
        scan.get("files_processed"),
        scan.get("total_files"),
    )


async def _scan_event_stream(scan_id: str, request: Request):
    loop = asyncio.get_event_loop()
    started = loop.time()
    last_beat = started
    last_sig = None

    while True:
        # Stop promptly if the browser closed the tab / navigated away.
        if await request.is_disconnected():
            return

        # Offload the blocking SQLite read to a thread (same shared connection
        # the sync polling route already uses from Starlette's threadpool), so
        # the event loop is never blocked.
        scan = await asyncio.to_thread(get_scan, scan_id)

        if scan is None:
            yield _sse_frame({"status": "error", "error": "Scan not found"})
            return

        sig = _progress_signature(scan)
        if sig != last_sig:
            yield _sse_frame(scan)
            last_sig = sig
            last_beat = loop.time()

        # Terminal state was just emitted above — close the stream.
        if scan.get("status") in ("complete", "error"):
            return

        now = loop.time()
        if now - started > _SSE_MAX_DURATION:
            yield _sse_frame({
                "status": "error",
                "error": "Live progress stream exceeded its time limit",
            })
            return

        # Heartbeat comment during long silent stages so proxies keep the
        # connection open. EventSource ignores comment lines (leading ':').
        if now - last_beat > _SSE_HEARTBEAT_EVERY:
            yield ": keep-alive\n\n"
            last_beat = now

        await asyncio.sleep(_SSE_POLL_INTERVAL)


@app.get("/scan/{scan_id}/stream")
async def scan_status_stream(scan_id: str, request: Request):
    return StreamingResponse(
        _scan_event_stream(scan_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",   # disable proxy (nginx) response buffering
        },
    )


# Scan History (Fix K) — persisted list of past completed scans so the
# frontend history survives a page reload instead of living only in memory.

@app.get("/scans")
def scan_history(limit: int = 50):
    return {"scans": list_scans(limit)}


# ----------------------------------------------------------
# Settings API
# ----------------------------------------------------------

@app.get("/settings")
def get_settings():
    return load_settings()


@app.post("/settings")
def update_settings(settings: dict):
    saved = save_settings(settings)
    return {"status": "saved", "settings": saved}


@app.post("/settings/reset")
def reset_all_settings():
    defaults = reset_settings()
    return {"status": "reset", "settings": defaults}


# ----------------------------------------------------------
# Feedback API (Phase 5)
# Thumbs up/down on a finding, persisted to the DB, plus a
# running precision estimate (up / (up + down)).
# ----------------------------------------------------------

@app.post("/feedback")
def submit_feedback(feedback: FeedbackRequest, request: Request):
    retry_after = api_guard.check_rate_limit(
        "feedback", api_guard.client_identity(request))
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many feedback submissions. Try again shortly.",
            headers={"Retry-After": str(retry_after)},
        )

    try:
        feedback_id = record_feedback(
            feedback.review_id, feedback.finding_key, feedback.vote
        )
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    return {"status": "recorded", "feedback_id": feedback_id,
            "precision": get_precision_estimate()}


@app.get("/feedback/precision")
def feedback_precision():
    return get_precision_estimate()


# ----------------------------------------------------------
# GitHub Webhook — REMOVED in Phase A
# ----------------------------------------------------------
#
# `POST /github-webhook` used to accept any unsigned payload and hand it to
# pr_review_engine. It was removed rather than hardened because it was pure
# attack surface with zero function on both counts:
#
#   * no HMAC verification of X-Hub-Signature-256, so anyone could invoke it;
#   * raw payload["pull_request"] indexing, so a malformed body was a 500;
#   * the engine behind it is a known silent no-op (audit Defect E — it feeds
#     unified-diff text to ast.parse, which always raises, yielding zero
#     findings without reporting an error).
#
# Roadmap item G1 rebuilds PR review properly (GitHub App auth, full files at
# the head SHA, findings mapped to changed lines) and reintroduces the webhook
# WITH signature verification at that point. Until then there is nothing to
# protect, and an unauthenticated route into dead code is a liability.


# ----------------------------------------------------------
# CLI Entry Point
# ----------------------------------------------------------

if __name__ == "__main__":

    if len(sys.argv) > 1:
        repo_path = sys.argv[1]
    else:
        repo_path = "rag/data"

    result = run_pipeline(repo_path)

    print(result)