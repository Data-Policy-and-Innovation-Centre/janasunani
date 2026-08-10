"""The routing and triage swap seams.

Two properties matter here and neither is about the current implementations:

1. A future trained model can be substituted without touching a call site.
2. When that model is absent, broken, or misconfigured, the demo degrades to a
   worse answer rather than to an error, and *says so* in preflight instead of
   degrading quietly. PERFORMANCE.md recorded a live submission returning
   ``method:"fallback"`` while everyone believed routing was learned; the
   status probes exist to make that state visible before an audience sees it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from janasunani.routing.provider import (
    ROUTER_DEFAULT,
    ROUTER_ENV_VAR,
    ROUTER_RULES,
    RoutingProvider,
    router_from_env,
    router_status,
)
from janasunani.serving.schemas import RoutingResult, SpamReview, TriageResult
from janasunani.serving.triage import (
    TRIAGE_BOUNDED,
    TRIAGE_ENV_VAR,
    TRIAGE_MODEL,
    TRIAGE_OFF,
    OffTriageProvider,
    ScoredTriageProvider,
    TriageUnavailableError,
    UnwiredTriageProvider,
    triage_provider_from_env,
    triage_status,
)


class RecordingRouter:
    """A stand-in trained router: satisfies the Protocol, records its calls."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def route(self, *, category, subcategory=None, district=None) -> RoutingResult:
        self.calls.append(
            {"category": category, "subcategory": subcategory, "district": district}
        )
        return RoutingResult(
            dept="Test Department",
            office="Test Office",
            confidence=0.5,
            method="fallback",
        )


# --- the Protocol is satisfied by what already exists -----------------------


def test_the_shipped_routers_already_satisfy_the_public_protocol():
    """The Protocol declares an existing contract; it does not impose a new one."""
    from janasunani.routing.rules import DEFAULT_ROUTER, MappingRouter, RuleRouter

    for router in (DEFAULT_ROUTER, MappingRouter(), RuleRouter()):
        assert hasattr(router, "route")
        result = router.route(category="Housing", district="Sambalpur")
        assert isinstance(result, RoutingResult)


def test_a_substitute_router_needs_only_the_route_method():
    provider: RoutingProvider = RecordingRouter()
    result = provider.route(category="Housing", district="Sambalpur")
    assert result.dept == "Test Department"


# --- env selection degrades rather than raising -----------------------------


def test_router_from_env_returns_the_default_when_unset(monkeypatch):
    monkeypatch.delenv(ROUTER_ENV_VAR, raising=False)
    from janasunani.routing.rules import DEFAULT_ROUTER

    assert router_from_env() is DEFAULT_ROUTER


def test_router_from_env_can_select_the_crosswalk_free_ladder():
    router = router_from_env(ROUTER_RULES)
    assert router.route(category="Housing", district="Sambalpur").method in {
        "rules",
        "fallback",
    }


@pytest.mark.parametrize("value", ["nonsense", "LEARNED", "  ", "model"])
def test_router_from_env_never_raises_on_a_bad_value(value):
    """An operator typo must not take routing off the air."""
    router = router_from_env(value)
    assert isinstance(router.route(category="Housing"), RoutingResult)


def test_router_status_reports_the_rung_that_answers_first(monkeypatch):
    monkeypatch.delenv(ROUTER_ENV_VAR, raising=False)
    name, ok, detail = router_status()
    assert name == ROUTER_DEFAULT
    # The artifact is committed, so this should be the learned rung.
    assert ok is True
    assert "learned" in detail


def test_router_status_flags_an_unknown_configuration(monkeypatch):
    monkeypatch.setenv(ROUTER_ENV_VAR, "nonsense")
    name, ok, detail = router_status()
    assert ok is False
    assert "unknown" in detail


# --- triage selection -------------------------------------------------------


def test_triage_from_env_defaults_to_the_bounded_scorer(monkeypatch):
    monkeypatch.delenv(TRIAGE_ENV_VAR, raising=False)
    assert isinstance(triage_provider_from_env(), UnwiredTriageProvider)


