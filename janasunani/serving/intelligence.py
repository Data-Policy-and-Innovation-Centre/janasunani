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
    SupervisorDashboard,
    UnavailableArtifactProvenance,
    UnavailableClosurePanel,
    UnavailableSpikePanel,
    UnavailableWorkloadPanel,
)

# A one-row summary from the governed closure mart has precisely these aggregate
# columns. An allowlist is deliberate: accepting extra columns makes it too
# easy for a later producer to smuggle a row-level field into a serving path.
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


def _dashboard(
    *,
    closure: RecordedClosurePanel | UnavailableClosurePanel,
    unavailable_reason: str,
) -> SupervisorDashboard:
    """Build the stable response while capabilities are still backfill-gated."""

    return SupervisorDashboard(
        generated_label="Supervisor aggregate response",
        safety_note=(
            "Aggregate counts only. This endpoint reads validated published "
            "aggregate artifacts and never queries grievance text, contact "
            "details, citizen identifiers, or the lake at request time."
        ),
        workload=_workload_unavailable(unavailable_reason),
        spike=_spike_unavailable(unavailable_reason),
        closure=closure,
    )


class UnavailableSupervisorProvider:
    """Return a fully explicit response when aggregate publication is not enabled."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def dashboard(self) -> SupervisorDashboard:
        return _dashboard(
            closure=_closure_unavailable(self._reason),
            unavailable_reason=self._reason,
        )


class ArtifactSupervisorProvider:
    """Read the narrow, validated artifact seam consumed by the supervisor UI."""

    def __init__(self, findings_dir: Path) -> None:
        self._findings_dir = findings_dir.resolve()
        data_root = DATA_DIR.resolve()
        if self._findings_dir == data_root or self._findings_dir.is_relative_to(data_root):
            raise ValueError(
                "supervisor aggregate artifacts must not be served from the data directory"
            )

    def dashboard(self) -> SupervisorDashboard:
        closure = self._load_closure()
        common_reason = (
            "No validated aggregate artifact is available for this capability. "
            "The manual confirmed-duplicates baseline is not a substitute for "
            "deduplicated workload or a worked spike."
        )
        return _dashboard(
            closure=closure
            if closure is not None
            else _closure_unavailable(
                "No publishable closure aggregate artifact was found, or it did "
                "not pass the required aggregate schema and arithmetic checks."
            ),
            unavailable_reason=common_reason,
        )

    def _load_closure(self) -> RecordedClosurePanel | None:
        candidates = [
            self._findings_dir / name
            for name in _CLOSURE_ARTIFACT_NAMES
            if _is_regular_artifact(self._findings_dir / name, self._findings_dir)
        ]
        # A file could be a stale legacy copy beside the current producer's
        # output. The service cannot prove which one is intended, so it returns
        # no number until the operator resolves the ambiguity.
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
            # Do not log a producer-controlled row or header. API and deploy
            # logs can travel much farther than the trusted artifact directory.
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

    # A headline share with no base is undefined, not zero.  It must remain
    # unavailable until a non-empty, publishable aggregate exists.
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


def _assert_percentage(actual: float, expected: float) -> None:
    if not math.isclose(actual, expected, abs_tol=_PERCENT_TOLERANCE):
        raise ValueError("closure artifact percentage does not match its counts")


def supervisor_provider_from_env() -> SupervisorProvider:
    """Enable the aggregate seam only when an operator configures it explicitly."""

    configured = os.environ.get("JANASUNANI_SUPERVISOR_FINDINGS_DIR")
    if not configured:
        return UnavailableSupervisorProvider(
            "No supervisor aggregate artifact directory has been configured."
        )
    try:
        return ArtifactSupervisorProvider(Path(configured))
    except (OSError, RuntimeError, ValueError):
        # Do not echo a potentially sensitive configured path in a response.
        return UnavailableSupervisorProvider(
            "The configured supervisor aggregate artifact directory is not allowed."
        )
