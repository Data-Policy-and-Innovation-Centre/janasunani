"""Assemble reproducible benchmark outputs into one publication-gated bundle.

The bundle is deterministic: it contains no wall-clock generation timestamp and
derives its ID from the configuration and exact input bytes. Missing required
evidence remains an explicit blocker; it is never replaced with a proxy value.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Sequence, TypeGuard


SCHEMA_VERSION = "janasunani-full-benchmark-v1"
SECTIONS = ("speed", "accuracy", "impact")
REQUIRED_FIELD_SCHEMAS = {
    "array",
    "boolean",
    "finite_number",
    "metric_map",
    "nonempty_array",
    "nonempty_count_map",
    "nonempty_object",
    "nonempty_string",
    "nonempty_string_array",
    "nonnegative_integer",
    "nonnegative_number",
    "object",
    "positive_integer",
    "positive_number",
    "sha256",
    "unit_interval",
}
SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _lookup(payload: object, dotted_path: str) -> object:
    current = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _is_finite_number(value: object) -> TypeGuard[int | float]:
    """Accept JSON numbers without coercing arbitrary-size integers to float."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return not isinstance(value, float) or math.isfinite(value)


def _is_metric_map(
    value: object,
    *,
    metric_keys: frozenset[str],
    label_keys: frozenset[str] = frozenset(),
) -> bool:
    """Validate label -> support plus an exact bounded metric vocabulary."""

    if not isinstance(value, dict) or not value or not metric_keys:
        return False
    if label_keys and set(value) != label_keys:
        return False
    for label, row in value.items():
        if not isinstance(label, str) or not label.strip() or not isinstance(row, dict):
            return False
        support = row.get("support")
        if (
            not isinstance(support, int)
            or isinstance(support, bool)
            or support <= 0
        ):
            return False
        if set(row) != {"support", *metric_keys}:
            return False
        if not all(
            _is_finite_number(row[key]) and 0 <= row[key] <= 1
            for key in metric_keys
        ):
            return False
    return True


def _is_nonempty_count_map(value: object) -> bool:
    """Validate a nonempty aggregate label -> count object."""

    return (
        isinstance(value, dict)
        and bool(value)
        and all(
            isinstance(label, str)
            and bool(label.strip())
            and isinstance(count, int)
            and not isinstance(count, bool)
            and count >= 0
            for label, count in value.items()
        )
    )


def _matches_field_schema(
    payload: object,
    dotted_path: str,
    schema: str,
    *,
    metric_keys: frozenset[str] = frozenset(),
    label_keys: frozenset[str] = frozenset(),
) -> bool:
    value = _lookup(payload, dotted_path)
    if schema == "array":
        return isinstance(value, list)
    if schema == "boolean":
        return isinstance(value, bool)
    if schema == "finite_number":
        return _is_finite_number(value)
    if schema == "metric_map":
        return _is_metric_map(
            value, metric_keys=metric_keys, label_keys=label_keys
        )
    if schema == "nonempty_array":
        return isinstance(value, list) and bool(value)
    if schema == "nonempty_count_map":
        return _is_nonempty_count_map(value)
    if schema == "nonempty_object":
        return isinstance(value, dict) and bool(value)
    if schema == "nonempty_string":
        return isinstance(value, str) and bool(value.strip())
    if schema == "nonempty_string_array":
        return (
            isinstance(value, list)
            and bool(value)
            and all(isinstance(item, str) and bool(item.strip()) for item in value)
        )
    if schema == "nonnegative_integer":
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    if schema == "nonnegative_number":
        return _is_finite_number(value) and value >= 0
    if schema == "object":
        return isinstance(value, dict)
    if schema == "positive_integer":
        return isinstance(value, int) and not isinstance(value, bool) and value > 0
    if schema == "positive_number":
        return _is_finite_number(value) and value > 0
    if schema == "sha256":
        return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None
    if schema == "unit_interval":
        return _is_finite_number(value) and 0 <= value <= 1
    raise AssertionError(f"unvalidated required-field schema: {schema}")


