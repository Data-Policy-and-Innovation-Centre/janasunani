"""Method ladder for live routing (Unit 7).

The live classifier emits only ``category`` + ``district`` — no subcategory
(crosswalk.py:15). Only two rungs are live: ``by_category`` and
``by_category_district``. ``by_subcategory`` / ``by_full`` are built but not
consulted on the deployed path.

Ladder, with real artifact:

    crosswalk (method:"learned", empirical_evidence, confidence from
               support+share) -> mappings/rules (method:"rules") ->
               fallback (method:"fallback")

Missing or corrupt artifact degrades gracefully (``None`` -> fall through).
No fixture reads ``data/``.
"""

from __future__ import annotations

import json
import math

from janasunani.routing.crosswalk import (
    DEFAULT_ARTIFACT,
    MAX_CONFIDENCE,
    Crosswalk,
    CrosswalkRoute,
    load_crosswalk,
)
from janasunani.serving.schemas import RoutingResult


def entry(dept: str, support: int, share: float, office: str | None = None) -> dict:
    d = {"dept": dept, "support": support, "share": share}
    if office is not None:
        d["office"] = office
    return d


# ---------------------------------------------------------------------------
# Confidence is computed from support+share, not asserted
# ---------------------------------------------------------------------------


class TestConfidenceComputed:
    def test_support_and_share_both_matter(self):
        thin = CrosswalkRoute(dept="X", support=4, share=1.0, width="category")
        thick = CrosswalkRoute(dept="X", support=40000, share=1.0, width="category")
        split = CrosswalkRoute(dept="X", support=40000, share=0.51, width="category")
        dominant = CrosswalkRoute(dept="X", support=40000, share=0.98, width="category")
        assert thin.confidence < thick.confidence
        assert split.confidence < dominant.confidence
        assert thick.confidence <= MAX_CONFIDENCE

    def test_confidence_formula_matches_crosswalk(self):
        """Confidence = min(MAX, share * min(1, log1p(support)/log1p(200)))."""
        for support, share in [(10, 0.8), (100, 0.6), (500, 0.9), (50000, 1.0)]:
            route = CrosswalkRoute(dept="X", support=support, share=share, width="category")
            evidence = math.log1p(support) / math.log1p(200)
            expected = round(min(MAX_CONFIDENCE, share * min(1.0, evidence)), 4)
            assert route.confidence == expected


# ---------------------------------------------------------------------------
# Ladder: learned -> rules -> fallback, against the real artifact
# ---------------------------------------------------------------------------


class TestLadderWithRealArtifact:
    def test_known_category_gets_learned(self):
        from janasunani.routing.rules import DEFAULT_ROUTER

        result = DEFAULT_ROUTER.route(category="Agriculture & Farming")
        assert result.method == "learned"
        assert result.empirical_evidence is not None
        assert result.empirical_evidence.width == "category"
        assert result.empirical_evidence.support >= 3
        assert 0 < result.confidence <= MAX_CONFIDENCE
        # learned invariant: empirical_evidence present exactly when method is learned
        assert RoutingResult.model_validate(result.model_dump())

    def test_known_category_plus_district_gets_district_rung_when_present(self):
        """Live path: category+district must reach the district table, not bare category."""
        from janasunani.routing.rules import DEFAULT_ROUTER

        # Angul has a real category+district entry for Water Supply.
        result = DEFAULT_ROUTER.route(category="Water Supply", district="Angul")
        assert result.method == "learned"
        assert result.empirical_evidence.width == "category+district"

    def test_unknown_category_falls_through_to_rules_or_fallback(self):
        from janasunani.routing.rules import DEFAULT_ROUTER

        result = DEFAULT_ROUTER.route(category="Astrophysics")
        assert result.method in {"rules", "fallback"}
        assert result.empirical_evidence is None

    def test_fragmented_category_falls_through(self):
        """General / miscellaneous etc have share ~0.21 < MIN_CONFIDENCE, so
        crosswalk declines and mapping/fallback takes over."""
        from janasunani.routing.rules import DEFAULT_ROUTER

        result = DEFAULT_ROUTER.route(category="General")
        assert result.method in {"rules", "fallback"}

    def test_learned_carries_empirical_evidence_and_rules_does_not(self):
        from janasunani.routing.rules import DEFAULT_ROUTER

        learned = DEFAULT_ROUTER.route(category="Energy")
        assert learned.method == "learned"
        assert learned.empirical_evidence is not None
        assert learned.empirical_evidence.concentration > 0
        # Unknown category -> not learned -> no evidence
        fallback = DEFAULT_ROUTER.route(category="Astrophysics-xyz-123")
        assert fallback.empirical_evidence is None


