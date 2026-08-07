"""Governed metric definitions over the lake (#77).

Synthetic Parquet fixtures written to ``tmp_path`` only -- nothing here reads
``data/``. The real lake tables are ``complaints`` / ``action_history`` (see
``janasunani/db/models.py`` for the column set); ``dedup_groups`` and
``dedup_signatures`` are Phase 14 tables that do not exist in the lake yet
(deliberately excluded, see ``olap/materialize.py``), which is exactly what
``unique_grievance_clusters`` and ``unique_citizens_signatories`` document.
"""

from __future__ import annotations

from datetime import datetime, timezone

import polars as pl
import pytest

from janasunani.olap import metrics
from janasunani.olap.metrics import (
    REGISTRY,
    SMALL_CELL_THRESHOLD,
    MetricDefinition,
    MetricNotComputable,
    compute_metric,
    get_definition,
    list_metrics,
)


def _write_complaints(path, rows: list[dict]) -> None:
    pl.DataFrame(rows).write_parquet(path / "complaints.parquet")


# --------------------------------------------------------------------------
# The registry is introspectable without running anything
# --------------------------------------------------------------------------


class TestTheRegistryStandsAloneAsAQueryTarget:
    def test_lists_the_three_headline_counts(self):
        ids = {d.id for d in list_metrics()}
        assert ids == {
            "total_filings",
            "unique_grievance_clusters",
            "unique_citizens_signatories",
        }

    def test_listing_touches_no_filesystem_or_lake(self, monkeypatch):
        """Introspection must not require a lake directory at all -- a caller
        listing definitions for a UI or a docs page has no ``tmp_path``."""
        import janasunani.olap.lake as lake_module

        def _boom(*a, **k):
            raise AssertionError("list_metrics() touched the lake")

        monkeypatch.setattr(lake_module, "connect", _boom)
        monkeypatch.setattr(lake_module, "query", _boom)
        assert len(list_metrics()) == 3

    def test_get_definition_of_an_unknown_metric_raises(self):
        with pytest.raises(KeyError):
            get_definition("pendency_by_district")  # existing-portal metric, not ours

    def test_every_definition_states_a_name_description_and_denominator(self):
        for definition in list_metrics():
            assert definition.name
            assert definition.description
            assert definition.denominator

    def test_every_definition_is_either_computable_or_says_why_not(self):
        for definition in list_metrics():
            if definition.computable:
                assert definition.sql is not None
                assert "SELECT" in definition.sql.upper()
                assert definition.unavailable_reason is None
            else:
                assert definition.sql is None
                assert definition.unavailable_reason


# --------------------------------------------------------------------------
# Governance is enforced at registration time, not left to caller discretion
# --------------------------------------------------------------------------


class TestGovernanceIsEnforcedInTheLayer:
    def test_a_metric_with_no_denominator_is_rejected(self):
        with pytest.raises(ValueError, match="denominator"):
            MetricDefinition(
                id="bad",
                name="Bad",
                description="d",
                denominator="",
                tables=("complaints",),
                source="complaints",
                measure="count(*)",
            )

    def test_a_metric_with_both_a_query_and_an_unavailable_reason_is_rejected(self):
        with pytest.raises(ValueError):
            MetricDefinition(
                id="bad",
                name="Bad",
                description="d",
                denominator="all complaints",
                tables=("complaints",),
                source="complaints",
                measure="count(*)",
                unavailable_reason="needs a table that doesn't exist",
            )

    def test_a_metric_with_neither_a_query_nor_a_reason_is_rejected(self):
        with pytest.raises(ValueError):
            MetricDefinition(
                id="bad",
                name="Bad",
                description="d",
                denominator="all complaints",
                tables=("complaints",),
            )

    def test_selecting_the_raw_grievance_column_is_rejected(self):
        """ROADMAP §3.2: no query in the metrics path selects
        `complaints.grievance`. The check runs at definition time, so a
        badly-written metric never reaches the registry at all."""
        with pytest.raises(ValueError, match="grievance"):
            MetricDefinition(
                id="bad",
                name="Bad",
                description="d",
                denominator="all complaints",
                tables=("complaints",),
                source="complaints",
                measure="count(distinct grievance)",
            )

    def test_selecting_the_qualified_raw_grievance_column_is_also_rejected(self):
        with pytest.raises(ValueError, match="grievance"):
            MetricDefinition(
                id="bad",
                name="Bad",
                description="d",
                denominator="all complaints",
                tables=("complaints",),
                source="complaints c",
                measure="count(distinct c.grievance)",
            )

    def test_reading_grievance_redacted_is_allowed(self):
        """The ban is specifically the raw column -- `grievance_redacted` and
        the `grievance_redactions` table name must not trip the same guard."""
        definition = MetricDefinition(
            id="redacted_ok",
            name="ok",
            description="d",
            denominator="all redactions",
            tables=("grievance_redactions",),
            source="grievance_redactions",
            measure="count(*) FILTER (WHERE grievance_redacted IS NOT NULL)",
        )
        assert definition.computable

    def test_duplicate_metric_ids_are_rejected(self):
        a = MetricDefinition(
            id="dup",
            name="A",
            description="d",
            denominator="x",
            tables=("complaints",),
            source="complaints",
            measure="count(*)",
        )
        b = MetricDefinition(
            id="dup",
            name="B",
            description="d",
            denominator="x",
            tables=("complaints",),
            source="complaints",
            measure="count(*)",
        )
        with pytest.raises(ValueError, match="duplicate"):
            metrics._build_registry(a, b)