def _schema_relation_errors(payload: object, schema_version: object) -> list[str]:
    """Validate cross-field invariants that scalar schemas cannot express."""

    if schema_version == "janasunani.pilot-citizen-outcomes/v1":
        invitations = _lookup(payload, "invitations")
        responses = _lookup(payload, "responses")
        satisfaction_n = _lookup(payload, "effects.satisfaction.n")
        response_rate = _lookup(payload, "effects.response_rate")
        counts = (invitations, responses, satisfaction_n)
        if not all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in counts
        ) or not _is_finite_number(response_rate):
            return []
        errors: list[str] = []
        if responses > invitations:
            errors.append("responses must not exceed invitations")
        if satisfaction_n > responses:
            errors.append("effects.satisfaction.n must not exceed responses")
        if invitations > 0 and not math.isclose(
            response_rate, responses / invitations, rel_tol=1e-9, abs_tol=1e-12
        ):
            errors.append("effects.response_rate must equal responses divided by invitations")
        return errors

    if schema_version != "janasunani.pipeline-latency/v1":
        return []
    attempts = _lookup(payload, "attempts")
    completed = _lookup(payload, "completed_attempts")
    failed = _lookup(payload, "failed_attempts")
    values = (attempts, completed, failed)
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in values):
        return []
    if attempts != completed + failed:
        return ["attempts must equal completed_attempts plus failed_attempts"]
    return []


def _required_field_relation_errors(
    payload: object, required_fields: dict[str, str]
) -> list[str]:
    """Validate relations implied by configured groups of required fields."""

    suffixes = ("estimate", "ci_low", "ci_high")
    interval_prefixes = {
        path.removesuffix(".estimate")
        for path, schema in required_fields.items()
        if path.endswith(".estimate")
        and schema == "finite_number"
        and all(
            required_fields.get(f"{path.removesuffix('.estimate')}.{suffix}")
            == "finite_number"
            for suffix in suffixes
        )
    }
    errors: list[str] = []
    for prefix in sorted(interval_prefixes):
        estimate = _lookup(payload, f"{prefix}.estimate")
        ci_low = _lookup(payload, f"{prefix}.ci_low")
        ci_high = _lookup(payload, f"{prefix}.ci_high")
        if all(_is_finite_number(value) for value in (estimate, ci_low, ci_high)):
            if not ci_low <= estimate <= ci_high:
                errors.append(
                    f"{prefix} must satisfy ci_low <= estimate <= ci_high"
                )
    return errors


def _validate_config(config: object) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError("benchmark bundle config must be an object")
    artifacts = config.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("benchmark bundle config requires a non-empty artifacts list")
    seen: set[str] = set()
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            raise ValueError(f"artifacts[{index}] must be an object")
        missing = sorted(
            {
                "id",
                "section",
                "path",
                "required_for_publication",
                "tracked_input",
            }
            - artifact.keys()
        )
        if missing:
            raise ValueError(f"artifacts[{index}] missing keys: {', '.join(missing)}")
        artifact_id = artifact["id"]
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError(f"artifacts[{index}].id must be a non-empty string")
        if artifact_id in seen:
            raise ValueError(f"duplicate artifact id: {artifact_id}")
        seen.add(artifact_id)
        if artifact["section"] not in SECTIONS:
            raise ValueError(f"{artifact_id}: section must be one of {SECTIONS}")
        if not isinstance(artifact["path"], str) or not artifact["path"]:
            raise ValueError(f"{artifact_id}: path must be a non-empty string")
        if not isinstance(artifact["required_for_publication"], bool):
            raise ValueError(f"{artifact_id}: required_for_publication must be boolean")
        if not isinstance(artifact["tracked_input"], bool):
            raise ValueError(f"{artifact_id}: tracked_input must be boolean")
        required_values = artifact.get("required_values", {})
        if not isinstance(required_values, dict) or not all(
            isinstance(key, str) and key for key in required_values
        ):
            raise ValueError(f"{artifact_id}: required_values must be an object of dotted paths")
        required_fields = artifact.get("required_fields", {})
        if not isinstance(required_fields, dict) or not all(
            isinstance(field, str)
            and field
            and isinstance(schema, str)
            and schema in REQUIRED_FIELD_SCHEMAS
            for field, schema in required_fields.items()
        ):
            raise ValueError(
                f"{artifact_id}: required_fields must map dotted paths to supported schemas"
            )
        metric_map_fields = {
            field for field, schema in required_fields.items() if schema == "metric_map"
        }
        metric_map_required_metrics = artifact.get("metric_map_required_metrics", {})
        if (
            not isinstance(metric_map_required_metrics, dict)
            or set(metric_map_required_metrics) != metric_map_fields
            or not all(
                isinstance(metrics, list)
                and bool(metrics)
                and all(
                    isinstance(metric, str) and bool(metric.strip())
                    for metric in metrics
                )
                and len(metrics) == len(set(metrics))
                for metrics in metric_map_required_metrics.values()
            )
        ):
            raise ValueError(
                f"{artifact_id}: metric_map_required_metrics must define unique non-empty "
                "metric names for every metric_map field"
            )
        metric_map_labels = artifact.get("metric_map_labels", {})
        if (
            not isinstance(metric_map_labels, dict)
            or not set(metric_map_labels).issubset(metric_map_fields)
            or not all(
                isinstance(labels, list)
                and bool(labels)
                and all(isinstance(label, str) and bool(label.strip()) for label in labels)
                and len(labels) == len(set(labels))
                for labels in metric_map_labels.values()
            )
        ):
            raise ValueError(
                f"{artifact_id}: metric_map_labels must define unique non-empty "
                "labels for metric_map fields"
            )
        expected_schema = artifact.get("schema_version")
        if expected_schema is not None and (
            not isinstance(expected_schema, str) or not expected_schema
        ):
            raise ValueError(f"{artifact_id}: schema_version must be a non-empty string")
        if artifact["required_for_publication"]:
            if expected_schema is None:
                raise ValueError(
                    f"{artifact_id}: required artifacts must declare schema_version"
                )
            if required_values.get("publication_ready") is not True:
                raise ValueError(
                    f"{artifact_id}: required artifacts must require publication_ready=true"
                )
            if not required_fields:
                raise ValueError(
                    f"{artifact_id}: required artifacts must declare substantive required_fields"
                )
    return config


