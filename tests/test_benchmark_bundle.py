from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from janasunani.evaluation.benchmark_bundle import build_bundle, main, render_markdown


def _config() -> dict[str, object]:
    return {
        "benchmark_release": "test-release",
        "artifacts": [
            {
                "id": "latency",
                "section": "speed",
                "path": "results/latency.json",
                "required_for_publication": True,
                "tracked_input": True,
                "schema_version": "test-latency/v1",
                "required_values": {"publication_ready": True},
                "required_fields": {"metrics": "nonempty_object"},
            },
            {
                "id": "quality",
                "section": "accuracy",
                "path": "results/quality.json",
                "required_for_publication": True,
                "tracked_input": True,
                "schema_version": "test-quality/v1",
                "required_values": {"publication_ready": True},
                "required_fields": {"metrics": "nonempty_object"},
            },
            {
                "id": "pilot",
                "section": "impact",
                "path": "results/pilot.json",
                "required_for_publication": True,
                "tracked_input": True,
                "schema_version": "test-pilot/v1",
                "required_values": {"publication_ready": True},
                "required_fields": {"metrics": "nonempty_object"},
            },
        ],
    }


def test_bundle_is_deterministic_and_hashes_exact_inputs(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    for name in ("latency", "quality", "pilot"):
        (results / f"{name}.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "schema_version": f"test-{name}/v1",
                    "publication_ready": True,
                    "metrics": {"n": 1},
                }
            )
            + "\n"
        )
    first = build_bundle(_config(), root=tmp_path)
    second = build_bundle(_config(), root=tmp_path)
    assert first == second
    assert first["publication_ready"] is True
    assert all(row["sha256"] for row in first["artifacts"])