# --------------------------------------------------------------------------
# total_filings: computable, tested for real arithmetic and its denominator
# --------------------------------------------------------------------------


class TestTotalFilings:
    def test_counts_every_row_including_duplicate_filings(self, tmp_path):
        """A campaign (many rows, same underlying issue) must not be
        collapsed -- this metric answers 'how much work arrived'."""
        _write_complaints(
            tmp_path,
            [
                {"ticket_no": "T1", "district": "Cuttack", "category": "Water"},
                {"ticket_no": "T2", "district": "Cuttack", "category": "Water"},
                {"ticket_no": "T3", "district": "Puri", "category": "Roads"},
            ],
        )
        results = compute_metric("total_filings", lake_dir=tmp_path)

        assert len(results) == 1
        result = results[0]
        assert result.value == 3
        assert result.dimensions == {}
        assert result.suppressed is False
        # every result carries its denominator; never a bare number
        assert result.denominator == get_definition("total_filings").denominator
        assert result.denominator

    def test_lake_as_of_reflects_the_complaints_file_mtime(self, tmp_path):
        _write_complaints(tmp_path, [{"ticket_no": "T1"}])
        before = datetime.fromtimestamp(
            (tmp_path / "complaints.parquet").stat().st_mtime, tz=timezone.utc
        ).replace(tzinfo=None)

        result = compute_metric("total_filings", lake_dir=tmp_path)[0]

        assert result.lake_as_of == before
        assert result.lake_as_of.tzinfo is None  # naive UTC, not aware

    def test_missing_complaints_table_raises_not_computable(self, tmp_path):
        with pytest.raises(MetricNotComputable):
            compute_metric("total_filings", lake_dir=tmp_path)


class TestSmallCellSuppressionIsEnforcedNotOptional:
    def test_a_cell_below_the_threshold_is_suppressed(self, tmp_path):
        rows = [
            {"ticket_no": f"A{i}", "district": "Cuttack", "category": "Water"}
            for i in range(SMALL_CELL_THRESHOLD + 2)  # comfortably over
        ] + [
            {"ticket_no": f"B{i}", "district": "Nabarangpur", "category": "Water"}
            for i in range(SMALL_CELL_THRESHOLD - 1)  # under the floor
        ]
        _write_complaints(tmp_path, rows)

        results = compute_metric(
            "total_filings", group_by=["district"], lake_dir=tmp_path
        )
        by_district = {r.dimensions["district"]: r for r in results}

        big = by_district["Cuttack"]
        assert big.value == SMALL_CELL_THRESHOLD + 2
        assert big.suppressed is False

        small = by_district["Nabarangpur"]
        assert small.value is None  # the count itself is withheld
        assert small.suppressed is True
        # the dimension label survives -- suppression hides the count, not
        # the fact that the district exists in the slice
        assert small.dimensions == {"district": "Nabarangpur"}

    def test_a_cell_exactly_at_the_threshold_is_not_suppressed(self, tmp_path):
        rows = [
            {"ticket_no": f"A{i}", "district": "Cuttack"}
            for i in range(SMALL_CELL_THRESHOLD)
        ]
        _write_complaints(tmp_path, rows)

        result = compute_metric(
            "total_filings", group_by=["district"], lake_dir=tmp_path
        )[0]
        assert result.value == SMALL_CELL_THRESHOLD
        assert result.suppressed is False

    def test_the_ungrouped_overall_total_is_never_suppressed(self, tmp_path):
        rows = [{"ticket_no": f"A{i}", "district": "Cuttack"} for i in range(3)]
        _write_complaints(tmp_path, rows)

        result = compute_metric("total_filings", lake_dir=tmp_path)[0]
        assert result.value == 3
        assert result.suppressed is False

    def test_caller_cannot_bypass_suppression(self, tmp_path):
        """There is no parameter that returns the raw, unsuppressed value --
        enforced in the layer, not left to caller discretion."""
        import inspect

        assert "suppress" not in inspect.signature(compute_metric).parameters


