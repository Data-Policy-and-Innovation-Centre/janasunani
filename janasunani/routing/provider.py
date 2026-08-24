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
from pathlib import Path
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

#: Checksummed aggregate-only empirical-Bayes artifact. This predicts the
#: unconfirmed department snapshot recorded in complaints.dept, never initial
#: assignment intent, action-history traversal, routing correctness, disposal
#: quality, or citizen benefit.
ROUTER_INCIDENCE = "incidence"

ROUTING_INCIDENCE_ARTIFACT_NAME = "routing_incidence"

SUPPORTED_ROUTERS = (ROUTER_DEFAULT, ROUTER_RULES, ROUTER_INCIDENCE)


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


class IncidenceRoutingProvider:
    """Serve a governed local incidence artifact with a safe fallback ladder."""

    def __init__(self, router, fallback: RoutingProvider) -> None:
        self._router = router
        self._fallback = fallback

    def route(
        self,
        *,
        category: str,
        subcategory: Optional[str] = None,
        district: Optional[str] = None,
    ) -> RoutingResult:
        try:
            prediction = self._router.predict_with_evidence(
                category=category,
                subcategory=subcategory,
                district=district,
            )
            if prediction is not None:
                department = max(
                    prediction.probabilities,
                    key=prediction.probabilities.get,
                )
                district_name = " ".join((district or "State").split())
                from janasunani.serving.schemas import EmpiricalRoutingEvidence

                return RoutingResult(
                    dept=department,
                    office=f"{department}, {district_name}",
                    confidence=min(0.95, prediction.probabilities[department]),
                    method="learned",
                    empirical_evidence=EmpiricalRoutingEvidence(
                        support=prediction.support,
                        concentration=prediction.concentration,
                        width=prediction.width,
                    ),
                )
        except Exception:
            logger.warning(
                "incidence routing failed for an aggregate lookup; using the "
                "crosswalk/rules fallback"
            )
        return self._fallback.route(
            category=category,
            subcategory=subcategory,
            district=district,
        )


def _load_incidence_provider(
    *,
    models_dir: Path | None = None,
    verified_artifacts: dict[tuple[Path, str], Path] | None = None,
) -> IncidenceRoutingProvider | None:
    """Resolve and load the local artifact; never contact MLflow or a registry."""

    try:
        from janasunani.evaluation.routing import load_incidence_router
        from janasunani.routing.rules import DEFAULT_ROUTER
        from janasunani.tracking.artifacts import resolve_artifact

        artifact = resolve_artifact(
            ROUTING_INCIDENCE_ARTIFACT_NAME,
            models_dir=models_dir,
            verified_artifacts=verified_artifacts,
        )
        if artifact is None:
            return None
        router = load_incidence_router(artifact)
        if router is None:
            return None
        return IncidenceRoutingProvider(router, DEFAULT_ROUTER)
    except Exception:
        return None


def router_from_env(
    value: str | None = None,
    *,
    models_dir: Path | None = None,
    verified_artifacts: dict[tuple[Path, str], Path] | None = None,
) -> RoutingProvider:
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
    if configured == ROUTER_INCIDENCE:
        provider = _load_incidence_provider(
            models_dir=models_dir,
            verified_artifacts=verified_artifacts,
        )
        if provider is not None:
            return provider
        logger.warning(
            "{}={} has no valid local {} artifact; using the default router",
            ROUTER_ENV_VAR,
            configured,
            ROUTING_INCIDENCE_ARTIFACT_NAME,
        )
        return DEFAULT_ROUTER
    logger.warning(
        "{}={!r} is not one of {}; using the default router",
        ROUTER_ENV_VAR,
        configured,
        ", ".join(SUPPORTED_ROUTERS),
    )
    return DEFAULT_ROUTER


