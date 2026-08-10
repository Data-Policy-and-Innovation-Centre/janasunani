"""Validate and import cached Sarvam aggregate evidence into MLflow.

This command never calls Sarvam.  It converts the privacy-safe aggregate file
under ``docs/evidence`` into comparable MLflow benchmark runs so the paid work
already completed remains visible alongside local candidates.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from janasunani.tracking.mlflow_utils import log_benchmark_run


SCHEMA_VERSION = "janasunani.sarvam-cached-evidence/v1"
DEFAULT_EVIDENCE = Path("docs/evidence/sarvam_cached_benchmark.json")
_ROOT_FIELDS = {
    "schema_version", "as_of", "provider", "model", "slice",
    "extract_schema_version", "normalizer_version",
    "credits_available_for_new_calls", "reproducibility", "runs", "reporting_rule",
}
_RUN_FIELDS = {
    "run_id", "status", "arm", "pages_submitted", "pages_attempted",
    "pages_paired_scored", "pages_excluded", "tickets", "provider_failures",
    "accepted_jobs", "accepted_digitise_jobs", "accepted_extract_jobs",
    "completed_jobs", "completed_digitise_jobs", "completed_extract_jobs",
    "provider_job_failures", "credit_exhaustion_http_402_submissions",
    "recorded_cost_rupees", "estimated_list_price_accepted_jobs_rupees",
    "estimated_list_price_completed_jobs_rupees", "actual_billing_available",
    "normalized_exact_text_divergence", "pytesseract_normalized_characters",
    "sarvam_normalized_characters", "sarvam_to_pytesseract_character_ratio",
    "sarvam_longer_pages", "pytesseract_longer_pages", "sample_manifest_pages",
    "sample_manifest_tickets", "sample_manifest_categories", "sample_seed",
    "cost_basis", "source", "limitations",
}
_REPRODUCIBILITY_FIELDS = {
    "tracked_aggregate_only",
    "source_artifacts_tracked",
    "source_artifact_hashes_available",
    "derivation_command_recorded",
    "latency_distribution_available",
    "claim_limit",
}
_COUNT_FIELDS = {
    "pages_submitted",
    "pages_attempted",
    "pages_paired_scored",
    "pages_excluded",
    "tickets",
    "provider_failures",
    "accepted_jobs",
    "accepted_digitise_jobs",
    "accepted_extract_jobs",
    "completed_jobs",
    "completed_digitise_jobs",
    "completed_extract_jobs",
    "provider_job_failures",
    "credit_exhaustion_http_402_submissions",
    "pytesseract_normalized_characters",
    "sarvam_normalized_characters",
    "sarvam_longer_pages",
    "pytesseract_longer_pages",
    "sample_manifest_pages",
    "sample_manifest_tickets",
    "sample_manifest_categories",
    "sample_seed",
}
_COST_FIELDS = {
    "recorded_cost_rupees",
    "estimated_list_price_accepted_jobs_rupees",
    "estimated_list_price_completed_jobs_rupees",
}


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _finite_number(value: object, *, field: str, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < minimum:
        raise ValueError(f"{field} must be a finite number >= {minimum}")
    return numeric


def load_evidence(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("cached Sarvam evidence root must be an object")
    unknown_root = set(payload) - _ROOT_FIELDS
    if unknown_root:
        raise ValueError(f"cached Sarvam evidence has unknown fields: {sorted(unknown_root)}")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported cached Sarvam evidence schema")
    required_root = {
        "as_of", "provider", "model", "slice", "extract_schema_version",
        "normalizer_version", "credits_available_for_new_calls", "runs",
        "reporting_rule",
    }
    missing_root = required_root - set(payload)
    if missing_root:
        raise ValueError(f"cached Sarvam evidence is missing fields: {sorted(missing_root)}")
    try:
        date.fromisoformat(payload["as_of"])
    except (TypeError, ValueError) as exc:
        raise ValueError("cached Sarvam evidence as_of must be an ISO date") from exc
    for field in (
        "provider", "model", "slice", "extract_schema_version",
        "normalizer_version", "reporting_rule",
    ):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise ValueError(f"cached Sarvam evidence {field} must be a non-empty string")
    if not isinstance(payload["credits_available_for_new_calls"], bool):
        raise ValueError("credits_available_for_new_calls must be a boolean")
    reproducibility = payload.get("reproducibility")
    if reproducibility is not None:
        if not isinstance(reproducibility, Mapping):
            raise ValueError("reproducibility must be an object")
        if set(reproducibility) != _REPRODUCIBILITY_FIELDS:
            raise ValueError("reproducibility has an unexpected shape")
        for field in _REPRODUCIBILITY_FIELDS - {"claim_limit"}:
            if not isinstance(reproducibility[field], bool):
                raise ValueError(f"reproducibility.{field} must be a boolean")
        if not isinstance(reproducibility["claim_limit"], str) or not reproducibility[
            "claim_limit"
        ].strip():
            raise ValueError("reproducibility.claim_limit must be a non-empty string")
    runs = payload.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("cached Sarvam evidence requires at least one run")
    run_ids: set[str] = set()
    for index, run in enumerate(runs):
        if not isinstance(run, Mapping):
            raise ValueError(f"cached Sarvam run {index} must be an object")
        unknown_run = set(run) - _RUN_FIELDS
        if unknown_run:
            raise ValueError(
                f"cached Sarvam run {index} has unknown fields: {sorted(unknown_run)}"
            )
        for field in ("run_id", "status", "arm", "pages_paired_scored"):
            if run.get(field) in (None, ""):
                raise ValueError(f"cached Sarvam run {index} is missing {field}")
        run_id = run["run_id"]
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError(f"cached Sarvam run {index} run_id must be a string")
        if run_id in run_ids:
            raise ValueError(f"cached Sarvam run {index} duplicates run_id {run_id!r}")
        run_ids.add(run_id)
        if not isinstance(run["status"], str) or run["status"] not in {
            "completed", "interrupted_credit_exhaustion",
        }:
            raise ValueError(f"cached Sarvam run {index} has an invalid status")
        if not isinstance(run["arm"], str) or run["arm"] not in {
            "digitise", "extract", "both",
        }:
            raise ValueError(f"cached Sarvam run {index} has an invalid arm")
        counts = {
            field: _nonnegative_int(run[field], field=f"runs[{index}].{field}")
            for field in _COUNT_FIELDS
            if field in run
        }
        attempted = _nonnegative_int(
            run.get("pages_attempted", run.get("pages_submitted", 0)),
            field=f"runs[{index}].pages_attempted",
        )
        scored = _nonnegative_int(
            run["pages_paired_scored"], field=f"runs[{index}].pages_paired_scored"
        )
        if scored > attempted:
            raise ValueError(f"cached Sarvam run {index} scored more pages than attempted")
        if "pages_excluded" in counts and counts["pages_excluded"] != attempted - scored:
            raise ValueError(
                f"cached Sarvam run {index} pages_excluded must equal attempted minus scored"
            )
        accepted = counts.get("accepted_jobs")
        completed = counts.get("completed_jobs")
        job_failures = counts.get("provider_job_failures")
        if accepted is not None and completed is not None and completed > accepted:
            raise ValueError(f"cached Sarvam run {index} completed more jobs than accepted")
        if (
            accepted is not None
            and completed is not None
            and job_failures is not None
            and completed + job_failures != accepted
        ):
            raise ValueError(
                f"cached Sarvam run {index} completed jobs plus failures must equal accepted jobs"
            )
        accepted_digitise = counts.get("accepted_digitise_jobs")
        accepted_extract = counts.get("accepted_extract_jobs")
        if (
            accepted is not None
            and accepted_digitise is not None
            and accepted_extract is not None
            and accepted_digitise + accepted_extract != accepted
        ):
            raise ValueError(
                f"cached Sarvam run {index} accepted arm jobs must sum to accepted jobs"
            )
        completed_digitise = counts.get("completed_digitise_jobs")
        completed_extract = counts.get("completed_extract_jobs")
        if (
            completed is not None
            and completed_digitise is not None
            and completed_extract is not None
            and completed_digitise + completed_extract != completed
        ):
            raise ValueError(
                f"cached Sarvam run {index} completed arm jobs must sum to completed jobs"
            )
        divergence = _finite_number(
            run.get("normalized_exact_text_divergence"),
            field=f"runs[{index}].normalized_exact_text_divergence",
        )
        if divergence > 1.0:
            raise ValueError(f"cached Sarvam run {index} divergence must be in [0,1]")
        _finite_number(
            run.get("sarvam_to_pytesseract_character_ratio"),
            field=f"runs[{index}].sarvam_to_pytesseract_character_ratio",
        )
        for field in _COST_FIELDS:
            if field in run:
                _finite_number(run[field], field=f"runs[{index}].{field}")
        for field in ("source", "cost_basis"):
            if field in run and (
                not isinstance(run[field], str) or not run[field].strip()
            ):
                raise ValueError(f"runs[{index}].{field} must be a non-empty string")
        if "limitations" in run and (
            not isinstance(run["limitations"], list)
            or not all(
                isinstance(limitation, str) and limitation.strip()
                for limitation in run["limitations"]
            )
        ):
            raise ValueError(f"runs[{index}].limitations must be a list of strings")
        if "actual_billing_available" in run and not isinstance(
            run["actual_billing_available"], bool
        ):
            raise ValueError(
                f"runs[{index}].actual_billing_available must be a boolean"
            )
    return payload


def import_evidence(
    path: Path,
    *,
    tracking_uri: str | None = None,
    artifact_uri: str | None = None,
) -> dict[str, str]:
    payload = load_evidence(path)
    imported: dict[str, str] = {}
    for run in payload["runs"]:
        attempted = int(run.get("pages_attempted", run.get("pages_submitted", 0)))
        scored = int(run["pages_paired_scored"])
        failures = int(run.get("provider_job_failures", run.get("provider_failures", 0)))
        accepted = int(run.get("accepted_jobs", 0))
        metrics = {
            "pages_attempted": float(attempted),
            "pages_paired_scored": float(scored),
            "paired_page_coverage": scored / attempted if attempted else 0.0,
            "normalized_exact_text_divergence": float(
                run["normalized_exact_text_divergence"]
            ),
            "sarvam_to_pytesseract_character_ratio": float(
                run["sarvam_to_pytesseract_character_ratio"]
            ),
            "provider_job_failures": float(failures),
            "provider_job_failure_rate": failures / accepted if accepted else 0.0,
        }
        cost = run.get("recorded_cost_rupees")
        cost_kind = "recorded"
        if cost is None:
            cost = run.get("estimated_list_price_accepted_jobs_rupees")
            cost_kind = "estimated_list_price_accepted_jobs"
        if cost is not None:
            metrics["cost_per_attempted_page_rupees"] = (
                float(cost) / attempted if attempted else 0.0
            )
            metrics["cost_total_rupees"] = float(cost)
        imported[str(run["run_id"])] = log_benchmark_run(
            pipeline_variant="sarvam_both" if run["arm"] == "both" else f"sarvam_{run['arm']}",
            sarvam_arm=str(run["arm"]),
            schema_version=str(payload["extract_schema_version"]),
            slice_id=str(payload["slice"]),
            ocr_engine="sarvam",
            sample_n=scored,
            cost_per_doc_rupees=(
                float(cost) / attempted if cost is not None and attempted else None
            ),
            ocr_divergence_rate=float(run["normalized_exact_text_divergence"]),
            extra_params={
                "cached_evidence_run_id": str(run["run_id"]),
                "evidence_status": str(run["status"]),
                "provider": str(payload["provider"]),
                "provider_model": str(payload["model"]),
                "normalizer_version": str(payload["normalizer_version"]),
                "credits_available_for_new_calls": str(
                    payload["credits_available_for_new_calls"]
                ).lower(),
                "actual_billing_available": str(
                    run.get("actual_billing_available", False)
                ).lower(),
                "cost_evidence": cost_kind if cost is not None else "unavailable",
                "cost_basis": str(run.get("cost_basis", "not recorded")),
                "quality_claim_permitted": "false",
            },
            extra_metrics=metrics,
            artifacts=[path],
            tracking_uri=tracking_uri,
            artifact_uri=artifact_uri,
        )
    return imported


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--tracking-uri")
    parser.add_argument("--artifact-uri")
    args = parser.parse_args(argv)
    imported = import_evidence(
        args.evidence,
        tracking_uri=args.tracking_uri,
        artifact_uri=args.artifact_uri,
    )
    print(json.dumps(imported, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
