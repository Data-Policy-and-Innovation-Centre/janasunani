import json
from datetime import date

import pytest

from janasunani.evaluation.routing import (
    IncidenceRouter,
    RouteRecord,
    load_incidence_router,
    save_incidence_router,
)
from janasunani.routing.provider import (
    ROUTER_DEFAULT,
    ROUTER_ENV_VAR,
    ROUTER_INCIDENCE,
    IncidenceRoutingProvider,
    router_from_env,
    router_status,
)
from janasunani.routing.rules import RuleRouter


def _artifact(tmp_path, directory="release"):
    rows = [
        RouteRecord(
            item_id=f"aggregate-source-{index}",
            group_id=f"aggregate-source-{index}",
            observed_on=date(2024, 1, 1),
            category="Housing",
            district="Sambalpur",
            department="Housing Department",
            split="train",
        )
        for index in range(4)
    ]
    router = IncidenceRouter(alpha=10.0, serving_min_support=3).fit(rows)
    return save_incidence_router(router, tmp_path / directory)


def test_incidence_provider_returns_support_and_concentration(tmp_path, monkeypatch):
    artifact = _artifact(tmp_path)
    monkeypatch.setenv("JANASUNANI_ROUTING_INCIDENCE_ARTIFACT", str(artifact))

    provider = router_from_env(ROUTER_INCIDENCE)
    result = provider.route(category="Housing", district="Sambalpur")

    assert isinstance(provider, IncidenceRoutingProvider)
    assert result.method == "learned"
    assert result.dept == "Housing Department"
    assert result.empirical_evidence is not None
    assert result.empirical_evidence.support == 4
    assert result.empirical_evidence.concentration == 1.0
    assert result.empirical_evidence.width == "category+district"


def test_incidence_provider_and_status_use_explicit_models_root(
    tmp_path, monkeypatch
):
    models_root = tmp_path / "custom-models"
    _artifact(models_root, "routing_incidence")
    monkeypatch.setenv(ROUTER_ENV_VAR, ROUTER_INCIDENCE)
    monkeypatch.delenv("JANASUNANI_MODELS_DIR", raising=False)
    monkeypatch.delenv("JANASUNANI_ROUTING_INCIDENCE_ARTIFACT", raising=False)

    provider = router_from_env(models_dir=models_root)
    name, ok, _detail = router_status(models_dir=models_root)

    assert isinstance(provider, IncidenceRoutingProvider)
    assert name == ROUTER_INCIDENCE
    assert ok is True


def test_incidence_provider_uses_existing_fallback_for_unseen_category(
    tmp_path, monkeypatch
):
    artifact = _artifact(tmp_path)
    monkeypatch.setenv("JANASUNANI_ROUTING_INCIDENCE_ARTIFACT", str(artifact))

    result = router_from_env(ROUTER_INCIDENCE).route(
        category="Unseen category", district="Sambalpur"
    )

    assert result.method != "learned"
    assert result.empirical_evidence is None


def test_incidence_provider_abstains_on_an_ambiguous_supported_cell(tmp_path):
    rows = [
        RouteRecord(
            item_id=f"ambiguous-{index}",
            group_id=f"ambiguous-{index}",
            observed_on=date(2024, 1, 1),
            category="Housing",
            district="Sambalpur",
            department=department,
            split="train",
        )
        for index, department in enumerate(("A", "B", "C", "D"))
    ]
    artifact = save_incidence_router(
        IncidenceRouter(alpha=10.0, serving_min_support=3).fit(rows),
        tmp_path / "ambiguous",
    )
    loaded = load_incidence_router(artifact)
    assert loaded is not None
    result = IncidenceRoutingProvider(loaded, RuleRouter()).route(
        category="Housing", district="Sambalpur"
    )

    assert result.method != "learned"
    assert result.empirical_evidence is None


def test_incidence_provider_abstains_below_the_configured_support_gate(tmp_path):
    rows = [
        RouteRecord(
            item_id=f"small-{index}",
            group_id=f"small-{index}",
            observed_on=date(2024, 1, 1),
            category="Housing",
            district="Sambalpur",
            department="Housing Department",
            split="train",
        )
        for index in range(4)
    ]
    artifact = save_incidence_router(
        IncidenceRouter(alpha=10.0, serving_min_support=5).fit(rows),
        tmp_path / "small",
    )
    loaded = load_incidence_router(artifact)
    assert loaded is not None
    result = IncidenceRoutingProvider(loaded, RuleRouter()).route(
        category="Housing", district="Sambalpur"
    )

    assert result.method != "learned"
    assert result.empirical_evidence is None


def test_missing_incidence_artifact_is_an_explicit_safe_fallback(monkeypatch):
    monkeypatch.setenv(ROUTER_ENV_VAR, ROUTER_INCIDENCE)
    monkeypatch.setenv(
        "JANASUNANI_ROUTING_INCIDENCE_ARTIFACT", "/definitely/not/present"
    )
    monkeypatch.setenv("JANASUNANI_MODELS_DIR", "/also/not/present")

    provider = router_from_env()
    name, ok, detail = router_status()

    assert provider.route(category="Housing").method in {
        "learned",
        "rules",
        "fallback",
    }
    assert name == ROUTER_DEFAULT
    assert ok is False
    assert "no local routing_incidence artifact" in detail


def test_invalid_incidence_artifact_is_not_served(tmp_path, monkeypatch):
    artifact = _artifact(tmp_path)
    payload = json.loads(artifact.read_text())
    payload["outcome_optimized"] = True
    artifact.write_text(json.dumps(payload))
    monkeypatch.setenv(ROUTER_ENV_VAR, ROUTER_INCIDENCE)
    monkeypatch.setenv("JANASUNANI_ROUTING_INCIDENCE_ARTIFACT", str(artifact))

    provider = router_from_env()
    name, ok, detail = router_status()

    assert not isinstance(provider, IncidenceRoutingProvider)
    assert name == ROUTER_DEFAULT
    assert ok is False
    assert "corrupt, or invalid" in detail


def test_all_abstaining_incidence_artifact_is_not_reported_healthy(
    tmp_path, monkeypatch
):
    rows = [
        RouteRecord(
            item_id=f"ambiguous-{index}",
            group_id=f"ambiguous-{index}",
            observed_on=date(2024, 1, 1),
            category="Housing",
            district="Sambalpur",
            department=department,
            split="train",
        )
        for index, department in enumerate(("A", "B", "C", "D"))
    ]
    artifact = save_incidence_router(
        IncidenceRouter(alpha=10.0, serving_min_support=3).fit(rows),
        tmp_path / "ambiguous-status",
    )
    monkeypatch.setenv(ROUTER_ENV_VAR, ROUTER_INCIDENCE)
    monkeypatch.setenv("JANASUNANI_ROUTING_INCIDENCE_ARTIFACT", str(artifact))

    name, ok, detail = router_status()

    assert name == ROUTER_DEFAULT
    assert ok is False
    assert "no aggregate that clears its serving gates" in detail


def test_fallback_failure_is_not_retried_or_misattributed():
    class AbstainingRouter:
        def predict_with_evidence(self, **_kwargs):
            return None

    class FailingFallback:
        calls = 0

        def route(self, **_kwargs):
            self.calls += 1
            raise RuntimeError("fallback unavailable")

    fallback = FailingFallback()
    provider = IncidenceRoutingProvider(AbstainingRouter(), fallback)

    with pytest.raises(RuntimeError, match="fallback unavailable"):
        provider.route(category="Housing")

    assert fallback.calls == 1
