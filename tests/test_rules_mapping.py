"""Tests for the CSV-backed mapping router (``janasunani.routing.mappings``
and ``janasunani.routing.rules.MappingRouter``).

The real-CSV-path tests are skipped when the ``data/raw/janasunani-mappings``
DVC data hasn't been materialized locally (the expected state on CI and any
machine that hasn't run ``dvc pull`` for this dataset) -- mirroring the
skip-on-missing-local-asset pattern used elsewhere in this suite (see
``tests/test_pipeline.py``'s tesseract/model skip). The no-CSV fallback path is
always exercised, real files or not.
"""

from pathlib import Path

import pytest

from janasunani.config import RAW_DATA_DIR
from janasunani.routing.mappings import load_mapping_tables
from janasunani.routing.rules import DEFAULT_RULES, FALLBACK_DEPT, MappingRouter, RuleRouter

MAPPING_DIR = RAW_DATA_DIR / "janasunani-mappings"

needs_mapping_csvs = pytest.mark.skipif(
    not (MAPPING_DIR / "m_admin_category.csv").exists(),
    reason="needs the DVC-pulled janasunani-mappings master tables",
)


def test_load_mapping_tables_returns_none_when_directory_missing(tmp_path):
    assert load_mapping_tables(tmp_path / "does-not-exist") is None


def test_mapping_router_falls_back_to_legacy_rules_when_csvs_absent(tmp_path):
    router = MappingRouter(mapping_dir=tmp_path / "does-not-exist")

    assert router.mapping_loaded is False

    # Still routes correctly via the illustrative RuleRouter layer.
    result = router.route(category="Water Supply", district="Puri")
    assert result.method == "rules"
    assert result.dept == "Rural Water Supply & Sanitation"

    # And still degrades to the shared generic fallback for a truly unknown
    # category, exactly like a bare RuleRouter would.
    fallback = router.route(category="Nothing Recognizable", district="Ganjam")
    assert fallback.method == "fallback"
    assert fallback.dept == FALLBACK_DEPT


def test_mapping_router_matches_legacy_router_when_csvs_absent():
    """With no mapping data, MappingRouter must reproduce RuleRouter exactly
    (same fallback behavior the pre-existing tests pin down)."""
    router = MappingRouter(mapping_dir=Path("/nonexistent-mapping-dir"))
    legacy = RuleRouter()

    for category, subcategory in [
        ("Drinking Water Supply", "Hand pump repair"),
        ("Electricity", None),
        ("Unmapped Category", "Unmapped Subcategory"),
    ]:
        got = router.route(category=category, subcategory=subcategory, district="Cuttack")
        want = legacy.route(category=category, subcategory=subcategory, district="Cuttack")
        assert got == want


@needs_mapping_csvs
class TestMappingRouterWithRealCsvs:
    def test_loads_real_tables(self):
        tables = load_mapping_tables()
        assert tables is not None
        assert len(tables.categories) > 0
        assert len(tables.departments) > 0
        # At least the categories that are also literal department names
        # ("Energy", "Excise", "Tourism", "Disaster Management" on the current
        # snapshot) must resolve -- the only non-fabricated bridge available.
        assert len(tables.category_to_department) > 0

    def test_energy_category_resolves_to_real_department_and_escalation(self):
        """Energy is both a real category and a real department name, and the
        department has an explicit, non-generic escalation chain on file
        (the four Odisha power-distribution company CEOs) -- this is the
        clearest end-to-end real join available in the master tables."""
        router = MappingRouter()
        assert router.mapping_loaded is True

        result = router.route(category="Energy", district="Cuttack")

        assert result.method == "rules"
        assert result.dept == "Energy"
        assert result.confidence == 0.9
        assert result.designation is not None
        assert result.escalation_authority is not None
        assert "Cuttack" in result.office

    def test_odia_category_text_matches_real_category(self):
        """Real grievances are Odia; category matching must work on the Odia
        column, not just English."""
        router = MappingRouter()
        tables = load_mapping_tables()
        energy = next(c for c in tables.categories if c.name_en == "Energy")
        assert energy.name_od

        result = router.route(category=energy.name_od, district="Khordha")
        assert result.method == "rules"
        assert result.dept == "Energy"

    def test_category_without_derivable_department_falls_back_to_legacy_rules(self):
        """Most real categories have no department FK in the master tables
        (documented gap); MappingRouter must fall through to the illustrative
        RuleRouter layer rather than fabricate a department."""
        router = MappingRouter()
        tables = load_mapping_tables()

        # "Water Supply" is a real category (ids 39/40) but its name does not
        # equal any real department name, so it cannot resolve via the CSVs.
        assert tables.find_category("Water Supply") is not None
        assert "Water Supply" not in {
            tables.find_department(d).name_en
            for cat_id, d in tables.category_to_department.items()
        }

        result = router.route(category="Water Supply", district="Puri")
        assert result.method == "rules"
        assert result.dept == "Rural Water Supply & Sanitation"  # legacy DEFAULT_RULES hit

    def test_unrecognized_category_text_falls_back_to_generic(self):
        router = MappingRouter()
        result = router.route(category="Not A Real Category At All", district="Ganjam")
        assert result.method == "fallback"
        assert result.dept == FALLBACK_DEPT

    def test_default_rules_unaffected(self):
        """Sanity check that adding MappingRouter didn't touch the legacy
        fixtures the pre-existing router tests pin down."""
        assert len(DEFAULT_RULES) == 7
