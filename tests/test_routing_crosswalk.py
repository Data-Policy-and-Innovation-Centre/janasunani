"""The empirical routing crosswalk (#33).

Synthetic categories and departments only. The real crosswalk is aggregates —
category, subcategory, district, dept and counts — but nothing here needs the
corpus to exercise the ladder or the confidence arithmetic.
"""

from __future__ import annotations

import json

import pytest

from janasunani.routing.crosswalk import (
    MAX_CONFIDENCE,
    MIN_SUPPORT,
    Crosswalk,
    CrosswalkRoute,
    _argmax_table,
    load_crosswalk,
    save_crosswalk,
)


def entry(dept: str, support: int, share: float) -> dict:
    return {"dept": dept, "support": support, "share": share}


@pytest.fixture
def crosswalk() -> Crosswalk:
    return Crosswalk(
        by_full={"water|leakage|sambalpur": entry("PHED", 4000, 0.9)},
        by_subcategory={"water|leakage|": entry("PHED", 9000, 0.7)},
        by_category={"water||": entry("RURAL DEV", 20000, 0.5)},
    )


class TestConfidenceIsComputedNotAsserted:
    """#33 is explicit: a route backed by 4 rows and one backed by 40,000 must
    not present identically."""

    def test_thin_support_scores_far_below_thick_support(self):
        thin = CrosswalkRoute(dept="X", support=4, share=1.0, width="category")
        thick = CrosswalkRoute(dept="X", support=40_000, share=1.0, width="category")
        assert thin.confidence < thick.confidence
        assert thick.confidence - thin.confidence > 0.3

    def test_a_split_destination_scores_below_a_dominant_one(self):
        """Support alone must not carry it: a heavily used category that splits
        evenly between two departments is not a confident route."""
        split = CrosswalkRoute(dept="X", support=40_000, share=0.51, width="category")
        dominant = CrosswalkRoute(dept="X", support=40_000, share=0.98, width="category")
        assert split.confidence < dominant.confidence

    def test_confidence_never_claims_certainty(self):
        """A frequency count describes the past; it does not judge that the
        past was correct."""
        perfect = CrosswalkRoute(dept="X", support=10**6, share=1.0, width="category")
        assert perfect.confidence <= MAX_CONFIDENCE

    def test_confidence_stays_in_range(self):
        for support in (1, 10, 1000, 100_000):
            for share in (0.1, 0.5, 1.0):
                value = CrosswalkRoute("X", support, share, "category").confidence
                assert 0.0 <= value <= 1.0


class TestTheFallbackLadder:
    def test_widest_key_wins_when_present(self, crosswalk):
        hit = crosswalk.lookup("Water", "Leakage", "Sambalpur")
        assert hit.dept == "PHED"
        assert hit.width == "category+subcategory+district"

    def test_falls_back_to_subcategory_when_district_is_unknown(self, crosswalk):
        hit = crosswalk.lookup("Water", "Leakage", "Nowhere")
        assert hit.width == "category+subcategory"

    def test_falls_back_to_category_when_subcategory_is_unknown(self, crosswalk):
        hit = crosswalk.lookup("Water", "Unheard-of", "Sambalpur")
        assert hit.width == "category"
        assert hit.dept == "RURAL DEV"

    def test_missing_subcategory_skips_the_narrower_rungs(self, crosswalk):
        hit = crosswalk.lookup("Water", None, None)
        assert hit.width == "category"

    def test_an_unknown_category_returns_nothing(self, crosswalk):
        assert crosswalk.lookup("Astrophysics", None, None) is None

    def test_lookup_is_case_and_space_insensitive(self, crosswalk):
        assert crosswalk.lookup("  WATER ", "leakage", "SAMBALPUR").dept == "PHED"


class TestLowSupportIsRefused:
    """A wrong confident route is worse than falling through: too few rows
    cannot distinguish a pattern from an accident."""

    def test_below_the_floor_the_rung_is_skipped(self):
        thin = Crosswalk(
            by_full={"water|leakage|sambalpur": entry("PHED", MIN_SUPPORT - 1, 1.0)},
            by_subcategory={},
            by_category={"water||": entry("RURAL DEV", 5000, 0.8)},
        )
        hit = thin.lookup("Water", "Leakage", "Sambalpur")
        assert hit.width == "category"

    def test_all_rungs_thin_returns_nothing(self):
        thin = Crosswalk(
            by_full={"water|leakage|sambalpur": entry("PHED", 1, 1.0)},
            by_subcategory={},
            by_category={},
        )
        assert thin.lookup("Water", "Leakage", "Sambalpur") is None


