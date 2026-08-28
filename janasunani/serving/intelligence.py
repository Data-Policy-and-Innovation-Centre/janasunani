"""Aggregate-only provider for the Phase 15 supervisor briefing.

The supervisor API does not query the lake or the dedup index. It reads only
small, published aggregate artifacts from an explicitly configured directory,
then validates their schema and arithmetic before returning a minimal DTO.
This keeps the serving boundary on the safe side of the lake and makes a
missing or stale capability visible as unavailable rather than as a
plausible-looking substitute.

The closure reader accepts both the current closure_finding_summary.csv name
and the one-finding closure_recording_no_action.csv name introduced by the
findings-pack work. If both are present, it fails closed rather than choosing
an arbitrary version. The manual confirmed-duplicates finding is intentionally
not read here: it is an insight about existing officer labels, not the
MinHash-backed duplicate-adjusted-workload capability.

Workload and spike are read from the aggregate seam (DATA_DIR/aggregates by
default, or the configured findings directory for tests). Both carry the same
dedup source digest; a mismatch fails loudly as unavailable.
"""

from __future__ import annotations

import csv
import math
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from janasunani.config import DATA_DIR
from janasunani.serving.schemas import (
    RecordedArtifactProvenance,
    RecordedClosurePanel,
    RecordedSpikePanel,
    RecordedWorkloadPanel,
    SupervisorAggregateCount,
    SupervisorDashboard,
    SupervisorSlice,
    UnavailableArtifactProvenance,
    UnavailableClosurePanel,
    UnavailableSpikePanel,
    UnavailableWorkloadPanel,
)

_CLOSURE_FIELDS = frozenset(
    {
        "resolved_complaints",
        "ladder_closures",
        "bare",
        "with_action",
        "benefit",
        "claims_action",
        "off_ladder",
        "bare_share_of_ladder_pct",
        "bare_share_of_resolved_pct",
        "ladder_coverage_pct",
        "off_ladder_share_pct",
    }
)
_CLOSURE_ARTIFACT_NAMES = (
    "closure_recording_no_action.csv",
    "closure_finding_summary.csv",
)
_MAX_CLOSURE_ARTIFACT_BYTES = 32 * 1024
_MAX_SAFE_JSON_INTEGER = (2**53) - 1
_PERCENT_TOLERANCE = 0.051

_CLOSURE_CAVEAT = (
    "This is descriptive, not a failure rate. A bare disposal does not prove "
    "that no work occurred or that a closure was wrong; making that claim "
    "requires human adjudication."
)

_WORKLOAD_FIELDS = frozenset(
    {
        "slice_district",
        "slice_category",
        "slice_period",
        "total_filings",
        "distinct_problems",
        "duplicate_adjustment",
        "source_name",
        "source_snapshot_id",
        # #317. Corpus-wide grouping depends on records outside a slice, so
        # the slice digest above cannot tell two group assignments apart.
        "grouping_scope_snapshot_id",
    }
)
_SPIKE_FIELDS = frozenset(
    {
        "slice_district",
        "slice_category",
        "slice_period",
        "filings",
        "distinct_problems",
        "distinct_citizens",
        "source_name",
        "source_snapshot_id",
        "grouping_scope_snapshot_id",
        "interpretation",
    }
)
_WORKLOAD_ARTIFACT = "workload.csv"
_SPIKE_ARTIFACT = "spike.csv"
_MAX_AGGREGATE_BYTES = 32 * 1024

_DEDUP_SOURCE_NAME = "oltp:complaints+grievance_redactions"


class SupervisorProvider(Protocol):
    """Injectable source for the aggregate-only supervisor response."""

    def dashboard(self) -> SupervisorDashboard: ...


def _unavailable(label: str, reason: str) -> UnavailableArtifactProvenance:
    return UnavailableArtifactProvenance(label=label, reason=reason)