# ---------------------------------------------------------------------------
# Subcategory rungs are not consulted live (no subcategory from classifier)
# ---------------------------------------------------------------------------


class TestOnlyLiveRungsAreConsulted:
    def test_live_call_shape_never_reaches_subcategory_rungs(self):
        """Even if only the subcategory/full tables had a key, the live
        route(category, district) call must not find it."""
        cw = Crosswalk(
            by_full={"water|leakage|sambalpur": entry("FULL", 5000, 0.9)},
            by_subcategory={"water|leakage|": entry("SUB", 5000, 0.9)},
            by_category_district={},
            by_category={},
        )
        # Live shape: no subcategory -> neither full nor subcategory is reachable.
        assert cw.lookup("Water", district="Sambalpur") is None
        # Synthetic shape with subcategory would hit it — proving the data exists.
        assert cw.lookup("Water", subcategory="Leakage", district="Sambalpur") is not None

    def test_category_district_is_the_district_signal_live(self):
        cw = Crosswalk(
            by_full={},
            by_subcategory={},
            by_category_district={"water||sambalpur": entry("DISTRICT", 500, 0.8)},
            by_category={"water||": entry("CATEGORY", 5000, 0.8)},
        )
        # Live shape reaches the district rung.
        hit = cw.lookup("Water", district="Sambalpur")
        assert hit is not None
        assert hit.width == "category+district"
        assert hit.dept == "DISTRICT"


# ---------------------------------------------------------------------------
# Missing artifact degrades gracefully
# ---------------------------------------------------------------------------


class TestMissingArtifactDegrades:
    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        import janasunani.routing.crosswalk as cw

        monkeypatch.setattr(cw, "load_crosswalk", lambda *a, **k: None)
        from janasunani.routing.rules import _LazyDefaultRouter

        router = _LazyDefaultRouter(enable_crosswalk=True)
        result = router.route(category="Agriculture & Farming", district="Angul")
        assert result.method in {"rules", "fallback"}
        assert result.empirical_evidence is None

    def test_corrupt_artifact_returns_none(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert load_crosswalk(bad) is None
        bad2 = tmp_path / "bad2.json"
        bad2.write_text(
            json.dumps(
                {
                    "by_full": {},
                    "by_subcategory": {},
                    "by_category_district": {},
                    "by_category": ["not-a-table"],
                }
            ),
            encoding="utf-8",
        )
        assert load_crosswalk(bad2) is None

    def test_load_crosswalk_missing_path_is_none(self, tmp_path):
        assert load_crosswalk(tmp_path / "absent.json") is None

    def test_real_artifact_is_not_empty_and_has_live_rungs(self):
        loaded = load_crosswalk(DEFAULT_ARTIFACT)
        assert loaded is not None
        assert loaded.by_category
        assert loaded.by_category_district


# ---------------------------------------------------------------------------
# Guard: incidence only, never outcome (disposal time / benefit)
# ---------------------------------------------------------------------------


class TestIncidenceOnly:
    def test_crosswalk_never_routes_on_outcome(self):
        """Crosswalk keys are category/subcategory/district + counts only.
        No disposal time, benefit, or outcome column may appear in the artifact."""
        payload = json.loads(DEFAULT_ARTIFACT.read_text(encoding="utf-8"))
        text = json.dumps(payload).lower()
        for forbidden in ("disposal", "benefit", "outcome", "closure", "days_to_close"):
            assert forbidden not in text

    def test_artifact_is_aggregates_only(self):
        payload = json.loads(DEFAULT_ARTIFACT.read_text(encoding="utf-8"))
        for table in ("by_category", "by_category_district", "by_full", "by_subcategory"):
            for key, entry_ in payload[table].items():
                assert set(entry_.keys()) <= {"dept", "support", "share", "office"}
                assert isinstance(entry_["dept"], str) and entry_["dept"]
                assert isinstance(entry_["support"], int) and entry_["support"] >= 3