def _resolve_artifact_path(*, root: Path, relative: Path, artifact_id: str) -> Path:
    """Confine aggregate evidence reads to non-data files below ``root``."""
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{artifact_id}: path must stay below the repository root")
    if relative.parts and relative.parts[0].casefold() == "data":
        raise ValueError(f"{artifact_id}: paths under data/ cannot be bundled")
    resolved_root = root.resolve()
    resolved = (resolved_root / relative).resolve()
    try:
        resolved_relative = resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"{artifact_id}: resolved path must stay below the repository root"
        ) from exc
    if resolved_relative.parts and resolved_relative.parts[0].casefold() == "data":
        raise ValueError(f"{artifact_id}: resolved paths under data/ cannot be bundled")
    return resolved


def build_bundle(config: dict[str, Any], *, root: Path) -> dict[str, Any]:
    """Load configured aggregate artifacts and return a deterministic bundle."""
    config = _validate_config(config)
    records: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    for spec in sorted(config["artifacts"], key=lambda row: row["id"]):
        relative = Path(spec["path"])
        path = _resolve_artifact_path(
            root=root, relative=relative, artifact_id=spec["id"]
        )
        record: dict[str, Any] = {
            "id": spec["id"],
            "section": spec["section"],
            "path": relative.as_posix(),
            "producer": spec.get("producer"),
            "claim": spec.get("claim"),
            "required_for_publication": spec["required_for_publication"],
            "tracked_input": spec["tracked_input"],
            "expected_schema_version": spec.get("schema_version"),
        }
        if not spec["tracked_input"]:
            record["status"] = "untracked"
            record["sha256"] = None
            record["payload"] = None
            if spec["required_for_publication"]:
                blockers.append(
                    {
                        "artifact_id": spec["id"],
                        "reason": (
                            f"{relative.as_posix()} is not enabled as a DVC-tracked "
                            "bundle input"
                        ),
                    }
                )
        elif not path.is_file():
            record["status"] = "missing"
            record["sha256"] = None
            record["payload"] = None
            if spec["required_for_publication"]:
                blockers.append(
                    {
                        "artifact_id": spec["id"],
                        "reason": spec.get("missing_reason", f"missing {relative.as_posix()}"),
                    }
                )
        else:
            raw = path.read_bytes()
            try:
                payload = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"{spec['id']}: expected aggregate JSON at {relative}: {exc}") from exc
            mismatches: list[str] = []
            expected_schema = spec.get("schema_version")
            if expected_schema is not None and _lookup(payload, "schema_version") != expected_schema:
                mismatches.append(f"schema_version must equal {expected_schema!r}")
            mismatches.extend(
                f"{dotted_path} must equal {expected!r}"
                for dotted_path, expected in spec.get("required_values", {}).items()
                if _lookup(payload, dotted_path) != expected
            )
            mismatches.extend(
                f"{dotted_path} must satisfy required schema {schema!r}"
                for dotted_path, schema in spec.get("required_fields", {}).items()
                if not _matches_field_schema(
                    payload,
                    dotted_path,
                    schema,
                    metric_keys=frozenset(
                        spec.get("metric_map_required_metrics", {}).get(
                            dotted_path, []
                        )
                    ),
                    label_keys=frozenset(
                        spec.get("metric_map_labels", {}).get(dotted_path, [])
                    ),
                )
            )
            mismatches.extend(
                _required_field_relation_errors(
                    payload, spec.get("required_fields", {})
                )
            )
            mismatches.extend(_schema_relation_errors(payload, expected_schema))
            record["status"] = "incomplete" if mismatches else "available"
            record["sha256"] = _sha256(raw)
            record["payload"] = payload
            if mismatches:
                record["completeness_errors"] = mismatches
                if spec["required_for_publication"]:
                    blockers.append(
                        {
                            "artifact_id": spec["id"],
                            "reason": "; ".join(mismatches),
                        }
                    )
        records.append(record)

    section_status: dict[str, dict[str, int | bool]] = {}
    for section in SECTIONS:
        selected = [row for row in records if row["section"] == section]
        required = [row for row in selected if row["required_for_publication"]]
        available_required = [row for row in required if row["status"] == "available"]
        section_status[section] = {
            "configured": len(selected),
            "required": len(required),
            "available_required": len(available_required),
            "complete": bool(required) and len(required) == len(available_required),
        }

    identity = {
        "schema_version": SCHEMA_VERSION,
        "config": config,
        "artifacts": [
            {key: row[key] for key in ("id", "section", "path", "required_for_publication", "status", "sha256")}
            for row in records
        ],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "bundle_id": _sha256(_canonical_json(identity)),
        "benchmark_release": config.get("benchmark_release"),
        "publication_ready": not blockers and all(
            bool(section_status[section]["complete"]) for section in SECTIONS
        ),
        "section_status": section_status,
        "blockers": blockers,
        "artifacts": records,
        "interpretation": {
            "speed": "technical runtime and reliability; never officer time saved",
            "accuracy": "frozen-set model quality, coverage, abstention, and safety",
            "impact": "officer, workflow, and citizen outcomes; causal only under the locked pilot",
        },
    }


