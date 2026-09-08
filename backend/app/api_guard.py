# ==========================================================
# File: api_guard.py
# Purpose: HTTP-boundary security controls for the FastAPI surface
#          (Phase A — security & config hardening).
# ==========================================================
#
# The July audit scored the analysis engine 8/10 and the service around it 4/10:
# any caller who could reach the API could trigger unlimited `git clone` of
# arbitrary URLs, from any origin, at any rate. This module holds the four
# controls that close that gap, kept together so the whole trust boundary is
# readable in one file rather than scattered through route bodies.
#
# ENV (all read at CALL time, never at import time):
#   API_KEY               If set, protected routes require a matching X-API-Key
#                         header. If UNSET the API is open — local-dev mode.
#                         /health reports which state is live, so an operator can
#                         see at a glance that a deployment is unauthenticated.
#   ALLOWED_ORIGINS       Comma-separated CORS origins. Default: local dev hosts.
#   ALLOWED_GIT_HOSTS     Comma-separated hostnames that may be cloned.
#                         Setting it REPLACES the defaults (an explicit
#                         allowlist means exactly what it says).
#   RATE_LIMIT_PER_MINUTE Requests per minute per client per route. Default 60.
#   REDIS_URL             If set, the rate-limit window is kept in Redis and the
#                         budget is shared by every API replica. If UNSET the
#                         window is process-local, which is correct for a
#                         single container and N times too generous across N.
#                         /health reports which store is live. See
#                         rate_limit_store.py.
#
# Reading env at call time is a deliberate testability constraint: it lets the
# suite monkeypatch os.environ without reloading `main`. celery_app.py reads its
# config at import time and is correspondingly painful to test — don't copy it.

import ipaddress
import os
from urllib.parse import urlparse

from backend.app import rate_limit_store

# Routes that must stay reachable without a key. Uptime monitors and container
# healthchecks cannot send a secret, and the OpenAPI docs are harmless.
PUBLIC_PATHS = frozenset({
    "/", "/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect",
})

DEFAULT_GIT_HOSTS = ("github.com", "gitlab.com", "bitbucket.org")

DEFAULT_ORIGINS = (
    "http://localhost:8080",    # Vite dev server (vite.config.ts)
    "http://localhost:5173",    # Vite default, in case the port is changed back
    "http://127.0.0.1:8080",
    "http://127.0.0.1:5173",
)

# git clone targets are short; anything longer is a probe or an accident.
MAX_REPO_URL_LENGTH = 512

DEFAULT_RATE_LIMIT_PER_MINUTE = 60
_RATE_WINDOW_SECONDS = 60.0


# ----------------------------------------------------------
# S1 — API key authentication
# ----------------------------------------------------------

def configured_api_key() -> str:
    return os.getenv("API_KEY", "").strip()


def auth_enabled() -> bool:
    return bool(configured_api_key())


def is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS


def is_stream_path(path: str) -> bool:
    """True for the SSE progress stream, the one route that may take a query key.

    The browser's EventSource API cannot set request headers at all, so an
    authenticated deployment would have no way to open the live-progress stream
    if X-API-Key were the only accepted channel. The fallback is scoped to this
    single route rather than allowed everywhere because query strings are logged
    by proxies and land in browser history — a key there is far less private
    than one in a header.
    """
    return path.startswith("/scan/") and path.endswith("/stream")


def api_key_ok(supplied) -> bool:
    """Constant-time comparison of the supplied key against the configured one.

    Returns True when no key is configured — that is local-dev mode, and the
    caller (the auth middleware) decides whether that is acceptable.
    """
    expected = configured_api_key()
    if not expected:
        return True
    if not supplied:
        return False
    # hmac.compare_digest is the stdlib constant-time comparison; a plain ==
    # leaks key length and prefix through timing.
    import hmac
    return hmac.compare_digest(str(supplied), expected)


# ----------------------------------------------------------
# S2 — Repository URL validation (SSRF / local-file / DoS surface)
# ----------------------------------------------------------

class RepoUrlError(ValueError):
    """Raised when a submitted repository URL is not safe to clone."""


def allowed_git_hosts(raw=None):
    if raw is None:
        raw = os.getenv("ALLOWED_GIT_HOSTS", "")
    hosts = [h.strip().lower() for h in raw.split(",") if h.strip()]
    return hosts or list(DEFAULT_GIT_HOSTS)