class TestArgmax:
    def test_the_most_common_destination_wins_and_share_is_its_fraction(self):
        table = _argmax_table(
            [("water||", "PHED", 70), ("water||", "RURAL DEV", 30)]
        )
        assert table["water||"]["dept"] == "PHED"
        assert table["water||"]["support"] == 70
        assert table["water||"]["share"] == 0.7

    def test_share_reflects_fragmentation(self):
        table = _argmax_table(
            [("x||", "A", 34), ("x||", "B", 33), ("x||", "C", 33)]
        )
        assert table["x||"]["share"] == pytest.approx(0.34, abs=0.01)


class TestArtifactRoundTripAndDegradation:
    def test_save_then_load_preserves_the_tables(self, tmp_path, crosswalk):
        path = save_crosswalk(crosswalk, tmp_path / "cw.json")
        loaded = load_crosswalk(path)
        assert loaded.lookup("Water", "Leakage", "Sambalpur").dept == "PHED"

    def test_a_missing_artifact_returns_none_rather_than_raising(self, tmp_path):
        """#33's stated degradation: routing reverts to the placeholder and
        nothing else in the demo is affected."""
        assert load_crosswalk(tmp_path / "absent.json") is None

    def test_a_corrupt_artifact_returns_none_rather_than_raising(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_crosswalk(path) is None

    def test_an_artifact_missing_a_table_returns_none(self, tmp_path):
        path = tmp_path / "partial.json"
        path.write_text(json.dumps({"by_full": {}}), encoding="utf-8")
        assert load_crosswalk(path) is None


class TestWiredIntoTheDefaultRouter:
    def test_a_crosswalk_hit_is_reported_as_learned(self, monkeypatch, crosswalk):
        """Not 'rules': this came from what the record shows, not a
        hand-written table."""
        import janasunani.routing.crosswalk as cw
        from janasunani.routing.rules import _LazyDefaultRouter

        monkeypatch.setattr(cw, "load_crosswalk", lambda *a, **k: crosswalk)
        router = _LazyDefaultRouter()
        result = router.route(category="Water", subcategory="Leakage", district="Sambalpur")
        assert result.method == "learned"
        assert result.dept == "PHED"
        assert 0.0 < result.confidence <= MAX_CONFIDENCE

    def test_no_crosswalk_falls_through_to_the_existing_router(self, monkeypatch):
        import janasunani.routing.crosswalk as cw
        from janasunani.routing.rules import _LazyDefaultRouter

        monkeypatch.setattr(cw, "load_crosswalk", lambda *a, **k: None)
        router = _LazyDefaultRouter()
        result = router.route(category="Water", subcategory="Leakage", district="Sambalpur")
        assert result.method in {"rules", "fallback"}

    def test_an_unknown_category_falls_through_even_with_a_crosswalk(
        self, monkeypatch, crosswalk
    ):
        import janasunani.routing.crosswalk as cw
        from janasunani.routing.rules import _LazyDefaultRouter

        monkeypatch.setattr(cw, "load_crosswalk", lambda *a, **k: crosswalk)
        router = _LazyDefaultRouter()
        result = router.route(category="Astrophysics")
        assert result.method in {"rules", "fallback"}


class TestTheLadderPrefersEvidenceOverSpecificity:
    """A narrow cell can be thin where the broader one is solid. Real data:
    Accident/Fire Accident/Cuttack has support 3 and scores 0.26, while
    Accident/Fire Accident has 66 and scores 0.56. Handing back the narrow one
    would give the caller the less trustworthy answer labelled more confident."""

    def test_a_thin_narrow_cell_loses_to_a_solid_broad_one(self):
        cw = Crosswalk(
            by_full={"a|b|c": entry("THIN DEPT", 3, 1.0)},
            by_subcategory={"a|b|": entry("SOLID DEPT", 5000, 0.9)},
            by_category={},
        )
        hit = cw.lookup("a", "b", "c")
        assert hit.dept == "SOLID DEPT"
        assert hit.width == "category+subcategory"

    def test_a_solid_narrow_cell_still_wins(self):
        cw = Crosswalk(
            by_full={"a|b|c": entry("NARROW", 5000, 0.95)},
            by_subcategory={"a|b|": entry("BROAD", 9000, 0.6)},
            by_category={},
        )
        assert cw.lookup("a", "b", "c").width == "category+subcategory+district"

    def test_a_tie_keeps_the_more_specific_rung(self):
        cw = Crosswalk(
            by_full={"a|b|c": entry("NARROW", 5000, 0.9)},
            by_subcategory={"a|b|": entry("BROAD", 5000, 0.9)},
            by_category={},
        )
        assert cw.lookup("a", "b", "c").width == "category+subcategory+district"
