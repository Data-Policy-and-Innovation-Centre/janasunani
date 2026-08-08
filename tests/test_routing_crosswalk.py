"""The empirical routing crosswalk (#33).

Synthetic categories and departments only. The real crosswalk is aggregates —
category, subcategory, district, dept, office and counts — but nothing here
needs the corpus to exercise the ladder, the confidence arithmetic, or (via a
synthetic Parquet lake in ``tmp_path``) the real DuckDB build path. Nothing in
this file reads anything under ``data/``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from janasunani.routing.crosswalk import (
    DEFAULT_ARTIFACT,
    MAX_CONFIDENCE,
    MIN_CONFIDENCE,
    MIN_SUPPORT,
    Crosswalk,
    CrosswalkRoute,
    _argmax_table,
    build_crosswalk,
    load_crosswalk,
    save_crosswalk,
)


def entry(dept: str, support: int, share: float, office: str | None = None) -> dict:
    built = {"dept": dept, "support": support, "share": share}
    if office is not None:
        built["office"] = office
    return built


@pytest.fixture
def crosswalk() -> Crosswalk:
    return Crosswalk(
        by_full={"water|leakage|sambalpur": entry("PHED", 4000, 0.9)},
        by_subcategory={"water|leakage|": entry("PHED SUBCAT", 9000, 0.7)},
        by_category_district={
            "water||sambalpur": entry(
                "PHED DISTRICT", 500, 0.6, office="PHED Sambalpur Division"
            )
        },
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

    def test_office_defaults_to_none(self):
        """A caller must not have to know whether office data exists to ask
        for it."""
        assert CrosswalkRoute(dept="X", support=10, share=1.0, width="category").office is None


class TestTheFallbackLadder:
    def test_widest_key_wins_when_present(self, crosswalk):
        hit = crosswalk.lookup("Water", "Leakage", "Sambalpur")
        assert hit.dept == "PHED"
        assert hit.width == "category+subcategory+district"

    def test_falls_back_to_subcategory_when_district_is_unknown(self, crosswalk):
        hit = crosswalk.lookup("Water", "Leakage", "Nowhere")
        assert hit.width == "category+subcategory"

    def test_falls_back_to_category_district_when_subcategory_is_unknown(self, crosswalk):
        """Real data: 0.6 (category+district) beats 0.5 (bare category), so an
        unrecognized subcategory with a known district must not throw the
        district signal away."""
        hit = crosswalk.lookup("Water", "Unheard-of", "Sambalpur")
        assert hit.width == "category+district"
        assert hit.dept == "PHED DISTRICT"

    def test_falls_back_to_category_when_subcategory_and_district_are_both_unknown(
        self, crosswalk
    ):
        hit = crosswalk.lookup("Water", "Unheard-of", "Nowhere")
        assert hit.width == "category"
        assert hit.dept == "RURAL DEV"

    def test_missing_subcategory_and_district_skips_the_narrower_rungs(self, crosswalk):
        hit = crosswalk.lookup("Water", None, None)
        assert hit.width == "category"

    def test_the_live_callers_shape_reaches_the_district_rung(self, crosswalk):
        """`inference/service.py` calls `route(category=..., district=...)` --
        no subcategory. This is that exact call shape (#33 P1: 'the live path
        never reaches the narrow rungs'), and it must land on the
        category+district table, not skip straight to bare category."""
        hit = crosswalk.lookup("Water", district="Sambalpur")
        assert hit.width == "category+district"
        assert hit.dept == "PHED DISTRICT"
        assert hit.office == "PHED Sambalpur Division"

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
            by_category_district={},
            by_category={"water||": entry("RURAL DEV", 5000, 0.8)},
        )
        hit = thin.lookup("Water", "Leakage", "Sambalpur")
        assert hit.width == "category"

    def test_all_rungs_thin_returns_nothing(self):
        thin = Crosswalk(
            by_full={"water|leakage|sambalpur": entry("PHED", 1, 1.0)},
            by_subcategory={},
            by_category_district={},
            by_category={},
        )
        assert thin.lookup("Water", "Leakage", "Sambalpur") is None


class TestAWeakHitDeclinesRatherThanOverride:
    """P1 on PR #156: any non-None crosswalk hit used to override the mapping
    router (0.75-0.9) and the generic fallback (0.25) regardless of its own
    confidence. Real data: 'general', 'miscellaneous' and 'pension/retirement
    benefits' score ~0.216, ~0.208 and ~0.194 -- all already below the generic
    fallback. MIN_CONFIDENCE must exceed 0.25, and by enough that these three
    real, heavily-supported categories fall through rather than override it.
    """

    def test_min_confidence_clears_the_generic_fallback(self):
        assert MIN_CONFIDENCE > 0.25

    def test_a_hit_below_min_confidence_is_refused(self):
        weak = Crosswalk(
            by_full={},
            by_subcategory={},
            by_category_district={},
            # Mirrors the real 'general' category: huge support, low share.
            by_category={"general||": entry("PANCHAYATI RAJ", 30_647, 0.2163)},
        )
        assert weak.lookup("General", None, None) is None

    def test_a_hit_at_or_above_min_confidence_is_returned(self):
        strong = Crosswalk(
            by_full={},
            by_subcategory={},
            by_category_district={},
            by_category={"water||": entry("PHED", 20_000, 0.5)},
        )
        hit = strong.lookup("Water", None, None)
        assert hit is not None
        assert hit.confidence >= MIN_CONFIDENCE

    def test_a_refused_hit_falls_through_to_the_mapping_router(self, monkeypatch):
        import janasunani.routing.crosswalk as cw
        from janasunani.routing.rules import _LazyDefaultRouter

        weak = Crosswalk(
            by_full={},
            by_subcategory={},
            by_category_district={},
            by_category={"general||": entry("PANCHAYATI RAJ", 30_647, 0.2163)},
        )
        monkeypatch.setattr(cw, "load_crosswalk", lambda *a, **k: weak)
        router = _LazyDefaultRouter(enable_crosswalk=True)
        result = router.route(category="General")
        assert result.method in {"rules", "fallback"}
        assert result.dept != "PANCHAYATI RAJ"


class TestArgmax:
    def test_the_most_common_destination_wins_and_share_is_its_fraction(self):
        table = _argmax_table(
            [("water||", "PHED", None, 70), ("water||", "RURAL DEV", None, 30)]
        )
        assert table["water||"]["dept"] == "PHED"
        assert table["water||"]["support"] == 70
        assert table["water||"]["share"] == 0.7
        assert "office" not in table["water||"]

    def test_share_reflects_fragmentation(self):
        table = _argmax_table(
            [("x||", "A", None, 34), ("x||", "B", None, 33), ("x||", "C", None, 33)]
        )
        assert table["x||"]["share"] == pytest.approx(0.34, abs=0.01)

    def test_below_min_support_the_key_is_dropped(self):
        table = _argmax_table([("x||", "A", None, MIN_SUPPORT - 1)])
        assert table == {}


class TestArgmaxLearnsOfficeJointly:
    """P1 on PR #156: every hit reported 'Office of the Collector, {district}'
    regardless of department, even though the crosswalk only aggregated
    `dept`. ROADMAP's target is `argmax(dept, office)`; the office must be
    learned from the same rows, not asserted."""

    def test_a_concentrated_office_is_named(self):
        table = _argmax_table(
            [
                ("water||", "PHED", "PHED Division A", 80),
                ("water||", "PHED", "PHED Division B", 5),
                ("water||", "RURAL DEV", "RD Office", 15),
            ]
        )
        assert table["water||"]["dept"] == "PHED"
        assert table["water||"]["office"] == "PHED Division A"

    def test_a_fragmented_office_is_not_named(self):
        """Too fragmented to call: the department is returned, the office
        is withheld rather than picking whichever office narrowly leads."""
        table = _argmax_table(
            [
                ("water||", "PHED", "PHED Division A", 45),
                ("water||", "PHED", "PHED Division B", 40),
                ("water||", "PHED", "PHED Division C", 15),
            ]
        )
        assert table["water||"]["dept"] == "PHED"
        assert "office" not in table["water||"]

    def test_a_thin_office_is_not_named_even_if_the_dept_clears_the_floor(self):
        table = _argmax_table(
            [
                ("water||", "PHED", "PHED Division A", 2),
                ("water||", "PHED", None, 1),
            ]
        )
        assert table["water||"]["support"] == 3  # the dept clears MIN_SUPPORT
        assert "office" not in table["water||"]  # the office (2 rows) does not

    def test_an_unrecorded_office_never_wins(self):
        """Rows with no office on file must not be able to 'win' the office
        slot just because they outnumber every real office."""
        table = _argmax_table(
            [
                ("water||", "PHED", None, 100),
                ("water||", "PHED", "PHED Division A", 40),
            ]
        )
        assert "office" not in table["water||"]


class TestArtifactRoundTripAndDegradation:
    def test_the_shipped_artifact_loads_with_semantically_valid_entries(self):
        """Schema migrations must include the packaged aggregate, not only
        newly built artifacts. ``load_crosswalk`` validates every table, key,
        and entry before returning. The rebuilt artifact now carries real
        category+district evidence (971 keys) built from source rows -- the
        legacy gap where ``by_category_district`` was empty because only
        winners were retained is closed."""
        loaded = load_crosswalk(DEFAULT_ARTIFACT)

        assert loaded is not None
        assert loaded.by_full
        assert loaded.by_subcategory
        assert loaded.by_category
        assert loaded.by_category_district

        category_hit = loaded.lookup("Agriculture & Farming")
        assert category_hit is not None
        assert category_hit.dept == "Agriculture & Farmers' Empowerment"
        assert category_hit.support == 4242

        district_hit = loaded.lookup("Water Supply", district="Angul")
        assert district_hit is not None
        assert district_hit.width == "category+district"
        assert district_hit.dept == "Panchayati Raj & Drinking Water"
        assert district_hit.support == 434

    def test_save_then_load_preserves_the_tables(self, tmp_path, crosswalk):
        path = save_crosswalk(crosswalk, tmp_path / "cw.json")
        loaded = load_crosswalk(path)
        assert loaded.lookup("Water", "Leakage", "Sambalpur").dept == "PHED"

    def test_save_then_load_preserves_the_office(self, tmp_path, crosswalk):
        path = save_crosswalk(crosswalk, tmp_path / "cw.json")
        loaded = load_crosswalk(path)
        hit = loaded.lookup("Water", district="Sambalpur")
        assert hit.office == "PHED Sambalpur Division"

    def test_a_missing_artifact_returns_none_rather_than_raising(self, tmp_path):
        """#33's stated degradation: routing reverts to the placeholder and
        nothing else in the demo is affected."""
        assert load_crosswalk(tmp_path / "absent.json") is None

    def test_a_corrupt_artifact_returns_none_rather_than_raising(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_crosswalk(path) is None

    def test_an_undecodable_artifact_returns_none_rather_than_raising(self, tmp_path):
        path = tmp_path / "bad_encoding.json"
        path.write_bytes(b"\xff\xfe")
        assert load_crosswalk(path) is None

    def test_an_artifact_missing_a_table_returns_none(self, tmp_path):
        path = tmp_path / "partial.json"
        path.write_text(json.dumps({"by_full": {}}), encoding="utf-8")
        assert load_crosswalk(path) is None

    def test_an_artifact_with_a_table_encoded_as_a_list_returns_none(self, tmp_path):
        """P2 on PR #156: a structurally corrupt but syntactically valid
        artifact used to load successfully and only raise on the first
        matching lookup."""
        path = tmp_path / "list_table.json"
        payload = {
            "by_full": {},
            "by_subcategory": {},
            "by_category_district": {},
            "by_category": ["not", "a", "table"],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert load_crosswalk(path) is None

    def test_an_artifact_with_an_entry_missing_support_returns_none(self, tmp_path):
        path = tmp_path / "bad_entry.json"
        payload = {
            "by_full": {},
            "by_subcategory": {},
            "by_category_district": {},
            "by_category": {"water||": {"dept": "PHED", "share": 0.9}},
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert load_crosswalk(path) is None

    def test_an_artifact_with_an_entry_missing_share_returns_none(self, tmp_path):
        path = tmp_path / "bad_entry2.json"
        payload = {
            "by_full": {},
            "by_subcategory": {},
            "by_category_district": {},
            "by_category": {"water||": {"dept": "PHED", "support": 10}},
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert load_crosswalk(path) is None

    def test_an_artifact_with_a_non_string_office_returns_none(self, tmp_path):
        path = tmp_path / "bad_office.json"
        payload = {
            "by_full": {},
            "by_subcategory": {},
            "by_category_district": {},
            "by_category": {
                "water||": {"dept": "PHED", "support": 10, "share": 0.9, "office": 123}
            },
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert load_crosswalk(path) is None

    @pytest.mark.parametrize(
        ("key", "entry"),
        [
            ("Water||", {"dept": "PHED", "support": 10, "share": 0.9}),
            ("water|leakage|", {"dept": "PHED", "support": 10, "share": 0.9}),
            ("water||", {"dept": "PHED", "support": 2, "share": 0.9}),
            ("water||", {"dept": "PHED", "support": 10, "share": 1.1}),
        ],
    )
    def test_an_artifact_with_a_misplaced_or_invalid_entry_returns_none(
        self, tmp_path, key, entry
    ):
        """Table width, normalized keys, and aggregate bounds are part of
        the artifact contract too; accepting any of them can make a corrupt
        artifact route a live request incorrectly."""
        path = tmp_path / "invalid_entry.json"
        payload = {
            "by_full": {},
            "by_subcategory": {},
            "by_category_district": {},
            "by_category": {key: entry},
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert load_crosswalk(path) is None

    def test_a_valid_artifact_with_no_office_on_an_entry_still_loads(self, tmp_path):
        path = tmp_path / "no_office.json"
        payload = {
            "by_full": {},
            "by_subcategory": {},
            "by_category_district": {},
            "by_category": {"water||": {"dept": "PHED", "support": 10, "share": 0.9}},
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        loaded = load_crosswalk(path)
        assert loaded is not None
        assert loaded.lookup("Water").office is None


class TestWiredIntoTheDefaultRouter:
    def test_a_crosswalk_hit_is_reported_as_learned(self, monkeypatch, crosswalk):
        """Not 'rules': this came from what the record shows, not a
        hand-written table."""
        import janasunani.routing.crosswalk as cw
        from janasunani.routing.rules import _LazyDefaultRouter

        monkeypatch.setattr(cw, "load_crosswalk", lambda *a, **k: crosswalk)
        router = _LazyDefaultRouter(enable_crosswalk=True)
        result = router.route(category="Water", subcategory="Leakage", district="Sambalpur")
        assert result.method == "learned"
        assert result.dept == "PHED"
        assert 0.0 < result.confidence <= MAX_CONFIDENCE
        assert result.empirical_evidence.model_dump() == {
            "support": 4000,
            "concentration": 0.9,
            "width": "category+subcategory+district",
        }

    def test_no_crosswalk_falls_through_to_the_existing_router(self, monkeypatch):
        import janasunani.routing.crosswalk as cw
        from janasunani.routing.rules import _LazyDefaultRouter

        monkeypatch.setattr(cw, "load_crosswalk", lambda *a, **k: None)
        router = _LazyDefaultRouter(enable_crosswalk=True)
        result = router.route(category="Water", subcategory="Leakage", district="Sambalpur")
        assert result.method in {"rules", "fallback"}

    def test_default_router_does_not_load_or_use_empirical_artifact(self, monkeypatch):
        """The routing gold validation does not exist yet, so an artifact on
        disk cannot silently turn on learned routing in the demo."""
        import janasunani.routing.crosswalk as cw
        from janasunani.routing.rules import _LazyDefaultRouter

        def unexpected_load(*args, **kwargs):
            raise AssertionError("default router must not load the crosswalk")

        monkeypatch.setattr(cw, "load_crosswalk", unexpected_load)
        result = _LazyDefaultRouter().route(category="Water Supply", district="Puri")
        assert result.method == "rules"

    def test_an_unknown_category_falls_through_even_with_a_crosswalk(
        self, monkeypatch, crosswalk
    ):
        import janasunani.routing.crosswalk as cw
        from janasunani.routing.rules import _LazyDefaultRouter

        monkeypatch.setattr(cw, "load_crosswalk", lambda *a, **k: crosswalk)
        router = _LazyDefaultRouter(enable_crosswalk=True)
        result = router.route(category="Astrophysics")
        assert result.method in {"rules", "fallback"}

    def test_the_live_callers_shape_gets_a_learned_route(self, monkeypatch, crosswalk):
        """The exact call `inference/service.py` makes: category + district,
        no subcategory. Before the category+district rung existed this fell
        all the way through to the bare category table."""
        import janasunani.routing.crosswalk as cw
        from janasunani.routing.rules import _LazyDefaultRouter

        monkeypatch.setattr(cw, "load_crosswalk", lambda *a, **k: crosswalk)
        router = _LazyDefaultRouter(enable_crosswalk=True)
        result = router.route(category="Water", district="Sambalpur")
        assert result.method == "learned"
        assert result.dept == "PHED DISTRICT"


class TestTheOfficeIsNeverFabricated:
    """P1 on PR #156: every hit used to return 'Office of the Collector,
    {district}' regardless of department. The office must come from the
    jointly-aggregated crosswalk entry, or withhold a specific office in
    favor of a department-level label -- never invent one."""

    def test_a_learned_office_is_returned_with_the_district_appended(
        self, monkeypatch, crosswalk
    ):
        import janasunani.routing.crosswalk as cw
        from janasunani.routing.rules import _LazyDefaultRouter

        monkeypatch.setattr(cw, "load_crosswalk", lambda *a, **k: crosswalk)
        router = _LazyDefaultRouter(enable_crosswalk=True)
        result = router.route(category="Water", district="Sambalpur")
        assert result.dept == "PHED DISTRICT"
        assert result.office == "PHED Sambalpur Division, Sambalpur"

    def test_a_missing_office_names_the_department_instead_of_the_collector(
        self, monkeypatch, crosswalk
    ):
        import janasunani.routing.crosswalk as cw
        from janasunani.routing.rules import _LazyDefaultRouter

        monkeypatch.setattr(cw, "load_crosswalk", lambda *a, **k: crosswalk)
        router = _LazyDefaultRouter(enable_crosswalk=True)
        result = router.route(category="Water", subcategory="Leakage", district="Sambalpur")
        assert result.dept == "PHED"
        assert result.office == "PHED Department, Sambalpur"
        assert "Collector" not in result.office


class TestTheLadderPrefersEvidenceOverSpecificity:
    """A narrow cell can be thin where the broader one is solid. Real data:
    Accident/Fire Accident/Cuttack has support 3 and scores 0.26, while
    Accident/Fire Accident has 66 and scores 0.56. Handing back the narrow one
    would give the caller the less trustworthy answer labelled more confident."""

    def test_a_thin_narrow_cell_loses_to_a_solid_broad_one(self):
        cw = Crosswalk(
            by_full={"a|b|c": entry("THIN DEPT", 3, 1.0)},
            by_subcategory={"a|b|": entry("SOLID DEPT", 5000, 0.9)},
            by_category_district={},
            by_category={},
        )
        hit = cw.lookup("a", "b", "c")
        assert hit.dept == "SOLID DEPT"
        assert hit.width == "category+subcategory"

    def test_a_solid_narrow_cell_still_wins(self):
        cw = Crosswalk(
            by_full={"a|b|c": entry("NARROW", 5000, 0.95)},
            by_subcategory={"a|b|": entry("BROAD", 9000, 0.6)},
            by_category_district={},
            by_category={},
        )
        assert cw.lookup("a", "b", "c").width == "category+subcategory+district"

    def test_a_tie_keeps_the_more_specific_rung(self):
        cw = Crosswalk(
            by_full={"a|b|c": entry("NARROW", 5000, 0.9)},
            by_subcategory={"a|b|": entry("BROAD", 5000, 0.9)},
            by_category_district={},
            by_category={},
        )
        assert cw.lookup("a", "b", "c").width == "category+subcategory+district"


class TestBuildCrosswalkAgainstASyntheticLake:
    """P1 on PR #156: the previous suite constructed `Crosswalk` objects and
    called `_argmax_table` directly, but nothing exercised `build_crosswalk`
    against a real Parquet lake -- the CLI (`janasunani-build-crosswalk`) that
    regenerates the committed runtime artifact could break silently. This
    writes a synthetic lake to `tmp_path`, following `tests/test_materialize
    .py`'s pattern, and validates all four generated tables. Nothing under
    `data/` is touched.
    """

    @pytest.fixture
    def lake_dir(self, tmp_path) -> Path:
        import polars as pl

        rows: list[dict] = []

        def add(category, subcategory, district, dept, office, n):
            rows.extend(
                {
                    "category": category,
                    "subcategory": subcategory,
                    "district": district,
                    "dept": dept,
                    "office": office,
                }
                for _ in range(n)
            )

        # Water Supply / Leakage / Sambalpur -> PHED, concentrated in one
        # office. A whitespace/case variant of the *same* real-world key is
        # mixed in on purpose (P2: build-time normalization must merge it
        # with the canonically-spelled rows, the way lookup-time _norm would,
        # rather than leave it as an unreachable orphan key).
        add("Water Supply", "Leakage", "Sambalpur", "PHED", "PHED Sambalpur Division", 5)
        add("  Water   Supply ", "LEAKAGE", "sambalpur", "PHED", "PHED Sambalpur Division", 3)
        add("Water Supply", "Leakage", "Sambalpur", "Rural Dev", "RD Office", 2)

        # Water Supply / Leakage, no district -- feeds the subcategory rung
        # only (the full-key rung requires a district).
        add("Water Supply", "Leakage", None, "PHED", "PHED HQ", 6)

        # Water Supply / Sambalpur, no subcategory -- the category+district
        # rung the live router (category + district, no subcategory) reaches.
        add("Water Supply", None, "Sambalpur", "PHED", "PHED Sambalpur Division", 4)

        # Roads, bare category, office fragmented three ways -- the dept must
        # be named, no single office should be.
        add("Roads", None, None, "Works", "Division A", 4)
        add("Roads", None, None, "Works", "Division B", 4)
        add("Roads", None, None, "Works", "Division C", 3)

        # Below MIN_SUPPORT -- must be dropped at build time, not merely
        # unreachable at lookup.
        add("Astrophysics", None, None, "Nobody", None, 1)

        # Isolated single-district category with no other data anywhere --
        # its category+district and bare-category entries are therefore
        # numerically identical (same 10 rows either way). That tie is the
        # cleanest possible proof the district rung is reachable end to end:
        # `lookup()` breaks ties toward the earlier (narrower) ladder entry,
        # so only a real category+district rung, correctly wired into the
        # ladder ahead of bare category, can win it.
        add("Sanitation", None, "Puri", "Health", "CDMO Puri Office", 10)

        frame = pl.DataFrame(rows)
        out = tmp_path / "interim"
        out.mkdir()
        frame.write_parquet(out / "complaints.parquet")
        return out

    def test_the_full_rung_merges_normalization_variants(self, lake_dir):
        crosswalk = build_crosswalk(lake_dir)
        key = "water supply|leakage|sambalpur"
        assert crosswalk.by_full[key]["dept"] == "PHED"
        assert crosswalk.by_full[key]["support"] == 8  # 5 + the 3-row variant
        assert crosswalk.by_full[key]["share"] == 0.8  # 8 / (8 + 2 Rural Dev)
        assert crosswalk.by_full[key]["office"] == "PHED Sambalpur Division"

    def test_the_subcategory_rung_ignores_district(self, lake_dir):
        crosswalk = build_crosswalk(lake_dir)
        key = "water supply|leakage|"
        entry_ = crosswalk.by_subcategory[key]
        assert entry_["dept"] == "PHED"
        assert entry_["support"] == 14  # 5 + 3 + the district-less 6
        assert entry_["office"] == "PHED Sambalpur Division"

    def test_the_category_district_rung_ignores_subcategory(self, lake_dir):
        crosswalk = build_crosswalk(lake_dir)
        key = "water supply||sambalpur"
        entry_ = crosswalk.by_category_district[key]
        assert entry_["dept"] == "PHED"
        assert entry_["support"] == 12  # 5 + 3 + the subcategory-less 4
        assert entry_["office"] == "PHED Sambalpur Division"

    def test_the_category_rung_aggregates_everything(self, lake_dir):
        crosswalk = build_crosswalk(lake_dir)
        key = "water supply||"
        entry_ = crosswalk.by_category[key]
        assert entry_["dept"] == "PHED"
        assert entry_["support"] == 18  # every PHED row, every width
        assert entry_["share"] == 0.9  # 18 / (18 + 2 Rural Dev)

    def test_a_fragmented_office_is_withheld_at_the_category_rung(self, lake_dir):
        crosswalk = build_crosswalk(lake_dir)
        entry_ = crosswalk.by_category["roads||"]
        assert entry_["dept"] == "Works"
        assert entry_["support"] == 11
        assert "office" not in entry_

    def test_below_support_floor_keys_never_reach_the_artifact(self, lake_dir):
        crosswalk = build_crosswalk(lake_dir)
        assert "astrophysics||" not in crosswalk.by_category

    def test_the_district_rung_entries_are_correct(self, lake_dir):
        crosswalk = build_crosswalk(lake_dir)
        entry_ = crosswalk.by_category_district["sanitation||puri"]
        assert entry_["dept"] == "Health"
        assert entry_["support"] == 10
        assert entry_["office"] == "CDMO Puri Office"

    def test_the_built_crosswalk_is_directly_usable_for_the_live_callers_shape(
        self, lake_dir
    ):
        """End to end: build, then route with exactly the call shape
        `inference/service.py` makes -- category + district, no subcategory.
        `Sanitation`/`Puri` has no data outside this one district, so its
        category+district and bare-category entries tie exactly; `lookup()`
        breaks ties toward the earlier ladder entry, so only a correctly
        wired district rung -- ahead of bare category -- can win this."""
        crosswalk = build_crosswalk(lake_dir)
        hit = crosswalk.lookup("Sanitation", district="Puri")
        assert hit is not None
        assert hit.width == "category+district"
        assert hit.dept == "Health"
        assert hit.office == "CDMO Puri Office"
