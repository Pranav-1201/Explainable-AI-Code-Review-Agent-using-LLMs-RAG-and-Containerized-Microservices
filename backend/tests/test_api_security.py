"""
API-surface security tests (Phase A — security & config hardening).

These cover the hardening the July audit flagged as the project's weakest area:
the analysis engine was production-grade while the service wrapping it accepted
unauthenticated scans of arbitrary URLs from any origin, at any rate.

Every test here drives the REAL routes through Starlette's TestClient rather
than calling helpers directly, because the thing under test IS the wiring —
a dependency that exists but is not attached to a route protects nothing.

Env is read at CALL time (not import time) inside api_guard, which is what lets
these tests monkeypatch os.environ without reloading `main`. That is a
deliberate design constraint, not an accident: celery_app.py reads its config at
import time and is correspondingly painful to test.
"""

import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

# The LLM layer stays gated off — nothing here touches the network.
os.environ.pop("ENABLE_ANTHROPIC", None)
os.environ.pop("ANTHROPIC_API_KEY", None)


VALID_REPO = "https://github.com/psf/requests"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient on an isolated temp DB, with a clean rate-limit bucket.

    Auth is DISABLED by default here (API_KEY unset) so each test opts in to the
    behaviour it is asserting; tests that want auth set API_KEY themselves.
    """
    from fastapi.testclient import TestClient
    import backend.database.connection as connection
    import main
    from backend.app import api_guard

    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("RATE_LIMIT_PER_MINUTE", raising=False)
    # S10: the limiter now has a Redis backend. These tests assert the limiting
    # POLICY, not the store, so pin them to the in-process one — otherwise a
    # developer with REDIS_URL exported would silently run them against a
    # shared server and see counts left over from another process.
    monkeypatch.delenv("REDIS_URL", raising=False)
    api_guard.reset_rate_limiter()

    original = connection.DB_PATH
    connection.DB_PATH = tmp_path / "reviews.db"
    connection.init_db()
    try:
        with TestClient(main.app) as c:
            yield c, main
    finally:
        connection.DB_PATH = original
        api_guard.reset_rate_limiter()


@pytest.fixture()
def no_clone(monkeypatch):
    """Stub the scan pipeline so a POST /scan never actually clones anything."""
    import main

    calls = []

    def fake_pipeline(scan_id, repo_url, explanation_depth="senior"):
        calls.append({"scan_id": scan_id, "repo_url": repo_url,
                      "depth": explanation_depth})

    monkeypatch.setattr(main, "run_scan_pipeline", fake_pipeline)
    return calls


# ----------------------------------------------------------
# S1 — API key authentication
# ----------------------------------------------------------

def test_scan_rejects_request_without_api_key_when_key_configured(
        client, no_clone, monkeypatch):
    """With API_KEY set, an unauthenticated POST /scan must be refused.

    Would fail if: the api_key dependency is missing from the /scan route, so
    anyone reaching the API can spend the box's CPU and disk cloning repos.
    """
    c, _ = client
    monkeypatch.setenv("API_KEY", "secret-key")

    r = c.post("/scan", json={"repo_path": VALID_REPO})

    assert r.status_code == 401, r.text
    assert not no_clone, "pipeline ran despite auth failure"


def test_scan_rejects_wrong_api_key(client, no_clone, monkeypatch):
    c, _ = client
    monkeypatch.setenv("API_KEY", "secret-key")

    r = c.post("/scan", json={"repo_path": VALID_REPO},
               headers={"X-API-Key": "wrong-key"})

    assert r.status_code == 401, r.text
    assert not no_clone


def test_scan_accepts_correct_api_key(client, no_clone, monkeypatch):
    c, _ = client
    monkeypatch.setenv("API_KEY", "secret-key")

    r = c.post("/scan", json={"repo_path": VALID_REPO},
               headers={"X-API-Key": "secret-key"})

    assert r.status_code == 200, r.text
    assert "scan_id" in r.json()
    assert len(no_clone) == 1


def test_routes_stay_open_when_no_api_key_configured(client, no_clone):
    """Unset API_KEY = local dev mode: the API behaves exactly as before.

    This is what keeps the pre-existing suite green, and it is why /health
    reports the auth state — an operator must be able to see that a deployed
    instance is running unauthenticated.
    """
    c, _ = client

    r = c.post("/scan", json={"repo_path": VALID_REPO})

    assert r.status_code == 200, r.text
    assert len(no_clone) == 1


def test_health_is_reachable_without_api_key(client, monkeypatch):
    """Uptime monitors and container healthchecks cannot send a secret."""
    c, _ = client
    monkeypatch.setenv("API_KEY", "secret-key")

    assert c.get("/health").status_code == 200
    assert c.get("/").status_code == 200


def test_sse_stream_accepts_api_key_as_query_parameter(client, monkeypatch):
    """EventSource cannot set request headers, so the key may ride the query.

    Without this the live-progress stream is unusable the moment auth is turned
    on — the browser API simply has no way to send X-API-Key. Restricted to a
    query fallback rather than a general one because URLs land in access logs;
    see api_guard.api_key_ok callers.

    Would fail if: the auth middleware only ever reads the header, silently
    breaking SSE on every authenticated deployment.
    """
    c, _ = client
    monkeypatch.setenv("API_KEY", "secret-key")

    denied = c.get("/scan/does-not-exist/stream")
    assert denied.status_code == 401

    ok = c.get("/scan/does-not-exist/stream", params={"api_key": "secret-key"})
    assert ok.status_code == 200, ok.text


def test_sse_stream_rejects_wrong_query_api_key(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("API_KEY", "secret-key")

    r = c.get("/scan/does-not-exist/stream", params={"api_key": "nope"})
    assert r.status_code == 401


def test_read_routes_are_protected_when_key_configured(client, monkeypatch):
    """Scan results are the product; they must not be world-readable."""
    c, _ = client
    monkeypatch.setenv("API_KEY", "secret-key")

    assert c.get("/scans").status_code == 401
    assert c.get("/settings").status_code == 401


# ----------------------------------------------------------
# S2 — Repository URL validation (SSRF / local-file / DoS surface)
# ----------------------------------------------------------

@pytest.mark.parametrize("bad_url", [
    "file:///etc/passwd",
    "file://C:/Windows/System32",
    "git@github.com:psf/requests.git",          # ssh form — not fetchable safely
    "ssh://git@github.com/psf/requests.git",
    "http://127.0.0.1:8000/x.git",              # loopback
    "http://localhost/x.git",
    "http://192.168.1.10/x.git",                # RFC1918
    "http://10.0.0.5/x.git",
    "http://172.16.0.1/x.git",
    "http://169.254.169.254/latest/meta-data",  # cloud metadata service
    "http://[::1]/x.git",                       # IPv6 loopback
    "https://evil.example.com/repo.git",        # host not on the allowlist
    "some/repo",                                # bare path — never a clone target
    "",
])
def test_scan_rejects_unsafe_repo_url(client, no_clone, bad_url):
    """Anything that is not an https URL on an allowlisted git host is refused.

    Would fail if: repo_path flows unvalidated into `git clone`, which is how
    the service could be made to read local files or probe internal hosts.
    """
    c, _ = client

    r = c.post("/scan", json={"repo_path": bad_url})

    assert r.status_code == 422, f"{bad_url!r} was accepted: {r.text}"
    assert not no_clone, f"pipeline ran for rejected URL {bad_url!r}"


def test_scan_rejects_overlong_repo_url(client, no_clone):
    c, _ = client
    long_url = "https://github.com/psf/" + ("a" * 1000)

    r = c.post("/scan", json={"repo_path": long_url})

    assert r.status_code == 422, r.text
    assert not no_clone


@pytest.mark.parametrize("good_url", [
    "https://github.com/psf/requests",
    "https://github.com/psf/requests.git",
    "https://gitlab.com/group/project",
    "https://bitbucket.org/team/repo.git",
])
def test_scan_accepts_allowlisted_https_urls(client, no_clone, good_url):
    c, _ = client

    r = c.post("/scan", json={"repo_path": good_url})

    assert r.status_code == 200, r.text
    assert no_clone[-1]["repo_url"] == good_url


def test_allowed_git_hosts_are_configurable(client, no_clone, monkeypatch):
    """A self-hosted GitLab must be reachable without patching source."""
    c, _ = client
    monkeypatch.setenv("ALLOWED_GIT_HOSTS", "git.internal.example.com")

    ok = c.post("/scan", json={"repo_path": "https://git.internal.example.com/a/b"})
    assert ok.status_code == 200, ok.text

    # The default hosts are REPLACED, not extended — an explicit allowlist means
    # exactly what it says.
    denied = c.post("/scan", json={"repo_path": "https://github.com/psf/requests"})
    assert denied.status_code == 422, denied.text


# ----------------------------------------------------------
# S3 — Rate limiting
# ----------------------------------------------------------

def test_scan_is_rate_limited(client, no_clone, monkeypatch):
    """Past the per-minute budget the API must shed load with 429.

    Would fail if: `while true; curl -XPOST /scan` can fill the disk with clones.
    """
    from backend.app import api_guard

    c, _ = client
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")
    api_guard.reset_rate_limiter()

    codes = [c.post("/scan", json={"repo_path": VALID_REPO}).status_code
             for _ in range(4)]

    assert codes[:3] == [200, 200, 200], codes
    assert codes[3] == 429, codes
    assert len(no_clone) == 3, "a rate-limited request still reached the pipeline"


def test_rate_limit_buckets_are_per_route(client, no_clone, monkeypatch):
    """Exhausting /scan must not lock the operator out of unrelated routes."""
    from backend.app import api_guard

    c, _ = client
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    api_guard.reset_rate_limiter()

    assert c.post("/scan", json={"repo_path": VALID_REPO}).status_code == 200
    assert c.post("/scan", json={"repo_path": VALID_REPO}).status_code == 429

    # Different route, its own budget.
    r = c.post("/feedback",
               json={"review_id": 1, "finding_key": "a.py:1:eval", "vote": "up"})
    assert r.status_code == 200, r.text


def test_rate_limited_response_advertises_retry_after(client, no_clone, monkeypatch):
    from backend.app import api_guard

    c, _ = client
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    api_guard.reset_rate_limiter()

    c.post("/scan", json={"repo_path": VALID_REPO})
    r = c.post("/scan", json={"repo_path": VALID_REPO})

    assert r.status_code == 429
    assert "retry-after" in {k.lower() for k in r.headers}


# ----------------------------------------------------------
# B1 — /health
# ----------------------------------------------------------

def test_health_reports_component_status(client):
    """Load balancers, compose healthchecks and uptime monitors all need this."""
    c, _ = client

    r = c.get("/health")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert "version" in body
    # Eager mode (no CELERY_BROKER_URL in the suite) must be reported honestly
    # rather than claimed as a healthy broker.
    assert body["queue"] == "eager"
    assert body["auth"] == "disabled"
    # S10: which store is actually serving the rate limit. "in-process" on a
    # multi-replica deployment means the budget is being granted N times over,
    # and that is otherwise invisible to an operator.
    assert body["rate_limit"] == "in-process"


def test_health_reports_auth_enabled_when_key_set(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("API_KEY", "secret-key")

    assert c.get("/health").json()["auth"] == "enabled"


# ----------------------------------------------------------
# S4 — CORS from environment
# ----------------------------------------------------------

def test_allowed_origins_parses_env_list():
    from backend.app import api_guard

    assert api_guard.allowed_origins("https://a.com, https://b.com") == [
        "https://a.com", "https://b.com"]


def test_allowed_origins_defaults_to_local_dev_hosts():
    from backend.app import api_guard

    defaults = api_guard.allowed_origins("")

    assert "http://localhost:8080" in defaults
    assert "*" not in defaults, "wildcard CORS is the footgun this replaces"


def test_cors_echoes_only_configured_origin(client):
    c, _ = client

    allowed = c.get("/health", headers={"Origin": "http://localhost:8080"})
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:8080"

    denied = c.get("/health", headers={"Origin": "https://evil.example.com"})
    assert denied.headers.get("access-control-allow-origin") != "https://evil.example.com"


# ----------------------------------------------------------
# S5 — GitHub webhook removed
# ----------------------------------------------------------

def test_github_webhook_route_is_removed(client):
    """The route verified nothing and called a known no-op engine (audit Defect
    E: it fed diff text to ast.parse, which always failed silently). Until
    Phase G rebuilds PR review properly it is pure attack surface, so it is gone.
    """
    c, _ = client

    r = c.post("/github-webhook", json={"action": "opened"})

    assert r.status_code == 404, r.text


def _vite_config_text():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    return (root / "frontend" / "vite.config.ts").read_text(encoding="utf-8")


def test_vite_dev_port_is_in_the_cors_allowlist():
    """The dev server's port and the backend's CORS allowlist must agree.

    They are two files in two languages and nothing tied them together. The
    moment they disagree, the browser's Origin stops matching the allowlist and
    every preflight returns 400 "Disallowed CORS origin" - which reaches the
    user as "the site cannot reach the backend", while the uvicorn log shows
    nothing but a bare 400 with no reason in it. Pin the pair here so changing
    the port breaks a test instead of the app.
    """
    import re
    from backend.app import api_guard

    match = re.search(r"port:\s*(\d+)", _vite_config_text())
    assert match, "no server.port found in vite.config.ts"
    port = match.group(1)

    defaults = api_guard.allowed_origins("")
    assert f"http://localhost:{port}" in defaults, defaults
    assert f"http://127.0.0.1:{port}" in defaults, defaults


def test_vite_pins_its_port_so_the_origin_cannot_drift():
    """Without strictPort, an occupied 8080 makes Vite serve on 8081 silently.

    That is not a cosmetic difference. The page still loads, so the app looks
    up, but its Origin is no longer allowlisted and every API call dies at the
    preflight - and start.bat, which polls 8080 to decide when to open the
    browser, never sees the server at all. Failing loudly on a busy port is far
    better than a running app whose every backend call fails.
    """
    import re

    assert re.search(r"strictPort:\s*true", _vite_config_text()), (
        "vite.config.ts must set strictPort: true so the dev port cannot drift "
        "off the CORS allowlist"
    )


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