def router_status(
    *,
    models_dir: Path | None = None,
    verified_artifacts: dict[tuple[Path, str], Path] | None = None,
) -> tuple[str, bool, str]:
    """Report ``(name, ok, detail)`` naming the rung that will answer first.

    Validates by **loading** the crosswalk, not by checking that a file
    exists. Those differ exactly where it matters: ``load_crosswalk`` returns
    ``None`` for a file that is present but corrupt or structurally invalid,
    and the router then falls through to the mapping tables. A presence check
    would report "first rung is learned" for precisely that file, which is the
    same false assurance this probe was added to prevent. Reproducing the bug
    inside the check for it would be worse than having no check.

    Loading successfully is still not sufficient: a crosswalk whose four
    tables are all structurally valid but empty loads without error (an empty
    dict is a valid, if useless, table -- see ``_valid_table``), yet every
    lookup misses and routing falls straight through to the mapping tables.
    That is the same false assurance under a different cause, so this also
    checks that the base rung actually has entries. ``by_category`` is the
    right table to gate on: ``Crosswalk.lookup`` always reaches it (it is the
    only rung with no precondition on the caller supplying a subcategory or
    district), so a populated ``by_category`` is necessary and sufficient for
    the first live submission to have something to match against.

    The narrower tables are deliberately not gated on. They are populated in
    the shipped artifact (34 / 257 / 971 / 5,084 entries across the four
    rungs), but the live classifier predicts no subcategory, so ``by_full``
    and ``by_subcategory`` are never consulted in production at all. Requiring
    a rung the deployed path does not read would fail an artifact that routes
    perfectly well.

    A non-empty ``by_category`` is still not sufficient on its own: an entry
    can be present and structurally valid yet fail ``Crosswalk.lookup``'s own
    eligibility bar (``MIN_CONFIDENCE``, computed from support and share --
    see ``CrosswalkRoute.confidence``). Four rows split evenly eight ways
    loads fine and clears every structural check, but scores nowhere near
    0.3. Reimplementing that threshold here would be a fifth parallel copy of
    an eligibility rule that has already drifted from the real one four
    times, so this instead calls ``crosswalk.lookup`` itself, once per
    category in the table, and reports ``ok=True`` only when at least one of
    those real calls actually returns a route. That is the same function and
    the same thresholds the live router uses, so it cannot disagree with the
    real router by construction -- there is no second copy of the rule left
    to drift.

    The cost is loading one JSON artifact and walking its category table,
    which preflight can afford: it already opens a real OLTP connection when
    one is configured.
    """
    from janasunani.routing.crosswalk import DEFAULT_ARTIFACT, MIN_CONFIDENCE, load_crosswalk

    configured = os.environ.get(ROUTER_ENV_VAR, "").strip().lower() or ROUTER_DEFAULT

    if configured == ROUTER_RULES:
        return (
            ROUTER_RULES,
            True,
            "crosswalk disabled by configuration; first rung is the ORTPSA mapping tables",
        )

    if configured == ROUTER_INCIDENCE:
        try:
            from janasunani.evaluation.routing import (
                INCIDENCE_ARTIFACT_SCHEMA_VERSION,
                load_incidence_router,
            )
            from janasunani.tracking.artifacts import resolve_artifact

            artifact = resolve_artifact(
                ROUTING_INCIDENCE_ARTIFACT_NAME,
                models_dir=models_dir,
                verified_artifacts=verified_artifacts,
            )
            if artifact is None:
                return (
                    ROUTER_DEFAULT,
                    False,
                    "incidence router requested but no local routing_incidence "
                    "artifact resolved; using the crosswalk/rules fallback",
                )
            router = load_incidence_router(artifact)
            if router is None:
                return (
                    ROUTER_DEFAULT,
                    False,
                    "incidence router artifact is unreadable, corrupt, or invalid; "
                    "using the crosswalk/rules fallback",
                )
            if not router.has_eligible_prediction():
                return (
                    ROUTER_DEFAULT,
                    False,
                    "incidence router artifact has no aggregate that clears its "
                    "serving gates; using the crosswalk/rules fallback",
                )
            return (
                ROUTER_INCIDENCE,
                True,
                f"checksummed {INCIDENCE_ARTIFACT_SCHEMA_VERSION} artifact loaded "
                "locally; objective is agreement with the unconfirmed "
                "complaints.dept snapshot, not assignment intent, action-history "
                "traversal, routing correctness, or outcome optimization",
            )
        except Exception as exc:
            return (
                ROUTER_DEFAULT,
                False,
                f"incidence router could not be loaded ({exc}); using the "
                "crosswalk/rules fallback",
            )

    if configured not in SUPPORTED_ROUTERS:
        return (
            ROUTER_DEFAULT,
            False,
            f"{ROUTER_ENV_VAR}={configured!r} is unknown; falling back to the default router",
        )

    try:
        # Pass the path explicitly: `load_crosswalk`'s default is bound at
        # definition time, so relying on it would report on a different file
        # than the one named in the detail string.
        crosswalk = load_crosswalk(DEFAULT_ARTIFACT)
    except Exception as exc:  # pragma: no cover - load_crosswalk is documented not to raise
        return (
            ROUTER_DEFAULT,
            False,
            f"crosswalk could not be loaded ({exc}); routing degrades to mapping "
            "tables then fallback",
        )

    if crosswalk is not None:
        if not crosswalk.by_category:
            return (
                ROUTER_DEFAULT,
                False,
                f"crosswalk loaded from {DEFAULT_ARTIFACT.name} but the "
                "category table is empty; every lookup misses and routing "
                "degrades to mapping tables then fallback (run "
                "janasunani-build-crosswalk to restore method:learned)",
            )
        # Structural, not a sixth special case: this calls `Crosswalk.lookup`
        # itself -- the exact function and thresholds a live request goes
        # through -- rather than re-deriving "eligible" from the table's raw
        # fields. `by_category` keys are already `_key(category, None, None)`
        # (`_norm`-normalized, joined with the two empty rungs), and `_key`
        # is idempotent under a second `_norm`, so splitting off the first
        # segment and passing it back as `category` reaches the same table
        # cell `lookup` would for the real (unnormalized) category text.
        if not any(
            crosswalk.lookup(category=key.split("|")[0]) is not None
            for key in crosswalk.by_category
        ):
            return (
                ROUTER_DEFAULT,
                False,
                f"crosswalk loaded from {DEFAULT_ARTIFACT.name} but no entry "
                f"clears Crosswalk.lookup's confidence floor "
                f"(MIN_CONFIDENCE={MIN_CONFIDENCE}); every lookup misses and "
                "routing degrades to mapping tables then fallback (run "
                "janasunani-build-crosswalk to restore method:learned)",
            )
        return (
            ROUTER_DEFAULT,
            True,
            f"crosswalk loaded from {DEFAULT_ARTIFACT.name}; first rung is learned",
        )

    present = DEFAULT_ARTIFACT.is_file()
    reason = (
        "present but unreadable or structurally invalid"
        if present
        else "missing"
    )
    return (
        ROUTER_DEFAULT,
        False,
        f"crosswalk artifact {reason}; routing degrades to mapping tables then "
        "fallback (run janasunani-build-crosswalk to restore method:learned)",
    )