def _is_private_host(hostname: str) -> bool:
    """True if the hostname is a literal IP in a range we must never fetch.

    The host allowlist already blocks these, but an operator who widens
    ALLOWED_GIT_HOSTS should not thereby expose the cloud metadata service
    (169.254.169.254) or the internal network. Defense in depth.
    """
    candidate = hostname.strip("[]")
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def validate_repo_url(url) -> str:
    """Return the URL if it is safe to hand to `git clone`, else raise.

    Only https:// on an allowlisted host is accepted. That single rule kills the
    whole class the audit flagged: file:// reads local disk, ssh:// and the
    git@host:path scp form reach for on-disk keys, and http:// to a private
    address turns the scanner into an internal port scanner.
    """
    if not isinstance(url, str):
        raise RepoUrlError("Repository URL must be a string.")

    candidate = url.strip()

    if not candidate:
        raise RepoUrlError("Repository URL must not be empty.")

    if len(candidate) > MAX_REPO_URL_LENGTH:
        raise RepoUrlError(
            f"Repository URL exceeds {MAX_REPO_URL_LENGTH} characters."
        )

    parsed = urlparse(candidate)

    if parsed.scheme.lower() != "https":
        raise RepoUrlError(
            "Repository URL must use https:// "
            "(ssh, git, http and file URLs are not accepted)."
        )

    if parsed.username or parsed.password:
        raise RepoUrlError("Credentials embedded in the URL are not accepted.")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise RepoUrlError("Repository URL has no host.")

    if _is_private_host(hostname):
        raise RepoUrlError("Repository URL points at a private or loopback address.")

    if hostname not in allowed_git_hosts():
        raise RepoUrlError(
            f"Host '{hostname}' is not an allowed git host. "
            f"Allowed: {', '.join(allowed_git_hosts())}."
        )

    if not parsed.path.strip("/"):
        raise RepoUrlError("Repository URL must include an owner/repository path.")

    return candidate


# ----------------------------------------------------------
# S3 — Rate limiting
# ----------------------------------------------------------
#
# A sliding window per (route bucket, caller). THE POLICY LIVES HERE — which
# bucket a route spends from, how big the budget is, and what a exhausted
# budget means — while the storage behind it lives in rate_limit_store.py.
#
# That split is S10. The window used to be a process-local dict, which is
# exactly as accurate as a shared store on a single container and N times too
# generous across N replicas. Setting REDIS_URL now moves the window into the
# Redis the stack already runs for Celery, making the limit global; leaving it
# unset keeps the original in-process behaviour. A Redis outage degrades back
# to in-process rather than failing open or closed — see DECISIONS.md D34 and
# DEPLOYMENT.md.


def rate_limit_per_minute() -> int:
    raw = os.getenv("RATE_LIMIT_PER_MINUTE", "").strip()
    if not raw:
        return DEFAULT_RATE_LIMIT_PER_MINUTE
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_RATE_LIMIT_PER_MINUTE
    return value if value > 0 else DEFAULT_RATE_LIMIT_PER_MINUTE


def reset_rate_limiter():
    """Clear all buckets. Used by tests; harmless in production."""
    rate_limit_store.reset()


def rate_limit_backend() -> str:
    """'redis' or 'in-process' — which store is actually serving the limit.

    Reported by /health so a deployment that has silently degraded to a
    per-replica limit is visible rather than merely documented.
    """
    return rate_limit_store.active_backend()


def check_rate_limit(bucket: str, client: str):
    """Consume one unit from (bucket, client). Returns retry-after seconds if
    the budget is exhausted, or None if the request may proceed.

    Buckets are per-route so exhausting the expensive /scan budget does not lock
    an operator out of unrelated endpoints.
    """
    return rate_limit_store.consume(
        bucket, client, rate_limit_per_minute(), _RATE_WINDOW_SECONDS
    )


def client_identity(request) -> str:
    """Best-effort caller identity for rate limiting.

    Behind Caddy/nginx the socket peer is the proxy, so X-Forwarded-For is
    honoured — but ONLY the first hop, and only because the documented
    deployment puts a trusted reverse proxy in front. Exposing this app
    directly to the internet would make the header spoofable.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return getattr(getattr(request, "client", None), "host", "") or "unknown"


# ----------------------------------------------------------
# S4 — CORS origins from environment
# ----------------------------------------------------------

def allowed_origins(raw=None):
    """Parse ALLOWED_ORIGINS into an explicit origin list.

    Never returns "*". The previous config paired a wildcard with
    allow_credentials=True, which browsers reject outright and which would be a
    genuine hole the moment any cookie-based auth were added.
    """
    if raw is None:
        raw = os.getenv("ALLOWED_ORIGINS", "")
    origins = [o.strip() for o in raw.split(",") if o.strip() and o.strip() != "*"]
    return origins or list(DEFAULT_ORIGINS)