def test_missing_impact_blocks_publication_without_proxy(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    (results / "latency.json").write_text(
        '{"schema_version":"test-latency/v1","publication_ready":true,"metrics":{"n":1}}\n'
    )
    (results / "quality.json").write_text(
        '{"schema_version":"test-quality/v1","publication_ready":true,"metrics":{"n":1}}\n'
    )
    bundle = build_bundle(_config(), root=tmp_path)
    assert bundle["publication_ready"] is False
    assert bundle["section_status"]["impact"]["complete"] is False
    pilot = next(row for row in bundle["artifacts"] if row["id"] == "pilot")
    assert pilot["payload"] is None
    assert {row["artifact_id"] for row in bundle["blockers"]} == {"pilot"}


def test_bundle_id_changes_when_input_bytes_change(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    for name in ("latency", "quality", "pilot"):
        (results / f"{name}.json").write_text(
            json.dumps(
                {
                    "schema_version": f"test-{name}/v1",
                    "publication_ready": True,
                    "metrics": {"n": 1},
                }
            )
            + "\n"
        )
    before = build_bundle(_config(), root=tmp_path)["bundle_id"]
    (results / "quality.json").write_text('{"accuracy":0.9}\n')
    after = build_bundle(_config(), root=tmp_path)["bundle_id"]
    assert before != after


def test_present_but_incomplete_required_artifact_still_blocks(tmp_path: Path) -> None:
    config = _config()
    results = tmp_path / "results"
    results.mkdir()
    for name in ("latency", "quality"):
        (results / f"{name}.json").write_text(
            json.dumps(
                {
                    "schema_version": f"test-{name}/v1",
                    "publication_ready": True,
                    "metrics": {"n": 1},
                }
            )
            + "\n"
        )
    (results / "pilot.json").write_text(
        '{"schema_version":"test-pilot/v1","publication_ready":false,"metrics":{"n":1}}\n'
    )
    bundle = build_bundle(config, root=tmp_path)  # type: ignore[arg-type]
    pilot = next(row for row in bundle["artifacts"] if row["id"] == "pilot")
    assert pilot["status"] == "incomplete"
    assert bundle["publication_ready"] is False
    assert bundle["blockers"] == [
        {"artifact_id": "pilot", "reason": "publication_ready must equal True"}
    ]


def test_required_artifact_must_declare_schema_and_publication_predicate() -> None:
    config = _config()
    artifact = config["artifacts"][0]  # type: ignore[index]
    artifact.pop("schema_version")
    with pytest.raises(ValueError, match="must declare schema_version"):
        build_bundle(config, root=Path("."))  # type: ignore[arg-type]

    artifact["schema_version"] = "test-latency/v1"
    artifact.pop("required_values")
    with pytest.raises(ValueError, match="must require publication_ready=true"):
        build_bundle(config, root=Path("."))  # type: ignore[arg-type]

    artifact["required_values"] = {"publication_ready": True}
    artifact.pop("required_fields")
    with pytest.raises(ValueError, match="must declare substantive required_fields"):
        build_bundle(config, root=Path("."))  # type: ignore[arg-type]


def test_tracked_input_must_be_explicit_boolean() -> None:
    config = _config()
    config["artifacts"][0]["tracked_input"] = "yes"  # type: ignore[index]

    with pytest.raises(ValueError, match="tracked_input must be boolean"):
        build_bundle(config, root=Path("."))  # type: ignore[arg-type]


def test_required_field_schema_names_are_closed() -> None:
    config = _config()
    config["artifacts"][0]["required_fields"] = {  # type: ignore[index]
        "metrics": "anything_truthy"
    }

    with pytest.raises(ValueError, match="supported schemas"):
        build_bundle(config, root=Path("."))  # type: ignore[arg-type]


def test_metric_map_fields_require_an_exact_metric_vocabulary() -> None:
    config = _config()
    artifact = config["artifacts"][0]  # type: ignore[index]
    artifact["required_fields"] = {"metrics.per_class": "metric_map"}

    with pytest.raises(ValueError, match="metric_map_required_metrics must define"):
        build_bundle(config, root=Path("."))  # type: ignore[arg-type]

    artifact["metric_map_required_metrics"] = {
        "metrics.per_class": ["precision", "precision"]
    }
    with pytest.raises(ValueError, match="metric_map_required_metrics must define"):
        build_bundle(config, root=Path("."))  # type: ignore[arg-type]


def test_schema_mismatch_blocks_required_artifact(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    for name in ("latency", "quality", "pilot"):
        (results / f"{name}.json").write_text(
            json.dumps(
                {
                    "schema_version": f"test-{name}/v1",
                    "publication_ready": True,
                    "metrics": {"n": 1},
                }
            )
            + "\n"
        )
    (results / "quality.json").write_text(
        '{"schema_version":"wrong/v1","publication_ready":true,"metrics":{"n":1}}\n'
    )

    bundle = build_bundle(_config(), root=tmp_path)

    quality = next(row for row in bundle["artifacts"] if row["id"] == "quality")
    assert quality["status"] == "incomplete"
    assert bundle["blockers"] == [
        {
            "artifact_id": "quality",
            "reason": "schema_version must equal 'test-quality/v1'",
        }
    ]


def test_latency_attempt_counts_must_reconcile_at_bundle_boundary(tmp_path: Path) -> None:
    config = _config()
    config["artifacts"][0]["schema_version"] = (  # type: ignore[index]
        "janasunani.pipeline-latency/v1"
    )
    results = tmp_path / "results"
    results.mkdir()
    for name in ("latency", "quality", "pilot"):
        schema_version = (
            "janasunani.pipeline-latency/v1"
            if name == "latency"
            else f"test-{name}/v1"
        )
        payload = {
            "schema_version": schema_version,
            "publication_ready": True,
            "metrics": {"n": 1},
        }
        if name == "latency":
            payload.update(
                {"attempts": 2, "completed_attempts": 1, "failed_attempts": 0}
            )
        (results / f"{name}.json").write_text(json.dumps(payload) + "\n")

    bundle = build_bundle(config, root=tmp_path)  # type: ignore[arg-type]

    latency = next(row for row in bundle["artifacts"] if row["id"] == "latency")
    assert latency["status"] == "incomplete"
    assert latency["completeness_errors"] == [
        "attempts must equal completed_attempts plus failed_attempts"
    ]


@pytest.mark.parametrize(
    ("include_metrics", "metrics"),
    [(False, None), (True, None), (True, {}), (True, []), (True, "")],
)
def test_required_artifact_needs_substantive_evidence_fields(
    tmp_path: Path, include_metrics: bool, metrics: object
) -> None:
    results = tmp_path / "results"
    results.mkdir()
    for name in ("latency", "quality", "pilot"):
        payload = {
            "schema_version": f"test-{name}/v1",
            "publication_ready": True,
            "metrics": {"n": 1},
        }
        if name == "quality":
            if include_metrics:
                payload["metrics"] = metrics
            else:
                payload.pop("metrics")
        (results / f"{name}.json").write_text(json.dumps(payload) + "\n")

    bundle = build_bundle(_config(), root=tmp_path)

    quality = next(row for row in bundle["artifacts"] if row["id"] == "quality")
    assert quality["status"] == "incomplete"
    assert bundle["publication_ready"] is False
    assert bundle["blockers"] == [
        {
            "artifact_id": "quality",
            "reason": "metrics must satisfy required schema 'nonempty_object'",
        }
    ]


@pytest.mark.parametrize("placeholder", [False, True, 0, 1, 0.0, 1.0, "metrics"])
def test_required_artifact_rejects_scalar_placeholders(
    tmp_path: Path, placeholder: object
) -> None:
    results = tmp_path / "results"
    results.mkdir()
    for name in ("latency", "quality", "pilot"):
        payload = {
            "schema_version": f"test-{name}/v1",
            "publication_ready": True,
            "metrics": {"n": 1},
        }
        if name == "quality":
            payload["metrics"] = placeholder
        (results / f"{name}.json").write_text(json.dumps(payload) + "\n")

    bundle = build_bundle(_config(), root=tmp_path)

    quality = next(row for row in bundle["artifacts"] if row["id"] == "quality")
    assert quality["status"] == "incomplete"
    assert bundle["publication_ready"] is False


def test_required_field_schema_distinguishes_boolean_from_integer(tmp_path: Path) -> None:
    config = _config()
    config["artifacts"][1]["required_fields"] = {  # type: ignore[index]
        "metrics.failures": "nonnegative_integer"
    }
    results = tmp_path / "results"
    results.mkdir()
    for name in ("latency", "quality", "pilot"):
        payload = {
            "schema_version": f"test-{name}/v1",
            "publication_ready": True,
            "metrics": {"n": 1, "failures": 0},
        }
        (results / f"{name}.json").write_text(json.dumps(payload) + "\n")

    assert build_bundle(config, root=tmp_path)["publication_ready"] is True  # type: ignore[arg-type]

    (results / "quality.json").write_text(
        '{"schema_version":"test-quality/v1","publication_ready":true,'
        '"metrics":{"failures":false}}\n'
    )
    assert build_bundle(config, root=tmp_path)["publication_ready"] is False  # type: ignore[arg-type]


@pytest.mark.parametrize("placeholder", [float("inf"), float("-inf"), float("nan")])
def test_required_numeric_field_rejects_nonfinite_values(
    tmp_path: Path, placeholder: float
) -> None:
    config = _config()
    config["artifacts"][1]["required_fields"] = {  # type: ignore[index]
        "metrics.seconds": "nonnegative_number"
    }
    results = tmp_path / "results"
    results.mkdir()
    for name in ("latency", "quality", "pilot"):
        payload = {
            "schema_version": f"test-{name}/v1",
            "publication_ready": True,
            "metrics": {"seconds": 0.0},
        }
        if name == "quality":
            payload["metrics"]["seconds"] = placeholder
        (results / f"{name}.json").write_text(json.dumps(payload) + "\n")

    assert build_bundle(config, root=tmp_path)["publication_ready"] is False  # type: ignore[arg-type]


def test_required_numeric_field_handles_arbitrary_size_integer(tmp_path: Path) -> None:
    config = _config()
    config["artifacts"][1]["required_fields"] = {  # type: ignore[index]
        "metrics.rate": "unit_interval"
    }
    results = tmp_path / "results"
    results.mkdir()
    for name in ("latency", "quality", "pilot"):
        payload = {
            "schema_version": f"test-{name}/v1",
            "publication_ready": True,
            "metrics": {"rate": 0.5},
        }
        if name == "quality":
            payload["metrics"]["rate"] = 10**309
        (results / f"{name}.json").write_text(json.dumps(payload) + "\n")

    bundle = build_bundle(config, root=tmp_path)  # type: ignore[arg-type]

    quality = next(row for row in bundle["artifacts"] if row["id"] == "quality")
    assert quality["status"] == "incomplete"
    assert bundle["publication_ready"] is False


def test_structured_metric_and_count_maps_validate_entries(tmp_path: Path) -> None:
    config = _config()
    for artifact in config["artifacts"]:  # type: ignore[union-attr]
        artifact["required_fields"] = {
            "metrics.per_class": "metric_map",
            "failures.by_class": "nonempty_count_map",
        }
        artifact["metric_map_required_metrics"] = {
            "metrics.per_class": ["precision", "recall"]
        }
    results = tmp_path / "results"
    results.mkdir()
    for name in ("latency", "quality", "pilot"):
        payload = {
            "schema_version": f"test-{name}/v1",
            "publication_ready": True,
            "metrics": {
                "per_class": {
                    "class-a": {"support": 2, "precision": 0.5, "recall": 1.0}
                }
            },
            "failures": {"by_class": {"class-a": 0}},
        }
        (results / f"{name}.json").write_text(json.dumps(payload) + "\n")

    assert build_bundle(config, root=tmp_path)["publication_ready"] is True  # type: ignore[arg-type]

    (results / "quality.json").write_text(
        '{"schema_version":"test-quality/v1","publication_ready":true,'
        '"metrics":{"per_class":{"class-a":{"support":1,"placeholder":0}}},'
        '"failures":{"by_class":{"class-a":0}}}\n'
    )
    assert build_bundle(config, root=tmp_path)["publication_ready"] is False  # type: ignore[arg-type]


def _assign_dotted(payload: dict[str, object], path: str, value: object) -> None:
    parts = path.split(".")
    target = payload
    for part in parts[:-1]:
        child = target.setdefault(part, {})
        assert isinstance(child, dict)
        target = child
    target[parts[-1]] = value


def _minimum_value_for_schema(schema: str) -> object:
    if schema in {"positive_integer", "positive_number"}:
        return 1
    if schema in {"nonnegative_integer", "nonnegative_number", "finite_number"}:
        return 0
    if schema == "unit_interval":
        return 0
    if schema == "boolean":
        return False
    if schema == "array":
        return []
    if schema == "nonempty_array":
        return [False]
    if schema == "nonempty_string_array":
        return ["placeholder"]
    if schema in {"metric_map", "nonempty_count_map"}:
        return {"placeholder": False}
    if schema in {"object", "nonempty_object"}:
        return {"placeholder": False}
    if schema == "sha256":
        return "0" * 64
    return "placeholder"


def test_production_contract_rejects_outer_container_placeholders(tmp_path: Path) -> None:
    config = json.loads(
        (Path(__file__).parents[1] / "config" / "benchmark_bundle.json").read_text()
    )
    required = [
        artifact
        for artifact in config["artifacts"]
        if artifact["required_for_publication"]
    ]
    for artifact in required:
        artifact["tracked_input"] = True
        payload: dict[str, object] = {
            "schema_version": artifact["schema_version"],
            "publication_ready": True,
        }
        for dotted_path, expected in artifact["required_values"].items():
            _assign_dotted(payload, dotted_path, expected)
        top_level_schemas: dict[str, str] = {}
        for dotted_path, schema in artifact["required_fields"].items():
            top_level_schemas.setdefault(dotted_path.split(".")[0], schema)
        for top_level, schema in top_level_schemas.items():
            if top_level not in payload:
                payload[top_level] = _minimum_value_for_schema(schema)
            elif isinstance(payload[top_level], dict):
                payload[top_level]["placeholder"] = False
        path = tmp_path / artifact["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n")

    bundle = build_bundle(config, root=tmp_path)

    assert bundle["publication_ready"] is False
    assert all(
        artifact["status"] == "incomplete"
        for artifact in bundle["artifacts"]
        if artifact["required_for_publication"]
    )


def test_production_contract_rejects_nested_map_placeholders(tmp_path: Path) -> None:
    config = json.loads(
        (Path(__file__).parents[1] / "config" / "benchmark_bundle.json").read_text()
    )
    required = [
        artifact
        for artifact in config["artifacts"]
        if artifact["required_for_publication"]
    ]
    for artifact in required:
        artifact["tracked_input"] = True
        payload: dict[str, object] = {
            "schema_version": artifact["schema_version"],
            "publication_ready": True,
        }
        for dotted_path, schema in artifact["required_fields"].items():
            _assign_dotted(payload, dotted_path, _minimum_value_for_schema(schema))
        for dotted_path, expected in artifact["required_values"].items():
            _assign_dotted(payload, dotted_path, expected)
        path = tmp_path / artifact["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n")

    bundle = build_bundle(config, root=tmp_path)

    assert bundle["publication_ready"] is False
    structured_map_ids = {
        artifact["id"]
        for artifact in required
        if {"metric_map", "nonempty_count_map"}
        & set(artifact["required_fields"].values())
    }
    assert structured_map_ids
    assert all(
        artifact["status"] == "incomplete"
        for artifact in bundle["artifacts"]
        if artifact["id"] in structured_map_ids
    )


def test_rejects_paths_outside_root(tmp_path: Path) -> None:
    config = _config()
    config["artifacts"][0]["path"] = "../latency.json"  # type: ignore[index]
    with pytest.raises(ValueError, match="repository root"):
        build_bundle(config, root=tmp_path)  # type: ignore[arg-type]


def test_untracked_required_artifact_cannot_participate_from_ambient_file(
    tmp_path: Path,
) -> None:
    config = _config()
    config["artifacts"][1]["tracked_input"] = False  # type: ignore[index]
    results = tmp_path / "results"
    results.mkdir()
    for name in ("latency", "quality", "pilot"):
        (results / f"{name}.json").write_text(
            json.dumps(
                {
                    "schema_version": f"test-{name}/v1",
                    "publication_ready": True,
                    "metrics": {"n": 1},
                }
            )
            + "\n"
        )

    bundle = build_bundle(config, root=tmp_path)  # type: ignore[arg-type]

    quality = next(row for row in bundle["artifacts"] if row["id"] == "quality")
    assert quality["status"] == "untracked"
    assert quality["payload"] is None
    assert quality["sha256"] is None
    assert bundle["publication_ready"] is False
    assert bundle["blockers"] == [
        {
            "artifact_id": "quality",
            "reason": "results/quality.json is not enabled as a DVC-tracked bundle input",
        }
    ]


def test_production_tracked_inputs_match_full_bundle_dvc_dependencies() -> None:
    root = Path(__file__).parents[1]
    config = json.loads((root / "config" / "benchmark_bundle.json").read_text())
    stage = yaml.safe_load((root / "dvc.yaml").read_text())["stages"][
        "full-benchmark-bundle"
    ]
    artifact_paths = {artifact["path"] for artifact in config["artifacts"]}
    tracked_paths = {
        artifact["path"]
        for artifact in config["artifacts"]
        if artifact["tracked_input"]
    }
    declared_artifact_dependencies = artifact_paths & set(stage["deps"])

    assert tracked_paths == declared_artifact_dependencies


def test_rejects_artifacts_under_data_even_when_they_are_json(tmp_path: Path) -> None:
    config = _config()
    config["artifacts"][0]["path"] = "data/private.json"  # type: ignore[index]
    private = tmp_path / "data" / "private.json"
    private.parent.mkdir()
    private.write_text('{"citizen_record":"must not be bundled"}\n')

    with pytest.raises(ValueError, match="under data/"):
        build_bundle(config, root=tmp_path)  # type: ignore[arg-type]


def test_rejects_symlink_alias_to_data_inside_root(tmp_path: Path) -> None:
    config = _config()
    config["artifacts"][0]["path"] = "results/private.json"  # type: ignore[index]
    private = tmp_path / "data" / "private.json"
    private.parent.mkdir()
    private.write_text('{"citizen_record":"must not be bundled"}\n')
    (tmp_path / "results").symlink_to(private.parent, target_is_directory=True)

    with pytest.raises(ValueError, match="resolved paths under data/"):
        build_bundle(config, root=tmp_path)  # type: ignore[arg-type]


def test_rejects_internal_symlink_that_escapes_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "latency.json").write_text("{}\n")
    (root / "results").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="resolved path"):
        build_bundle(_config(), root=root)


def test_cli_writes_incomplete_bundle_but_strict_check_fails(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config()))
    output = tmp_path / "full.json"
    markdown = tmp_path / "full.md"
    assert main(["--config", str(config_path), "--root", str(tmp_path), "--output", str(output), "--markdown", str(markdown)]) == 0
    assert main(["--config", str(config_path), "--root", str(tmp_path), "--output", str(output), "--markdown", str(markdown), "--require-complete"]) == 1
    assert json.loads(output.read_text())["publication_ready"] is False
    assert "Publication blockers" in render_markdown(json.loads(output.read_text()))
