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
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = "janasunani-full-benchmark-v1"
SECTIONS = ("speed", "accuracy", "impact")
REQUIRED_FIELD_SCHEMAS = {
    "array",
    "boolean",
    "nonempty_array",
    "nonempty_object",
    "nonempty_string",
    "nonnegative_integer",
    "nonnegative_number",
    "object",
    "positive_integer",
    "positive_number",
}


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


def _matches_field_schema(payload: object, dotted_path: str, schema: str) -> bool:
    value = _lookup(payload, dotted_path)
    if schema == "array":
        return isinstance(value, list)
    if schema == "boolean":
        return isinstance(value, bool)
    if schema == "nonempty_array":
        return isinstance(value, list) and bool(value)
    if schema == "nonempty_object":
        return isinstance(value, dict) and bool(value)
    if schema == "nonempty_string":
        return isinstance(value, str) and bool(value.strip())
    if schema == "nonnegative_integer":
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    if schema == "nonnegative_number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        )
    if schema == "object":
        return isinstance(value, dict)
    if schema == "positive_integer":
        return isinstance(value, int) and not isinstance(value, bool) and value > 0
    if schema == "positive_number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value > 0
        )
    raise AssertionError(f"unvalidated required-field schema: {schema}")


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
        missing = sorted({"id", "section", "path", "required_for_publication"} - artifact.keys())
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
            "expected_schema_version": spec.get("schema_version"),
        }
        if not path.is_file():
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
                if not _matches_field_schema(payload, dotted_path, schema)
            )
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