def test_triage_off_reports_unavailable_rather_than_clean():
    """Disabled triage must not read as 'assessed and found fine'."""
    provider = triage_provider_from_env(TRIAGE_OFF)
    assert isinstance(provider, OffTriageProvider)
    result = provider.assess(
        redacted_text="a genuine grievance about water supply",
        district="Sambalpur",
        submitted_on=datetime.now(UTC),
    )
    assert result.spam.decision == "abstained"
    assert result.spam.reason_code == "advisory_provider_unavailable"


def test_triage_model_falls_back_to_bounded_when_no_artifact_resolves(monkeypatch):
    monkeypatch.delenv("JANASUNANI_SPAM_SCORER_ARTIFACT", raising=False)
    provider = triage_provider_from_env(TRIAGE_MODEL)
    assert isinstance(provider, UnwiredTriageProvider)


@pytest.mark.parametrize("value", ["nonsense", "  learned  ", "1"])
def test_triage_from_env_never_raises_on_a_bad_value(value):
    provider = triage_provider_from_env(value)
    assert isinstance(
        provider.assess(
            redacted_text="text", district=None, submitted_on=datetime.now(UTC)
        ),
        TriageResult,
    )


def test_triage_status_says_the_shipped_scorer_is_heuristic(monkeypatch):
    """The narrative guard: 'bounded' must never read as a trained model."""
    monkeypatch.delenv(TRIAGE_ENV_VAR, raising=False)
    name, ok, detail = triage_status()
    assert name == TRIAGE_BOUNDED
    assert ok is True
    assert "not a learned model" in detail


def test_triage_status_flags_model_configured_without_an_artifact(monkeypatch):
    monkeypatch.setenv(TRIAGE_ENV_VAR, TRIAGE_MODEL)
    monkeypatch.delenv("JANASUNANI_SPAM_SCORER_ARTIFACT", raising=False)
    name, ok, detail = triage_status()
    assert name == TRIAGE_BOUNDED
    assert ok is False
    assert "no scorer artifact resolved" in detail


# --- a broken learned scorer must never reach the citizen -------------------


class ExplodingScorer:
    def score(self, redacted_text, *, is_repetition_collapsed=None):
        raise RuntimeError("model weights are corrupt")


class UnavailableScorer:
    def score(self, redacted_text, *, is_repetition_collapsed=None):
        raise TriageUnavailableError("not loaded")


class OutOfTaxonomyScorer:
    """Returns a score whose reason is not in the published set."""

    def score(self, redacted_text, *, is_repetition_collapsed=None):
        from janasunani.pipeline.spam import SpamScore

        score = SpamScore(spam_score=0.9, spam_reason="clean", evidence=())
        object.__setattr__(score, "spam_reason", "model_thinks_so")
        return score


@pytest.mark.parametrize("scorer", [ExplodingScorer(), UnavailableScorer()])
def test_a_broken_learned_scorer_degrades_instead_of_raising(scorer):
    """A failed model must not block a citizen's submission."""
    provider = ScoredTriageProvider(scorer)
    result = provider.assess(
        redacted_text="water supply has been cut for three weeks",
        district="Sambalpur",
        submitted_on=datetime.now(UTC),
    )
    assert result.spam.decision == "abstained"
    assert result.spam.reason_code == "advisory_provider_unavailable"


def test_an_out_of_taxonomy_reason_cannot_reach_the_wire():
    """The adapter is the enforcement, not the model's good behaviour."""
    provider = ScoredTriageProvider(OutOfTaxonomyScorer())
    result = provider.assess(
        redacted_text="test", district=None, submitted_on=datetime.now(UTC)
    )
    # Degrades rather than publishing an invented reason code.
    assert result.spam.reason_code == "advisory_provider_unavailable"


def test_a_working_scorer_reaches_the_response():
    from janasunani.pipeline.spam import BoundedSpamScorer

    provider = ScoredTriageProvider(BoundedSpamScorer())
    result = provider.assess(
        redacted_text="test",
        district=None,
        submitted_on=datetime.now(UTC),
    )
    assert isinstance(result.spam, SpamReview)
    assert 0.0 <= result.spam.spam_score <= 1.0