def render_markdown(bundle: dict[str, Any]) -> str:
    lines = [
        "# Full benchmark bundle",
        "",
        f"- Bundle ID: `{bundle['bundle_id']}`",
        f"- Publication ready: **{'yes' if bundle['publication_ready'] else 'no'}**",
        "",
        "| Section | Required available | Complete |",
        "|---|---:|---:|",
    ]
    for section in SECTIONS:
        status = bundle["section_status"][section]
        lines.append(
            f"| {section} | {status['available_required']} / {status['required']} | "
            f"{'yes' if status['complete'] else 'no'} |"
        )
    lines.extend(["", "## Evidence artifacts", "", "| ID | Section | Status | Required | SHA-256 |", "|---|---|---|---:|---|"])
    for artifact in bundle["artifacts"]:
        digest = artifact["sha256"] or "—"
        lines.append(
            f"| {artifact['id']} | {artifact['section']} | {artifact['status']} | "
            f"{'yes' if artifact['required_for_publication'] else 'no'} | `{digest}` |"
        )
    lines.extend(["", "## Publication blockers", ""])
    if bundle["blockers"]:
        lines.extend(
            f"- `{blocker['artifact_id']}`: {blocker['reason']}" for blocker in bundle["blockers"]
        )
    else:
        lines.append("None.")
    lines.extend(
        [
            "",
            "Speed, accuracy, and impact remain separate claim types. A complete speed or accuracy section cannot substitute for missing impact evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def write_bundle(bundle: dict[str, Any], *, output: Path, markdown: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_canonical_json(bundle))
    markdown.write_text(render_markdown(bundle), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Return non-zero unless speed, accuracy, and impact evidence are complete.",
    )
    args = parser.parse_args(argv)
    bundle = build_bundle(_load_json(args.config), root=args.root.resolve())
    write_bundle(bundle, output=args.output, markdown=args.markdown)
    print(f"wrote {args.output} ({bundle['bundle_id']})")
    print(f"publication_ready={str(bundle['publication_ready']).lower()}")
    return 1 if args.require_complete and not bundle["publication_ready"] else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
