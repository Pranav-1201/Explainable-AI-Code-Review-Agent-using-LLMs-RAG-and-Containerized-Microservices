# ==========================================================
# File: test_compose_contract.py
# Purpose: the compose file must actually pass the operator knobs that
#          DEPLOYMENT.md promises are supported. Documentation that describes a
#          variable the container never receives is worse than no documentation:
#          it sends the operator to debug the wrong subsystem.
# ==========================================================
#
# Why this exists: S10 shipped REDIS_URL documented as a supported `api`
# variable in both DEPLOYMENT.md's env table (marked supported for api) and
# .env.example (which tells the operator to set redis://redis:6379/2), but never
# added it to the api service's `environment:` block. An operator following the
# documented path exactly would set REDIS_URL in the host .env, redeploy, and
# the container would never see it. The limiter would stay in-process at N x the
# configured budget across N replicas -- the precise defect S10 exists to fix --
# while /health reported "in-process", pointing them at Redis connectivity that
# was never attempted. Every other operator-facing variable was plumbed; this
# one was missed, and no test could see it.
#
# The prod overlay's `api` service declares no `environment:` of its own, and
# DEPLOYMENT.md always layers `-f docker-compose.yml -f docker-compose.prod.yml`,
# so the base file is the single place this contract has to hold.
#
# Parsed as TEXT rather than with PyYAML deliberately: PyYAML is declared in
# neither requirements.txt nor requirements.lock, and CI installs the lock only
# (`pip install -r requirements.lock`). Importing yaml here would pass on a
# developer machine that has it transitively and fail at collection in CI.

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"

# Every variable DEPLOYMENT.md marks as supported for the `api` service.
# The worker is deliberately absent: it serves no HTTP surface, so the HTTP
# controls have nothing to guard there.
API_ENV_CONTRACT = (
    "API_KEY",
    "ALLOWED_ORIGINS",
    "ALLOWED_GIT_HOSTS",
    "RATE_LIMIT_PER_MINUTE",
    "REDIS_URL",
)


def _service_block(text, service):
    """The body of one top-level compose service.

    Services sit at two-space indent and their keys at four, so the block ends
    at the next two-space key or the next top-level key, whichever comes first.
    """
    lines = text.splitlines()

    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^  {re.escape(service)}:\s*$", line):
            start = i + 1
            break
    assert start is not None, f"no `{service}:` service in {COMPOSE.name}"

    body = []
    for line in lines[start:]:
        if re.match(r"^\S", line) or re.match(r"^  \S", line):
            break
        body.append(line)
    return "\n".join(body)


def test_api_service_passes_every_documented_operator_variable():
    """A variable documented for `api` must reach the api container."""
    block = _service_block(COMPOSE.read_text(encoding="utf-8"), "api")

    missing = [
        name for name in API_ENV_CONTRACT
        if not re.search(rf"^\s+{name}:", block, re.M)
    ]

    assert not missing, (
        f"{sorted(missing)} are documented for the api service but are not in "
        f"its environment block in {COMPOSE.name}. The container will never "
        f"receive them, so the documented deployment path silently does nothing."
    )


def test_documented_operator_variables_come_from_the_host_environment():
    """They must be `${VAR:-}` passthroughs, not values baked into the file.

    A hardcoded value would ignore the operator's .env just as completely as a
    missing line, and is the more confusing failure because the variable is
    visibly present.
    """
    block = _service_block(COMPOSE.read_text(encoding="utf-8"), "api")

    hardcoded = []
    for name in API_ENV_CONTRACT:
        match = re.search(rf"^\s+{name}:\s*(.+)$", block, re.M)
        if match and "${" not in match.group(1):
            hardcoded.append(f"{name} -> {match.group(1).strip()}")

    assert not hardcoded, (
        f"these api variables are documented as operator knobs but are pinned "
        f"in {COMPOSE.name} instead of read from the host environment: "
        f"{sorted(hardcoded)}"
    )