def _workload_unavailable(reason: str) -> UnavailableWorkloadPanel:
    return UnavailableWorkloadPanel(
        title="Duplicate-adjusted workload",
        provenance=_unavailable("Metric output unavailable", reason),
        requirement=(
            "Requires a validated aggregate release with total filings and "
            "deduplicated distinct-problem counts for the same selected slice."
        ),
    )


def _spike_unavailable(reason: str) -> UnavailableSpikePanel:
    return UnavailableSpikePanel(
        title="Worked spike decomposition",
        provenance=_unavailable("Metric output unavailable", reason),
        requirement=(
            "Requires one validated worked spike with total filings, distinct "
            "problems, and distinct citizens or signatories for the same slice, "
            "compared with the same period last year."
        ),
    )


def _closure_unavailable(reason: str) -> UnavailableClosurePanel:
    return UnavailableClosurePanel(
        title="How cases are closed",
        provenance=_unavailable("Recorded aggregate unavailable", reason),
        numerator_label="Closures on the bare disposal rung",
        primary_denominator_label="Closures matching one of the six disposal templates",
        secondary_denominator_label="All resolved complaints",
        caveat=_CLOSURE_CAVEAT,
    )


class UnavailableSupervisorProvider:
    """Return a fully explicit response when aggregate publication is not enabled."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def dashboard(self) -> SupervisorDashboard:
        return SupervisorDashboard(
            generated_label="Supervisor aggregate response",
            safety_note=(
                "Aggregate counts only. This endpoint reads validated published "
                "aggregate artifacts and never queries grievance text, contact "
                "details, citizen identifiers, or the lake at request time."
            ),
            workload=_workload_unavailable(self._reason),
            spike=_spike_unavailable(self._reason),
            closure=_closure_unavailable(self._reason),
        )


class ArtifactSupervisorProvider:
    """Read the narrow, validated artifact seam consumed by the supervisor UI."""

    def __init__(
        self,
        findings_dir: Path,
        aggregates_dir: Path | None = None,
    ) -> None:
        self._findings_dir = findings_dir.resolve()
        data_root = DATA_DIR.resolve()
        if self._findings_dir == data_root or self._findings_dir.is_relative_to(data_root):
            raise ValueError(
                "supervisor aggregate artifacts must not be served from the data directory"
            )
        if aggregates_dir is not None:
            self._aggregates_dir = aggregates_dir.resolve()
        else:
            default = (DATA_DIR / "aggregates").resolve()
            if default.exists():
                self._aggregates_dir = default
            else:
                self._aggregates_dir = self._findings_dir

    def dashboard(self) -> SupervisorDashboard:
        closure = self._load_closure()
        workload = self._load_workload()
        spike = self._load_spike()

        if workload is not None and spike is not None:
            w_row = self._read_raw_workload_row()
            s_row = self._read_raw_spike_row()
            if w_row is not None and s_row is not None:
                if w_row.get("source_snapshot_id") != s_row.get("source_snapshot_id") or w_row.get(
                    "grouping_scope_snapshot_id"
                ) != s_row.get("grouping_scope_snapshot_id"):
                    workload = None
                    spike = None
                    digest_reason = (
                        "Workload and spike aggregates carry different dedup source digests; "
                        "refusing to serve a mixed snapshot."
                    )
                    return SupervisorDashboard(
                        generated_label="Supervisor aggregate response",
                        safety_note=(
                            "Aggregate counts only. This endpoint reads validated published "
                            "aggregate artifacts and never queries grievance text, contact "
                            "details, citizen identifiers, or the lake at request time."
                        ),
                        workload=_workload_unavailable(digest_reason),
                        spike=_spike_unavailable(digest_reason),
                        closure=closure
                        if closure is not None
                        else _closure_unavailable(
                            "No publishable closure aggregate artifact was found, or it did "
                            "not pass the required aggregate schema and arithmetic checks."
                        ),
                    )
                if w_row.get("source_name") != s_row.get("source_name"):
                    workload = None
                    spike = None
                    digest_reason = (
                        "Workload and spike aggregates carry different source names; "
                        "refusing to serve a mixed snapshot."
                    )
                    return SupervisorDashboard(
                        generated_label="Supervisor aggregate response",
                        safety_note=(
                            "Aggregate counts only. This endpoint reads validated published "
                            "aggregate artifacts and never queries grievance text, contact "
                            "details, citizen identifiers, or the lake at request time."
                        ),
                        workload=_workload_unavailable(digest_reason),
                        spike=_spike_unavailable(digest_reason),
                        closure=closure
                        if closure is not None
                        else _closure_unavailable(
                            "No publishable closure aggregate artifact was found, or it did "
                            "not pass the required aggregate schema and arithmetic checks."
                        ),
                    )

        common_unavailable_reason = (
            "No validated aggregate artifact is available for this capability. "
            "The manual confirmed-duplicates baseline is not a substitute for "
            "deduplicated workload or a worked spike."
        )
        return SupervisorDashboard(
            generated_label="Supervisor aggregate response",
            safety_note=(
                "Aggregate counts only. This endpoint reads validated published "
                "aggregate artifacts and never queries grievance text, contact "
                "details, citizen identifiers, or the lake at request time."
            ),
            workload=workload if workload is not None else _workload_unavailable(common_unavailable_reason),
            spike=spike if spike is not None else _spike_unavailable(common_unavailable_reason),
            closure=closure
            if closure is not None
            else _closure_unavailable(
                "No publishable closure aggregate artifact was found, or it did "
                "not pass the required aggregate schema and arithmetic checks."
            ),
        )

    def _load_closure(self) -> RecordedClosurePanel | None:
        candidates = [
            self._findings_dir / name
            for name in _CLOSURE_ARTIFACT_NAMES
            if _is_regular_artifact(self._findings_dir / name, self._findings_dir)
        ]
        if len(candidates) != 1:
            return None

        path = candidates[0]
        if not _is_regular_artifact(path, self._findings_dir):
            return None
        try:
            row = _read_closure_row(path)
            _validate_closure_row(row)
            written_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except (OSError, UnicodeError, ValueError, csv.Error):
            return None

        return RecordedClosurePanel(
            title="How cases are closed",
            provenance=RecordedArtifactProvenance(
                label="Recorded aggregate artifact",
                artifact=path.name,
                artifact_written_at=written_at,
            ),
            numerator_label="Closures on the bare disposal rung",
            numerator=int(row["bare"]),
            primary_denominator_label=(
                "Closures matching one of the six disposal templates"
            ),
            primary_denominator=int(row["ladder_closures"]),
            primary_share_pct=float(row["bare_share_of_ladder_pct"]),
            secondary_denominator_label="All resolved complaints",
            secondary_denominator=int(row["resolved_complaints"]),
            secondary_share_pct=float(row["bare_share_of_resolved_pct"]),
            caveat=_CLOSURE_CAVEAT,
        )

    def _load_workload(self) -> RecordedWorkloadPanel | None:
        for base in (self._aggregates_dir, self._findings_dir):
            path = base / _WORKLOAD_ARTIFACT
            if not _is_regular_aggregate(path, base):
                continue
            try:
                row = _read_workload_row(path)
                _validate_workload_row(row)
                written_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except (OSError, UnicodeError, ValueError, csv.Error):
                return None
            return RecordedWorkloadPanel(
                title="Duplicate-adjusted workload",
                slice=SupervisorSlice(
                    district=str(row["slice_district"]),
                    category=str(row["slice_category"]),
                    period=str(row["slice_period"]),
                ),
                provenance=RecordedArtifactProvenance(
                    label="Recorded aggregate artifact",
                    artifact=path.name,
                    artifact_written_at=written_at,
                ),
                total_filings=SupervisorAggregateCount(
                    label="Total filings (portal count)",
                    value=int(row["total_filings"]),
                    explanation="All complaints in the slice, undeduplicated.",
                ),
                distinct_problems=SupervisorAggregateCount(
                    label="Distinct problems (dedup groups)",
                    value=int(row["distinct_problems"]),
                    explanation="Distinct grievance clusters after MinHash/LSH dedup.",
                ),
                duplicate_adjustment=SupervisorAggregateCount(
                    label="Duplicate adjustment",
                    value=int(row["duplicate_adjustment"]),
                    explanation="Filings that are extra copies beyond the first per group.",
                ),
            )
        return None

    def _load_spike(self) -> RecordedSpikePanel | None:
        for base in (self._aggregates_dir, self._findings_dir):
            path = base / _SPIKE_ARTIFACT
            if not _is_regular_aggregate(path, base):
                continue
            try:
                row = _read_spike_row(path)
                _validate_spike_row(row)
                written_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except (OSError, UnicodeError, ValueError, csv.Error):
                return None
            return RecordedSpikePanel(
                title="Worked spike decomposition",
                slice=SupervisorSlice(
                    district=str(row["slice_district"]),
                    category=str(row["slice_category"]),
                    period=str(row["slice_period"]),
                ),
                provenance=RecordedArtifactProvenance(
                    label="Recorded aggregate artifact",
                    artifact=path.name,
                    artifact_written_at=written_at,
                ),
                interpretation=str(row["interpretation"]),
                counts=(
                    SupervisorAggregateCount(
                        label="Total filings in spike week",
                        value=int(row["filings"]),
                        explanation="Filings in the spike week, undeduplicated.",
                    ),
                    SupervisorAggregateCount(
                        label="Distinct problems in spike week",
                        value=int(row["distinct_problems"]),
                        explanation="Distinct dedup groups among spike filings.",
                    ),
                    SupervisorAggregateCount(
                        label="Distinct citizens in spike week",
                        value=int(row["distinct_citizens"]),
                        explanation="Distinct signatories via salted identity keys.",
                    ),
                ),
            )
        return None

    def _read_raw_workload_row(self) -> dict[str, str] | None:
        for base in (self._aggregates_dir, self._findings_dir):
            path = base / _WORKLOAD_ARTIFACT
            if not _is_regular_aggregate(path, base):
                continue
            try:
                with path.open(newline="", encoding="utf-8") as handle:
                    reader = csv.DictReader(handle)
                    rows = list(reader)
                    if len(rows) == 1:
                        return rows[0]
            except (OSError, UnicodeError, csv.Error):
                return None
        return None

    def _read_raw_spike_row(self) -> dict[str, str] | None:
        for base in (self._aggregates_dir, self._findings_dir):
            path = base / _SPIKE_ARTIFACT
            if not _is_regular_aggregate(path, base):
                continue
            try:
                with path.open(newline="", encoding="utf-8") as handle:
                    reader = csv.DictReader(handle)
                    rows = list(reader)
                    if len(rows) == 1:
                        return rows[0]
            except (OSError, UnicodeError, csv.Error):
                return None
        return None


def _is_regular_artifact(path: Path, findings_dir: Path) -> bool:
    """Reject symlinks and oversized or escaped files before parsing them."""

    try:
        path.relative_to(findings_dir)
        if path.is_symlink():
            return False
        metadata = path.stat()
        return (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_size <= _MAX_CLOSURE_ARTIFACT_BYTES
        )
    except (OSError, ValueError):
        return False


def _is_regular_aggregate(path: Path, base_dir: Path) -> bool:
    try:
        path.relative_to(base_dir)
        if path.is_symlink():
            return False
        metadata = path.stat()
        return stat.S_ISREG(metadata.st_mode) and metadata.st_size <= _MAX_AGGREGATE_BYTES
    except (OSError, ValueError):
        return False


def _read_closure_row(path: Path) -> dict[str, int | float]:
    """Read exactly one allowed aggregate row, never passing source strings on."""

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames
        if (
            headers is None
            or len(headers) != len(_CLOSURE_FIELDS)
            or set(headers) != _CLOSURE_FIELDS
        ):
            raise ValueError("closure artifact has an unexpected aggregate schema")
        rows = list(reader)

    if len(rows) != 1 or set(rows[0]) != _CLOSURE_FIELDS:
        raise ValueError("closure artifact must contain exactly one aggregate row")

    row = rows[0]
    return {
        name: _parse_nonnegative_int(row[name])
        for name in (
            "resolved_complaints",
            "ladder_closures",
            "bare",
            "with_action",
            "benefit",
            "claims_action",
            "off_ladder",
        )
    } | {
        name: _parse_percentage(row[name])
        for name in (
            "bare_share_of_ladder_pct",
            "bare_share_of_resolved_pct",
            "ladder_coverage_pct",
            "off_ladder_share_pct",
        )
    }


def _read_workload_row(path: Path) -> dict[str, object]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames
        if headers is None or set(headers) != _WORKLOAD_FIELDS or len(headers) != len(_WORKLOAD_FIELDS):
            raise ValueError("workload artifact has an unexpected aggregate schema")
        rows = list(reader)
    if len(rows) != 1 or set(rows[0]) != _WORKLOAD_FIELDS:
        raise ValueError("workload artifact must contain exactly one aggregate row")
    row = rows[0]
    total = _parse_nonnegative_int(row["total_filings"])
    distinct = _parse_nonnegative_int(row["distinct_problems"])
    adjustment = _parse_nonnegative_int(row["duplicate_adjustment"])
    for key in ("slice_district", "slice_category", "slice_period", "source_name", "source_snapshot_id", "grouping_scope_snapshot_id"):
        val = row.get(key)
        if not isinstance(val, str) or not val or val.strip() != val:
            raise ValueError(f"workload {key} is missing or has whitespace")
    if row["source_name"] != _DEDUP_SOURCE_NAME:
        raise ValueError("workload source_name mismatch")
    if not row["source_snapshot_id"].startswith("sha256:"):
        raise ValueError("workload source_snapshot_id must be sha256")
    return {
        "slice_district": row["slice_district"],
        "slice_category": row["slice_category"],
        "slice_period": row["slice_period"],
        "total_filings": total,
        "distinct_problems": distinct,
        "duplicate_adjustment": adjustment,
        "source_name": row["source_name"],
        "source_snapshot_id": row["source_snapshot_id"],
        "grouping_scope_snapshot_id": row["grouping_scope_snapshot_id"],
    }


def _read_spike_row(path: Path) -> dict[str, object]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames
        if headers is None or set(headers) != _SPIKE_FIELDS or len(headers) != len(_SPIKE_FIELDS):
            raise ValueError("spike artifact has an unexpected aggregate schema")
        rows = list(reader)
    if len(rows) != 1 or set(rows[0]) != _SPIKE_FIELDS:
        raise ValueError("spike artifact must contain exactly one aggregate row")
    row = rows[0]
    filings = _parse_nonnegative_int(row["filings"])
    distinct = _parse_nonnegative_int(row["distinct_problems"])
    citizens = _parse_nonnegative_int(row["distinct_citizens"])
    for key in ("slice_district", "slice_category", "slice_period", "source_name", "source_snapshot_id", "grouping_scope_snapshot_id", "interpretation"):
        val = row.get(key)
        if not isinstance(val, str) or not val or val.strip() != val:
            raise ValueError(f"spike {key} is missing or has whitespace")
    if row["source_name"] != _DEDUP_SOURCE_NAME:
        raise ValueError("spike source_name mismatch")
    if not row["source_snapshot_id"].startswith("sha256:"):
        raise ValueError("spike source_snapshot_id must be sha256")
    return {
        "slice_district": row["slice_district"],
        "slice_category": row["slice_category"],
        "slice_period": row["slice_period"],
        "filings": filings,
        "distinct_problems": distinct,
        "distinct_citizens": citizens,
        "source_name": row["source_name"],
        "source_snapshot_id": row["source_snapshot_id"],
        "grouping_scope_snapshot_id": row["grouping_scope_snapshot_id"],
        "interpretation": row["interpretation"],
    }


def _parse_nonnegative_int(value: str | None) -> int:
    if value is None or not value or value.strip() != value:
        raise ValueError("aggregate count is missing")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError("aggregate count is not an integer") from exc
    if (
        parsed < 0
        or parsed > _MAX_SAFE_JSON_INTEGER
        or str(parsed) != value
    ):
        raise ValueError("aggregate count is outside the allowed range")
    return parsed


def _parse_percentage(value: str | None) -> float:
    if value is None or not value or value.strip() != value:
        raise ValueError("aggregate percentage is missing")
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError("aggregate percentage is not numeric") from exc
    if not math.isfinite(parsed) or not 0 <= parsed <= 100:
        raise ValueError("aggregate percentage is outside the allowed range")
    return parsed


def _validate_closure_row(row: dict[str, int | float]) -> None:
    """Verify the same aggregate identities the headline relies on."""

    resolved = int(row["resolved_complaints"])
    ladder = int(row["ladder_closures"])
    bare = int(row["bare"])
    with_action = int(row["with_action"])
    benefit = int(row["benefit"])
    claims_action = int(row["claims_action"])
    off_ladder = int(row["off_ladder"])

    if resolved == 0 or ladder == 0:
        raise ValueError("closure artifact has no publishable denominator")

    if (
        bare + claims_action != ladder
        or with_action + benefit != claims_action
        or ladder + off_ladder != resolved
    ):
        raise ValueError("closure artifact aggregate identities do not reconcile")

    _assert_percentage(
        float(row["bare_share_of_ladder_pct"]),
        100.0 * bare / ladder if ladder else 0.0,
    )
    _assert_percentage(
        float(row["bare_share_of_resolved_pct"]),
        100.0 * bare / resolved if resolved else 0.0,
    )
    _assert_percentage(
        float(row["ladder_coverage_pct"]),
        100.0 * ladder / resolved if resolved else 0.0,
    )
    _assert_percentage(
        float(row["off_ladder_share_pct"]),
        100.0 * off_ladder / resolved if resolved else 0.0,
    )


def _validate_workload_row(row: dict[str, object]) -> None:
    total = int(row["total_filings"])  # type: ignore
    distinct = int(row["distinct_problems"])  # type: ignore
    adj = int(row["duplicate_adjustment"])  # type: ignore
    if total == 0:
        raise ValueError("workload has no filings")
    if distinct == 0:
        raise ValueError("workload has no distinct problems")
    if distinct > total:
        raise ValueError("distinct problems cannot exceed total filings")
    if adj != total - distinct:
        raise ValueError("workload duplicate_adjustment must equal total_filings - distinct_problems")


def _validate_spike_row(row: dict[str, object]) -> None:
    filings = int(row["filings"])  # type: ignore
    distinct = int(row["distinct_problems"])  # type: ignore
    citizens = int(row["distinct_citizens"])  # type: ignore
    if filings == 0:
        raise ValueError("spike has no filings")
    if distinct == 0 or distinct > filings:
        raise ValueError("spike distinct_problems invalid")
    if citizens == 0 or citizens > filings:
        raise ValueError("spike distinct_citizens invalid")


def _assert_percentage(actual: float, expected: float) -> None:
    if not math.isclose(actual, expected, abs_tol=_PERCENT_TOLERANCE):
        raise ValueError("closure artifact percentage does not match its counts")


def supervisor_provider_from_env() -> SupervisorProvider:
    """Enable the aggregate seam only when an operator configures it explicitly."""

    configured = os.environ.get("JANASUNANI_SUPERVISOR_FINDINGS_DIR")
    aggregates_configured = os.environ.get("JANASUNANI_SUPERVISOR_AGGREGATES_DIR")
    if not configured:
        return UnavailableSupervisorProvider(
            "No supervisor aggregate artifact directory has been configured."
        )
    try:
        findings = Path(configured)
        aggregates = Path(aggregates_configured) if aggregates_configured else None
        return ArtifactSupervisorProvider(findings, aggregates_dir=aggregates)
    except (OSError, RuntimeError, ValueError):
        return UnavailableSupervisorProvider(
            "The configured supervisor aggregate artifact directory is not allowed."
        )
