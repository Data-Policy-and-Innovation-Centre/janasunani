from __future__ import annotations

import json
from pathlib import Path

import pytest

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
                "schema_version": "test-latency/v1",
                "required_values": {"publication_ready": True},
                "required_fields": ["metrics"],
            },
            {
                "id": "quality",
                "section": "accuracy",
                "path": "results/quality.json",
                "required_for_publication": True,
                "schema_version": "test-quality/v1",
                "required_values": {"publication_ready": True},
                "required_fields": ["metrics"],
            },
            {
                "id": "pilot",
                "section": "impact",
                "path": "results/pilot.json",
                "required_for_publication": True,
                "schema_version": "test-pilot/v1",
                "required_values": {"publication_ready": True},
                "required_fields": ["metrics"],
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
            "reason": "metrics must contain substantive evidence",
        }
    ]


def test_rejects_paths_outside_root(tmp_path: Path) -> None:
    config = _config()
    config["artifacts"][0]["path"] = "../latency.json"  # type: ignore[index]
    with pytest.raises(ValueError, match="repository root"):
        build_bundle(config, root=tmp_path)  # type: ignore[arg-type]


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