# --- the injection points themselves ----------------------------------------


def test_build_processor_exposes_both_swap_points():
    """The two parameters are the whole point of this change."""
    import inspect

    from janasunani.inference.service import build_processor

    params = inspect.signature(build_processor).parameters
    assert "router" in params
    assert "triage_provider" in params
    # Keyword-only and defaulted, so both existing call sites keep working.
    for name in ("router", "triage_provider"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert params[name].default is None


def test_an_injected_router_is_the_one_the_processor_uses():
    from janasunani.inference.service import PipelineGrievanceProcessor

    router = RecordingRouter()
    processor = PipelineGrievanceProcessor(
        ocr=lambda b, n: None,
        redact=lambda t: t,
        detect_pii=lambda t: [],
        categorizer=type("C", (), {"predict": lambda self, t: "Housing"})(),
        summarizer=type("S", (), {"summarize": lambda self, t: "summary"})(),
        router=router,
        is_english_compatible=lambda t: True,
        detect_language=lambda t: "en",
    )
    result = processor.process(
        grievance_id="g1",
        ticket_no="T1",
        text="the drain outside my house has been blocked for a month",
        district="Sambalpur",
    )
    assert router.calls, "the injected router was bypassed"
    assert result.routing.dept == "Test Department"


def test_router_status_rejects_a_corrupt_crosswalk(tmp_path, monkeypatch):
    """Codex finding on #234: presence is not validity.

    `load_crosswalk` returns None for a file that exists but is corrupt, and
    the router then falls through to the mapping tables. A status probe based
    on `is_file()` reports "first rung is learned" for exactly that file,
    reproducing the false assurance the probe exists to prevent.
    """
    import janasunani.routing.crosswalk as crosswalk_mod

    corrupt = tmp_path / "routing_crosswalk.json"
    corrupt.write_text("{not valid json")
    monkeypatch.setattr(crosswalk_mod, "DEFAULT_ARTIFACT", corrupt)
    monkeypatch.delenv(ROUTER_ENV_VAR, raising=False)

    name, ok, detail = router_status()
    assert name == ROUTER_DEFAULT
    assert ok is False
    assert "unreadable or structurally invalid" in detail


def test_router_status_distinguishes_missing_from_corrupt(tmp_path, monkeypatch):
    """The two need different operator actions, so they must read differently."""
    import janasunani.routing.crosswalk as crosswalk_mod

    monkeypatch.setattr(crosswalk_mod, "DEFAULT_ARTIFACT", tmp_path / "absent.json")
    monkeypatch.delenv(ROUTER_ENV_VAR, raising=False)

    _, ok, detail = router_status()
    assert ok is False
    assert "missing" in detail


def test_router_status_rejects_an_empty_crosswalk(tmp_path, monkeypatch):
    """Codex finding on #234: loading successfully is not the same as usable.

    All four tables empty is structurally valid -- ``_valid_table({})``
    succeeds -- so `load_crosswalk` returns a non-None `Crosswalk` for this
    file. Every lookup still misses and falls through to the mapping tables,
    so reporting "first rung is learned" here reproduces the exact false
    assurance this probe exists to prevent, just from a different cause than
    the corrupt-file case above.
    """
    import json

    import janasunani.routing.crosswalk as crosswalk_mod

    empty = tmp_path / "routing_crosswalk.json"
    empty.write_text(
        json.dumps(
            {
                "by_full": {},
                "by_subcategory": {},
                "by_category_district": {},
                "by_category": {},
            }
        )
    )
    monkeypatch.setattr(crosswalk_mod, "DEFAULT_ARTIFACT", empty)
    monkeypatch.delenv(ROUTER_ENV_VAR, raising=False)

    name, ok, detail = router_status()
    assert name == ROUTER_DEFAULT
    assert ok is False
    assert "empty" in detail
    # Distinct from the missing/corrupt wording, which need different fixes.
    assert "missing" not in detail
    assert "unreadable or structurally invalid" not in detail
