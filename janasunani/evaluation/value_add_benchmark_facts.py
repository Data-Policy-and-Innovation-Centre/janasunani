"""Load the value-add report's claims from one full benchmark bundle.

The Word generators use this module instead of copying benchmark numbers into
prose. It fails closed when a required development artifact is absent, fake
timing is supplied, or the selected actionability candidate cannot be found.
Publication readiness remains a separate bundle field: development evidence
may populate a working draft without becoming release or impact evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


DEFAULT_BUNDLE = Path("outputs/benchmark/full_benchmark.json")


@dataclass(frozen=True)
class BenchmarkFacts:
    bundle_id: str
    publication_ready: bool
    latency: dict[str, Any]
    actionability: dict[str, Any]
    categorization: dict[str, Any]
    weak_labels: dict[str, Any]
    pii: dict[str, Any]
    routing_all: dict[str, Any]
    routing_informative: dict[str, Any]
    summary: dict[str, Any]
    section_status: dict[str, dict[str, Any]]

    @property
    def impact_available_required(self) -> int:
        return int(self.section_status["impact"]["available_required"])

    @property
    def impact_required(self) -> int:
        return int(self.section_status["impact"]["required"])


def category_benchmark_summary(category: dict[str, Any]) -> str:
    """Format the governed category metrics used by all report generators."""
    return (
        f"{category['accuracy']:.2%} top-1 / "
        f"{category['top_k_accuracy']['3']:.2%} top-3 / "
        f"{category['macro_f1']:.2%} macro-F1 on a viewed 2024 chronological, "
        "exact-text-group-disjoint development test "
        f"(n={category['n']:,})"
    )


def _artifact_payload(bundle: dict[str, Any], artifact_id: str) -> dict[str, Any]:
    matches = [row for row in bundle.get("artifacts", []) if row.get("id") == artifact_id]
    if len(matches) != 1:
        raise ValueError(f"benchmark bundle must contain exactly one {artifact_id!r} artifact")
    record = matches[0]
    if record.get("status") != "available" or not isinstance(record.get("payload"), dict):
        raise ValueError(f"benchmark artifact {artifact_id!r} is not available")
    return record["payload"]


def load_benchmark_facts(path: Path = DEFAULT_BUNDLE) -> BenchmarkFacts:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    if bundle.get("schema_version") != "janasunani-full-benchmark-v1":
        raise ValueError("unsupported full benchmark schema")
    section_status = bundle.get("section_status")
    if not isinstance(section_status, dict) or not all(
        isinstance(section_status.get(section), dict)
        for section in ("speed", "accuracy", "impact")
    ):
        raise ValueError("benchmark bundle lacks speed/accuracy/impact status")

    latency = _artifact_payload(bundle, "pipeline_latency_development")
    if latency.get("is_fake_timing") is not False:
        raise ValueError("value-add report refuses fake or unlabeled latency")
    if latency.get("failed_attempts") != 0:
        raise ValueError("value-add report requires an explicitly successful timing run")
    for path_name in ("text", "document"):
        e2e = latency.get("input_paths", {}).get(path_name, {}).get("e2e")
        if not isinstance(e2e, dict) or not e2e.get("n"):
            raise ValueError(f"latency lacks measured {path_name!r} end-to-end results")

    actionability_payload = _artifact_payload(bundle, "actionability_candidates")
    selected = actionability_payload.get("validation_selected_candidate")
    selected_payload = actionability_payload.get("candidates", {}).get(selected)
    if not isinstance(selected_payload, dict) or not isinstance(
        selected_payload.get("test"), dict
    ):
        raise ValueError("actionability bundle lacks the validation-selected test result")
    actionability = {
        "selected_candidate": selected,
        "release_eligible": bool(actionability_payload.get("release_eligible")),
        **selected_payload["test"],
    }

    weak_labels = _artifact_payload(bundle, "actionability_weak_label_audit")
    categorization = _artifact_payload(
        bundle, "categorization_historical_chronological"
    ).get("test")
    if not isinstance(categorization, dict):
        raise ValueError("categorization artifact lacks its chronological test result")
    pii_payload = _artifact_payload(bundle, "pii_development_scorecard")
    if len(pii_payload) != 1:
        raise ValueError("PII scorecard must contain exactly one current language bucket")
    pii = next(iter(pii_payload.values()))
    if not isinstance(pii, dict):
        raise ValueError("PII scorecard language bucket is not an object")

    routing_all = _artifact_payload(bundle, "routing_historical_all").get("test")
    routing_informative = _artifact_payload(
        bundle, "routing_historical_informative"
    ).get("test")
    if not isinstance(routing_all, dict) or not isinstance(routing_informative, dict):
        raise ValueError("routing artifacts lack chronological test results")
    summary = _artifact_payload(bundle, "summary_development").get("overall")
    if not isinstance(summary, dict):
        raise ValueError("summary development artifact lacks overall metrics")

    return BenchmarkFacts(
        bundle_id=str(bundle["bundle_id"]),
        publication_ready=bool(bundle.get("publication_ready")),
        latency=latency,
        actionability=actionability,
        categorization=categorization,
        weak_labels=weak_labels,
        pii=pii,
        routing_all=routing_all,
        routing_informative=routing_informative,
        summary=summary,
        section_status=section_status,
    )
