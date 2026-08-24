"""Privacy-safe scorecard for officer-adjudicated summaries.

The input is structured judgment data only.  It deliberately contains no
source grievance, generated summary, reference summary, ticket number, or
officer identity; those narratives belong in a separately governed review
store.  This module measures whether the summary retained critical facts,
invented or contradicted anything, leaked PII, was useful without editing, and
correctly abstained on low-signal/non-grievance inputs.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any, Mapping, Sequence

from janasunani.evaluation.stats import wilson_interval


SCHEMA_VERSION = "janasunani.summary-judgments/v1"
REPORT_VERSION = "janasunani.summary-scorecard/v1"
BINDING_VERSION = "janasunani.summary-benchmark-binding/v1"
PROVENANCE_VERSION = "summary-development-provenance/v1"
_FORBIDDEN_FIELDS = {
    "grievance",
    "raw_text",
    "redacted_text",
    "source_text",
    "summary",
    "candidate_summary",
    "reference_summary",
    "ticket_no",
    "officer_id",
}
_REQUIRED_FIELDS = {
    "item_id",
    "group_id",
    "language",
    "source_type",
    "should_skip",
    "skipped",
    "critical_facts_total",
    "critical_facts_present",
    "unsupported_claims",
    "contradictions",
    "pii_leak",
    "usefulness",
    "usable_without_edit",
    "edit_seconds",
}


@dataclass(frozen=True)
class SummaryJudgment:
    item_id: str
    group_id: str
    language: str
    source_type: str
    should_skip: bool
    skipped: bool
    critical_facts_total: int
    critical_facts_present: int
    unsupported_claims: int
    contradictions: int
    pii_leak: bool
    usefulness: int | None
    usable_without_edit: bool | None
    edit_seconds: float | None

    def __post_init__(self) -> None:
        if not all((self.item_id, self.group_id, self.language, self.source_type)):
            raise ValueError("item/group/language/source values must be non-empty")
        for value, name in (
            (self.should_skip, "should_skip"),
            (self.skipped, "skipped"),
            (self.pii_leak, "pii_leak"),
        ):
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean")
        for value, name in (
            (self.critical_facts_total, "critical_facts_total"),
            (self.critical_facts_present, "critical_facts_present"),
            (self.unsupported_claims, "unsupported_claims"),
            (self.contradictions, "contradictions"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.critical_facts_present > self.critical_facts_total:
            raise ValueError("critical_facts_present cannot exceed total")
        if self.skipped:
            if any(
                (
                    self.critical_facts_present,
                    self.unsupported_claims,
                    self.contradictions,
                    int(self.pii_leak),
                )
            ):
                raise ValueError(
                    "a skipped summary cannot carry generated-output findings"
                )
            if any(
                value is not None
                for value in (
                    self.usefulness,
                    self.usable_without_edit,
                    self.edit_seconds,
                )
            ):
                raise ValueError(
                    "a skipped summary cannot carry usefulness/edit judgments"
                )
        else:
            if (
                self.usefulness is None
                or isinstance(self.usefulness, bool)
                or not isinstance(self.usefulness, int)
                or not 0 <= self.usefulness <= 3
            ):
                raise ValueError("generated summaries require usefulness in [0,3]")
            if not isinstance(self.usable_without_edit, bool):
                raise ValueError("generated summaries require usable_without_edit")
            if (
                self.edit_seconds is None
                or isinstance(self.edit_seconds, bool)
                or not isinstance(self.edit_seconds, (int, float))
                or not math.isfinite(self.edit_seconds)
                or self.edit_seconds < 0
            ):
                raise ValueError(
                    "generated summaries require non-negative edit_seconds"
                )


def load_judgments(path: Path) -> list[SummaryJudgment]:
    judgments: list[SummaryJudgment] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number} is not valid JSON") from exc
        if not isinstance(payload, Mapping):
            raise ValueError(f"line {line_number} must be an object")
        forbidden = set(payload).intersection(_FORBIDDEN_FIELDS)
        if forbidden:
            raise ValueError(
                f"line {line_number} contains forbidden narrative/identity fields: "
                f"{sorted(forbidden)}"
            )
        unknown = set(payload) - _REQUIRED_FIELDS
        missing = _REQUIRED_FIELDS - set(payload)
        if unknown or missing:
            raise ValueError(
                f"line {line_number} schema mismatch; missing={sorted(missing)} "
                f"unknown={sorted(unknown)}"
            )
        judgments.append(SummaryJudgment(**payload))
    _validate_collection(judgments)
    return judgments


def _md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_provenance(
    judgments_path: Path,
    provenance_path: Path,
    binding_path: Path,
    judgments: Sequence[SummaryJudgment],
    *,
    dataset_id: str,
) -> None:
    """Bind structured judgments to the recorded generation/review provenance."""

    try:
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"cannot read summary provenance contract: {exc}") from exc
    if not isinstance(binding, dict) or set(binding) != {
        "schema_version",
        "dataset_id",
        "judgments_md5",
        "provenance_md5",
    }:
        raise ValueError("summary benchmark binding has an unexpected shape")
    if binding.get("schema_version") != BINDING_VERSION:
        raise ValueError("summary benchmark binding version mismatch")
    if binding.get("dataset_id") != dataset_id:
        raise ValueError("summary benchmark binding dataset_id mismatch")
    if binding.get("judgments_md5") != _md5(judgments_path):
        raise ValueError("summary judgments fingerprint mismatch")
    if binding.get("provenance_md5") != _md5(provenance_path):
        raise ValueError("summary provenance fingerprint mismatch")
    if (
        not isinstance(provenance, dict)
        or provenance.get("schema_version") != PROVENANCE_VERSION
    ):
        raise ValueError("summary provenance version mismatch")
    if provenance.get("evidence_status") != "single-frontier-judge-development-only":
        raise ValueError("summary provenance evidence status mismatch")
    if provenance.get("publication_ready") is not False:
        raise ValueError("summary development provenance cannot be publication-ready")

    source = provenance.get("source")
    if not isinstance(source, dict) or source.get("redacted_only") is not True:
        raise ValueError("summary provenance does not require a redacted source")
    if source.get("split") != "test":
        raise ValueError("summary provenance split mismatch")
    if not isinstance(source.get("sha256"), str) or len(source["sha256"]) != 64:
        raise ValueError("summary provenance has no source fingerprint")

    selection = provenance.get("selection")
    generated = sum(not row.skipped for row in judgments)
    if not isinstance(selection, dict) or selection.get("sample_size") != len(
        judgments
    ):
        raise ValueError("summary provenance sample size mismatch")
    if selection.get("generated") != generated or selection.get("skipped") != (
        len(judgments) - generated
    ):
        raise ValueError("summary provenance generated/skipped counts mismatch")
    if selection.get("not_prevalence_representative") is not True:
        raise ValueError("summary provenance sample-design caveat is missing")
    review_sha256 = selection.get("private_review_sha256")
    if not isinstance(review_sha256, str) or len(review_sha256) != 64:
        raise ValueError("summary provenance has no private-review fingerprint")

    model = provenance.get("model")
    if not isinstance(model, dict) or model.get("local_files_only") is not True:
        raise ValueError("summary provenance does not pin a local model")
    weights_sha256 = model.get("weights_sha256")
    if not isinstance(weights_sha256, str) or len(weights_sha256) != 64:
        raise ValueError("summary provenance has no model-weights fingerprint")
    adjudication = provenance.get("adjudication")
    if not isinstance(adjudication, dict):
        raise ValueError("summary adjudication provenance is missing")
    if adjudication.get("structured_judgments_only_in_governed_artifacts") is not True:
        raise ValueError("summary provenance does not require structured judgments")
    if adjudication.get("officer_validated") is not False:
        raise ValueError(
            "summary development judgments cannot claim officer validation"
        )


def _validate_collection(judgments: Sequence[SummaryJudgment]) -> None:
    if not judgments:
        raise ValueError("at least one summary judgment is required")
    identifiers = [judgment.item_id for judgment in judgments]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("item_id values must be unique")


def _rate(successes: int, n: int) -> dict[str, int | float | str] | None:
    if n == 0:
        return None
    interval = wilson_interval(successes, n)
    return {
        "successes": successes,
        "n": n,
        "rate": interval.point,
        "ci_low": interval.ci_low,
        "ci_high": interval.ci_high,
        "ci_method": interval.method,
    }


def _metrics(judgments: Sequence[SummaryJudgment]) -> dict[str, Any]:
    generated = [row for row in judgments if not row.skipped]
    skip_correct = sum(row.skipped == row.should_skip for row in judgments)
    false_summaries = sum(row.should_skip and not row.skipped for row in judgments)
    missed_summaries = sum(not row.should_skip and row.skipped for row in judgments)
    facts_total = sum(row.critical_facts_total for row in generated)
    facts_present = sum(row.critical_facts_present for row in generated)
    edit_seconds = [
        float(row.edit_seconds) for row in generated if row.edit_seconds is not None
    ]
    usefulness = [
        int(row.usefulness) for row in generated if row.usefulness is not None
    ]
    return {
        "n": len(judgments),
        "generated_n": len(generated),
        "skipped_n": len(judgments) - len(generated),
        "critical_fact_recall": _rate(facts_present, facts_total),
        "unsupported_claim_case_rate": _rate(
            sum(row.unsupported_claims > 0 for row in generated), len(generated)
        ),
        "contradiction_case_rate": _rate(
            sum(row.contradictions > 0 for row in generated), len(generated)
        ),
        "pii_leak_case_rate": _rate(
            sum(row.pii_leak for row in generated), len(generated)
        ),
        "usable_without_edit_rate": _rate(
            sum(row.usable_without_edit is True for row in generated), len(generated)
        ),
        "correct_skip_rate": _rate(skip_correct, len(judgments)),
        "false_summary_on_should_skip_rate": _rate(
            false_summaries, sum(row.should_skip for row in judgments)
        ),
        "missed_summary_rate": _rate(
            missed_summaries, sum(not row.should_skip for row in judgments)
        ),
        "mean_usefulness_0_to_3": mean(usefulness) if usefulness else None,
        "median_edit_seconds": median(edit_seconds) if edit_seconds else None,
        "unsupported_claims_total": sum(row.unsupported_claims for row in generated),
        "contradictions_total": sum(row.contradictions for row in generated),
    }


def build_scorecard(
    judgments: Sequence[SummaryJudgment], *, dataset_id: str
) -> dict[str, Any]:
    _validate_collection(judgments)
    if not dataset_id.strip():
        raise ValueError("dataset_id must be non-empty")
    languages = sorted({row.language for row in judgments})
    sources = sorted({row.source_type for row in judgments})
    return {
        "report_version": REPORT_VERSION,
        "input_schema_version": SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "overall": _metrics(judgments),
        "by_language": {
            language: _metrics([row for row in judgments if row.language == language])
            for language in languages
        },
        "by_source_type": {
            source: _metrics([row for row in judgments if row.source_type == source])
            for source in sources
        },
        "safety": {
            "structured_judgments_only": True,
            "narrative_and_identity_fields_forbidden": True,
            "adjudication_required": True,
            "wilson_interval_units": {
                "critical_fact_recall": "pooled_fact",
                "case_rates": "item",
            },
            "critical_fact_within_item_dependence_adjusted": False,
            "cluster_uncertainty_pending": True,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--judgments", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    judgments = load_judgments(args.judgments)
    validate_provenance(
        args.judgments,
        args.provenance,
        args.binding,
        judgments,
        dataset_id=args.dataset_id,
    )
    report = build_scorecard(judgments, dataset_id=args.dataset_id)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