class TestGroupByIsRestrictedToDeclaredDimensions:
    def test_grouping_by_an_undeclared_dimension_is_rejected(self, tmp_path):
        _write_complaints(tmp_path, [{"ticket_no": "T1", "petitioner_mobile": "999"}])
        with pytest.raises(ValueError, match="petitioner_mobile"):
            compute_metric(
                "total_filings", group_by=["petitioner_mobile"], lake_dir=tmp_path
            )

    def test_grouping_by_multiple_declared_dimensions_works(self, tmp_path):
        _write_complaints(
            tmp_path,
            [
                {"ticket_no": "T1", "district": "Cuttack", "category": "Water"},
                {"ticket_no": "T2", "district": "Cuttack", "category": "Roads"},
            ],
        )
        results = compute_metric(
            "total_filings", group_by=["district", "category"], lake_dir=tmp_path
        )
        # both under the suppression floor, but both rows still come back
        assert {tuple(sorted(r.dimensions.items())) for r in results} == {
            (("category", "Roads"), ("district", "Cuttack")),
            (("category", "Water"), ("district", "Cuttack")),
        }


# --------------------------------------------------------------------------
# unique_grievance_clusters / unique_citizens_signatories: not computable yet
# --------------------------------------------------------------------------


class TestTheDedupDependentCountsAreDeclaredButNotInventedFrom:
    """#78 (the one worked spike) is blocked on the dedup index for exactly
    this reason: `dedup_groups` / `dedup_signatures` are not in
    `olap.materialize.LAKE_TABLES`. The definitions still exist -- so the
    registry stays introspectable and the three counts are named together --
    but computing either must fail loudly rather than fabricate a column."""

    @pytest.mark.parametrize(
        "metric_id", ["unique_grievance_clusters", "unique_citizens_signatories"]
    )
    def test_the_definition_states_why_it_cannot_run(self, metric_id):
        definition = get_definition(metric_id)
        assert not definition.computable
        assert definition.sql is None
        assert "dedup" in definition.unavailable_reason.lower()

    @pytest.mark.parametrize(
        "metric_id", ["unique_grievance_clusters", "unique_citizens_signatories"]
    )
    def test_computing_it_raises_rather_than_running_bogus_sql(
        self, metric_id, tmp_path
    ):
        # Even with a populated complaints table sitting right there, this
        # metric must not quietly fall back to counting complaints instead
        # of clusters/signatories.
        _write_complaints(tmp_path, [{"ticket_no": "T1"}])
        with pytest.raises(MetricNotComputable):
            compute_metric(metric_id, lake_dir=tmp_path)

    def test_dedup_groups_is_not_in_the_materialized_lake_tables(self):
        """Cross-check against the actual materializer, so this test breaks
        (rather than silently going stale) the day #50/#71 lands and these
        two metrics become computable."""
        from janasunani.olap.materialize import LAKE_TABLES

        assert "dedup_groups" not in LAKE_TABLES
        assert "dedup_signatures" not in LAKE_TABLES


# --------------------------------------------------------------------------
# Sanity: the registry's canonical objects are exactly what REGISTRY holds
# --------------------------------------------------------------------------


def test_registry_module_constant_matches_list_metrics():
    assert set(REGISTRY) == {d.id for d in list_metrics()}
