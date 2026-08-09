"""The routing seam: one Protocol, one env-gated factory, one status probe.

Routing today is the empirical crosswalk backed by the ORTPSA mapping tables
and a generic fallback (``rules.py``). A trained model is intended to replace
the first rung later. Nothing about that swap should require touching a call
site, so the selection happens here.

The Protocol is deliberately the one that already existed as the private
``_Router`` in ``janasunani/inference/service.py``. ``_LazyDefaultRouter``,
``MappingRouter`` and ``RuleRouter`` already satisfy it structurally, so this
declares an existing contract rather than imposing a new one.

The factory follows ``supervisor_provider_from_env``
(``janasunani/serving/intelligence.py``): read one environment variable,
return a working provider for every input including nonsense, and never raise.
A routing failure on demo day must degrade to a worse route, never to a 500.

Why ``router_status`` exists separately: ``PERFORMANCE.md`` recorded a live
submission returning ``method:"fallback"`` while the crosswalk artifact was
present and believed live. The only way to catch that before an audience does
is for preflight to say which rung is actually first, without loading the
models to find out.
"""

from __future__ import annotations

import os
from typing import Optional, Protocol

from loguru import logger

from janasunani.serving.schemas import RoutingResult

#: Environment variable selecting the routing implementation.
ROUTER_ENV_VAR = "JANASUNANI_ROUTER"

#: The shipped implementation: crosswalk, then mapping tables, then fallback.
ROUTER_DEFAULT = "crosswalk"

#: Rules and generic fallback only, skipping the empirical crosswalk. Useful to
#: reproduce the pre-#33 behaviour and to isolate the crosswalk in a comparison.
ROUTER_RULES = "rules"

SUPPORTED_ROUTERS = (ROUTER_DEFAULT, ROUTER_RULES)


class RoutingProvider(Protocol):
    """Anything that can turn a classified grievance into a route.

    A future learned router satisfies this by implementing ``route``. It must
    also honour the ``RoutingResult`` contract in
    ``janasunani/serving/schemas.py``, which enforces a biconditional between
    ``method`` and the evidence object: ``method="learned"`` requires
    ``empirical_evidence`` and every other method must not carry it. A model
    whose evidence is a probability rather than support-and-concentration needs
    a new evidence variant, not a reused one.
    """

    def route(
        self,
        *,
        category: str,
        subcategory: Optional[str] = None,
        district: Optional[str] = None,
    ) -> RoutingResult: ...


def router_from_env(value: str | None = None) -> RoutingProvider:
    """Select a router by environment, degrading to the default on anything odd.

    Unset, unknown, or unconstructable all return the shipped router. An
    operator typo must not take routing off the air, and it must not fail
    silently either, so the fallback logs.
    """
    from janasunani.routing.rules import DEFAULT_ROUTER, RuleRouter, _LazyDefaultRouter

    configured = (value if value is not None else os.environ.get(ROUTER_ENV_VAR, "")).strip().lower()
    if not configured:
        return DEFAULT_ROUTER
    if configured == ROUTER_DEFAULT:
        return DEFAULT_ROUTER
    if configured == ROUTER_RULES:
        try:
            return _LazyDefaultRouter(enable_crosswalk=False)
        except (OSError, RuntimeError, ValueError):
            logger.warning("{}={} could not be constructed; using the default router", ROUTER_ENV_VAR, configured)
            return RuleRouter()
    logger.warning(
        "{}={!r} is not one of {}; using the default router",
        ROUTER_ENV_VAR,
        configured,
        ", ".join(SUPPORTED_ROUTERS),
    )
    return DEFAULT_ROUTER


def router_status() -> tuple[str, bool, str]:
    """Report ``(name, ok, detail)`` naming the rung that will answer first.

    Deliberately cheap: it inspects the crosswalk artifact on disk rather than
    constructing the router, so preflight can run before any model is warm.
    ``ok`` is False only when the selection is degraded relative to what was
    configured, not when routing is merely unavailable-but-expected.
    """
    from janasunani.routing.crosswalk import DEFAULT_ARTIFACT

    configured = os.environ.get(ROUTER_ENV_VAR, "").strip().lower() or ROUTER_DEFAULT

    if configured == ROUTER_RULES:
        return (
            ROUTER_RULES,
            True,
            "crosswalk disabled by configuration; first rung is the ORTPSA mapping tables",
        )

    if configured not in SUPPORTED_ROUTERS:
        return (
            ROUTER_DEFAULT,
            False,
            f"{ROUTER_ENV_VAR}={configured!r} is unknown; falling back to the default router",
        )

    if DEFAULT_ARTIFACT.is_file():
        return (
            ROUTER_DEFAULT,
            True,
            f"crosswalk artifact present at {DEFAULT_ARTIFACT.name}; first rung is learned",
        )
    return (
        ROUTER_DEFAULT,
        False,
        "crosswalk artifact missing; routing degrades to mapping tables then fallback "
        "(run janasunani-build-crosswalk to restore method:learned)",
    )
