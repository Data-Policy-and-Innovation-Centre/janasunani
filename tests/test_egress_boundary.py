"""Only `janasunani/egress/` may talk to a third party (#83, ROADMAP §5.5).

The egress rule is the strongest safety claim in the repo: exactly one module
is permitted to send citizen data outside DPIC-controlled systems, so that the
audit log, the kill switch and the governance gate cannot be bypassed by a
second HTTP client appearing somewhere else.

It was previously asserted in a PR body as a "CI guard" that did not exist.
This is the guard. It is deliberately structural, not a grep for a provider
name: adding a new HTTP client anywhere in the package fails here and forces
whoever added it to either move it under `egress/` or justify the exception by
editing the allowlist below in the same diff.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "janasunani"

HTTP_MODULES = {"httpx", "requests", "aiohttp", "urllib3", "http.client"}

# Modules permitted to construct an HTTP client, and why.
#
# egress/sarvam.py  - the sole authorized-external client. Carries citizen
#                     document bytes to a third party, behind the governance
#                     gate and the per-call audit log.
# ingestion/*       - DPIC-controlled endpoints only: the Janasunani source
#                     API and S3. Not a third party, so not egress.
ALLOWED = {
    "egress/sarvam.py",
    "ingestion/client.py",
    "ingestion/document_ingestion.py",
}


def _http_importers() -> set[str]:
    found: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root in HTTP_MODULES or name in HTTP_MODULES:
                    found.add(path.relative_to(PACKAGE).as_posix())
    return found


def test_only_allowlisted_modules_construct_http_clients():
    """A new HTTP client anywhere else is a new way out of the building."""
    unexpected = _http_importers() - ALLOWED
    assert unexpected == set(), (
        "these modules import an HTTP client but are not on the egress "
        f"allowlist: {sorted(unexpected)}. Move the call under "
        "janasunani/egress/, or add it to ALLOWED with a comment saying which "
        "DPIC-controlled endpoint it talks to."
    )


def test_allowlist_has_no_stale_entries():
    """A stale allowlist quietly widens the boundary it is meant to hold."""
    importers = _http_importers()
    stale = {name for name in ALLOWED if not (PACKAGE / name).exists()}
    assert stale == set(), f"allowlisted modules no longer exist: {sorted(stale)}"

    unused = ALLOWED - importers
    assert unused == set(), (
        f"allowlisted modules no longer import an HTTP client: {sorted(unused)}. "
        "Remove them so the allowlist keeps meaning what it says."
    )


def test_the_provider_endpoint_is_only_named_in_egress():
    """The base URL is the thing a stray client would need. Keep it in one place."""
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path.relative_to(PACKAGE).parts[0] == "egress":
            continue
        if "api.sarvam.ai" in path.read_text(encoding="utf-8"):
            offenders.append(path.relative_to(PACKAGE).as_posix())
    assert offenders == [], (
        f"the Sarvam endpoint is referenced outside egress/: {offenders}"
    )
