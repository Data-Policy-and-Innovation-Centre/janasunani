"""Privacy-safe reconciliation of independent frontier adjudications.

Frontier labels are useful one-time supervision, not officer outcomes. Two
judges label the same PII-redacted, blinded records. Exact confident agreement
is accepted; every disagreement or uncertainty goes to a third independent
resolver. Outputs keep the redacted training text locally, while reports are
aggregate-only and safe to publish.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from janasunani.inference.actionability import ACTIONABILITY_LABELS, ActionabilityLabel


JUDGMENT_KEYS = {"item_id", "label", "confidence", "uncertain", "rationale_code"}
SAMPLE_KEYS = {
    "item_id",
    "group_id",
    "redacted_text",
    "created_year",
    "split",
    "language",
    "sampling_stratum",
}
RAW_TEXT_KEYS = {"grievance", "raw_text", "complaint_text", "unredacted_text"}
PROVENANCE_FIELDS = (
    "protocol_version",
    "rubric_version",
    "prompt_sha256",
    "judge_a_model",
    "judge_b_model",
    "resolver_model",
    "inference_environment",
    "egress_policy",
    "retention_policy",
)
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Judgment:
    item_id: str
    label: ActionabilityLabel
    confidence: float
    uncertain: bool
    rationale_code: str


def normalize_adjudication_provenance(
    provenance: Mapping[str, str] | None,
) -> dict[str, str]:
    """Return a complete, privacy-safe provenance record.

    Missing provenance is represented explicitly rather than guessed. The
    prompt itself is never accepted: only its SHA-256 digest belongs in an
    aggregate report or gold manifest.
    """

    supplied = dict(provenance or {})
    unknown = set(supplied).difference(PROVENANCE_FIELDS)
    if unknown:
        raise ValueError(f"unknown adjudication provenance fields: {sorted(unknown)!r}")
    normalized: dict[str, str] = {}
    for field in PROVENANCE_FIELDS:
        value = supplied.get(field, UNAVAILABLE)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"adjudication provenance {field} must be non-empty")
        value = value.strip()
        if len(value) > 256 or "\n" in value or "\r" in value:
            raise ValueError(f"adjudication provenance {field} is not safe metadata")
        if field == "prompt_sha256" and value != UNAVAILABLE:
            digest = value.removeprefix("sha256:")
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest.lower()):
                raise ValueError("prompt_sha256 must be unavailable or a SHA-256 digest")
            value = "sha256:" + digest.lower()
        normalized[field] = value
    return normalized


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _load_jsonl(
    path: Path, *, allow_empty: bool = False
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{line_number}: invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{path.name}:{line_number}: row must be an object")
        rows.append(payload)
    if not rows and not allow_empty:
        raise ValueError(f"{path.name}: file is empty")
    return rows


def load_sample(path: Path) -> list[dict[str, object]]:
    rows = _load_jsonl(path)
    ids: list[str] = []
    for index, row in enumerate(rows, 1):
        if set(row) != SAMPLE_KEYS:
            raise ValueError(f"sample row {index} has an unexpected shape")
        if RAW_TEXT_KEYS.intersection(row):
            raise ValueError(f"sample row {index} contains a raw-text field")
        if not all(
            isinstance(row[key], str) and str(row[key]).strip()
            for key in (
                "item_id",
                "group_id",
                "redacted_text",
                "split",
                "language",
                "sampling_stratum",
            )
        ):
            raise ValueError(f"sample row {index} has an invalid string field")
        if isinstance(row["created_year"], bool) or not isinstance(
            row["created_year"], int
        ):
            raise ValueError(f"sample row {index} has an invalid created_year")
        if row["item_id"] != row["group_id"]:
            raise ValueError("sample item_id and group_id must match")
        if row["split"] not in {"train", "validation", "test"}:
            raise ValueError("sample split is invalid")
        ids.append(str(row["item_id"]))
    if len(ids) != len(set(ids)):
        raise ValueError("sample item IDs must be unique")
    return rows


def load_judgments(
    path: Path, *, allow_empty: bool = False
) -> dict[str, Judgment]:
    rows = _load_jsonl(path, allow_empty=allow_empty)
    judgments: dict[str, Judgment] = {}
    for index, row in enumerate(rows, 1):
        if set(row) != JUDGMENT_KEYS:
            raise ValueError(f"judgment row {index} has an unexpected shape")
        item_id = row["item_id"]
        label = row["label"]
        confidence = row["confidence"]
        uncertain = row["uncertain"]
        rationale_code = row["rationale_code"]
        if not isinstance(item_id, str) or not item_id:
            raise ValueError("judgment item_id must be non-empty")
        if label not in ACTIONABILITY_LABELS:
            raise ValueError("judgment label is outside the taxonomy")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0.0 <= confidence <= 1.0
        ):
            raise ValueError("judgment confidence must be finite and in [0, 1]")
        if not isinstance(uncertain, bool):
            raise ValueError("judgment uncertain must be boolean")
        if (
            not isinstance(rationale_code, str)
            or not rationale_code
            or any(character.isspace() for character in rationale_code)
        ):
            raise ValueError("rationale_code must be one non-empty token")
        if item_id in judgments:
            raise ValueError("judgment item IDs must be unique")
        judgments[item_id] = Judgment(
            item_id=item_id,
            label=label,
            confidence=float(confidence),
            uncertain=uncertain,
            rationale_code=rationale_code,
        )
    return judgments


def _cohen_kappa(
    a: Sequence[ActionabilityLabel], b: Sequence[ActionabilityLabel]
) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("kappa requires equally sized non-empty sequences")
    observed = sum(left == right for left, right in zip(a, b, strict=True)) / len(a)
    counts_a = Counter(a)
    counts_b = Counter(b)
    expected = sum(
        counts_a[label] / len(a) * counts_b[label] / len(b)
        for label in ACTIONABILITY_LABELS
    )
    return (observed - expected) / (1.0 - expected) if expected < 1.0 else 1.0


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    path.chmod(0o600)


def prepare_resolution(
    sample_path: Path,
    judge_a_path: Path,
    judge_b_path: Path,
    *,
    consensus_path: Path,
    resolver_input_path: Path,
    report_path: Path,
    provenance: Mapping[str, str] | None = None,
) -> dict[str, object]:
    sample = load_sample(sample_path)
    judge_a = load_judgments(judge_a_path)
    judge_b = load_judgments(judge_b_path)
    normalized_provenance = normalize_adjudication_provenance(provenance)
    sample_ids = {str(row["item_id"]) for row in sample}
    if set(judge_a) != sample_ids or set(judge_b) != sample_ids:
        raise ValueError("each judge must cover exactly the sample IDs")

    consensus: list[dict[str, object]] = []
    resolver_input: list[dict[str, object]] = []
    labels_a: list[ActionabilityLabel] = []
    labels_b: list[ActionabilityLabel] = []
    for row in sample:
        item_id = str(row["item_id"])
        left = judge_a[item_id]
        right = judge_b[item_id]
        labels_a.append(left.label)
        labels_b.append(right.label)
        if left.label == right.label and not left.uncertain and not right.uncertain:
            consensus.append({"item_id": item_id, "label": left.label})
        else:
            resolver_input.append(dict(row))

    _write_jsonl(consensus_path, consensus)
    _write_jsonl(resolver_input_path, resolver_input)
    raw_agreement = sum(a == b for a, b in zip(labels_a, labels_b, strict=True))
    stratum_counts = Counter(str(row["sampling_stratum"]) for row in sample)
    report: dict[str, object] = {
        "schema_version": "frontier-actionability-adjudication-v1",
        "records": len(sample),
        "raw_agreement": raw_agreement / len(sample),
        "cohen_kappa": _cohen_kappa(labels_a, labels_b),
        "confident_consensus": len(consensus),
        "sent_to_resolver": len(resolver_input),
        "judge_a_distribution": dict(sorted(Counter(labels_a).items())),
        "judge_b_distribution": dict(sorted(Counter(labels_b).items())),
        "sample_design": {
            "sampling_scheme": "fixed quotas across opaque sampling strata",
            "sampling_stratum_counts": dict(sorted(stratum_counts.items())),
            "production_prevalence_representative": False,
            "metric_interpretation": (
                "agreement is measured on the designed adjudication composition; "
                "downstream accuracy, precision, and PPV are composition-specific"
            ),
        },
        "input_sha256": {
            "sample": _sha256(sample_path),
            "judge_a": _sha256(judge_a_path),
            "judge_b": _sha256(judge_b_path),
        },
        "adjudication_provenance": normalized_provenance,
        "privacy": "aggregate report contains no narrative text or ticket number",
        "claim_status": "frontier-adjudicated development gold, not officer-confirmed truth",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def finalize_gold(
    sample_path: Path,
    consensus_path: Path,
    resolver_path: Path,
    *,
    gold_path: Path,
    manifest_path: Path,
    sample_manifest_path: Path | None = None,
    provenance: Mapping[str, str] | None = None,
) -> dict[str, object]:
    sample = load_sample(sample_path)
    normalized_provenance = normalize_adjudication_provenance(provenance)
    consensus_rows = _load_jsonl(consensus_path, allow_empty=True)
    consensus: dict[str, ActionabilityLabel] = {}
    for row in consensus_rows:
        if set(row) != {"item_id", "label"} or row["label"] not in ACTIONABILITY_LABELS:
            raise ValueError("consensus row is invalid")
        item_id = str(row["item_id"])
        if item_id in consensus:
            raise ValueError("consensus item IDs must be unique")
        consensus[item_id] = row["label"]  # type: ignore[assignment]
    resolver = load_judgments(resolver_path, allow_empty=True)
    all_ids = {str(row["item_id"]) for row in sample}
    if set(consensus).intersection(resolver):
        raise ValueError("consensus and resolver IDs must be disjoint")
    if set(consensus).union(resolver) != all_ids:
        raise ValueError("consensus plus resolver must cover exactly the sample")

    gold: list[dict[str, object]] = []
    unresolved_by_split: Counter[str] = Counter()
    unresolved_by_stratum: Counter[str] = Counter()
    resolver_accepted = 0
    for row in sample:
        item_id = str(row["item_id"])
        if item_id in consensus:
            label = consensus[item_id]
        else:
            judgment = resolver[item_id]
            if judgment.uncertain:
                unresolved_by_split[str(row["split"])] += 1
                unresolved_by_stratum[str(row["sampling_stratum"])] += 1
                continue
            label = judgment.label
            resolver_accepted += 1
        gold.append(
            {
                "item_id": item_id,
                "redacted_text": row["redacted_text"],
                "label": label,
                "group_id": row["group_id"],
                "language": row["language"],
                "split": row["split"],
                "label_source": "frontier_adjudicated",
                "sampling_stratum": row["sampling_stratum"],
            }
        )
    if not gold:
        raise ValueError("no resolved rows remain for gold")
    sample_manifest_sha256: str | None = None
    split_policy = UNAVAILABLE
    design_parameters: dict[str, object] = {}
    if sample_manifest_path is not None:
        try:
            sample_manifest = json.loads(sample_manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("sample manifest is invalid JSON") from exc
        if not isinstance(sample_manifest, dict):
            raise ValueError("sample manifest must be an object")
        if sample_manifest.get("dataset_fingerprint") != _sha256(sample_path):
            raise ValueError("sample manifest fingerprint does not match sample")
        parameters = sample_manifest.get("parameters")
        if isinstance(parameters, dict):
            observed_policy = parameters.get("split_policy")
            if isinstance(observed_policy, str) and observed_policy.strip():
                split_policy = observed_policy.strip()
            for key in (
                "per_weak_stratum_split",
                "unlabeled_per_split",
                "adjudicator_blinding",
            ):
                if key in parameters:
                    design_parameters[key] = parameters[key]
        sample_manifest_sha256 = _sha256(sample_manifest_path)
    _write_jsonl(gold_path, gold)
    stratum_counts = Counter(str(row["sampling_stratum"]) for row in sample)
    gold_stratum_counts = Counter(str(row["sampling_stratum"]) for row in gold)
    manifest: dict[str, object] = {
        "schema_version": "actionability-gold-manifest-v1",
        "records": len(gold),
        "label_distribution": dict(
            sorted(Counter(str(row["label"]) for row in gold).items())
        ),
        "split_distribution": dict(
            sorted(Counter(str(row["split"]) for row in gold).items())
        ),
        "resolution": {
            "sample_records": len(sample),
            "confident_consensus_accepted": len(consensus),
            "resolver_judgments_received": len(resolver),
            "confident_resolver_accepted": resolver_accepted,
            "unresolved_excluded": sum(unresolved_by_split.values()),
            "unresolved_excluded_by_split": dict(sorted(unresolved_by_split.items())),
            "unresolved_excluded_by_sampling_stratum": dict(
                sorted(unresolved_by_stratum.items())
            ),
            "uncertain_resolver_labels_enter_gold": False,
        },
        "sample_design": {
            "sampling_scheme": "fixed quotas across opaque sampling strata",
            "split_policy": split_policy,
            "design_parameters": design_parameters,
            "sample_stratum_counts": dict(sorted(stratum_counts.items())),
            "gold_stratum_counts": dict(sorted(gold_stratum_counts.items())),
            "sample_manifest_sha256": sample_manifest_sha256 or UNAVAILABLE,
            "production_prevalence_representative": False,
            "metric_interpretation": (
                "accuracy, precision, PPV, and review workload are specific to this "
                "designed sample composition and must not be read as production prevalence"
            ),
        },
        "gold_sha256": _sha256(gold_path),
        "adjudication_provenance": normalized_provenance,
        "provenance": {
            "source": "PII-redacted DPIC-controlled sample",
            "adjudication": (
                "two independent frontier judges; confident third-resolver labels "
                "for disagreement or uncertainty; unresolved rows excluded"
            ),
            "claim_status": "development gold, not officer-confirmed truth",
        },
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest
