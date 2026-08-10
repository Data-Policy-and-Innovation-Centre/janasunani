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
            },
            {
                "id": "quality",
                "section": "accuracy",
                "path": "results/quality.json",
                "required_for_publication": True,
            },
            {
                "id": "pilot",
                "section": "impact",
                "path": "results/pilot.json",
                "required_for_publication": True,
            },
        ],
    }


def test_bundle_is_deterministic_and_hashes_exact_inputs(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    for name in ("latency", "quality", "pilot"):
        (results / f"{name}.json").write_text(json.dumps({"name": name}) + "\n")
    first = build_bundle(_config(), root=tmp_path)
    second = build_bundle(_config(), root=tmp_path)
    assert first == second
    assert first["publication_ready"] is True
    assert all(row["sha256"] for row in first["artifacts"])


def test_missing_impact_blocks_publication_without_proxy(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    (results / "latency.json").write_text("{}\n")
    (results / "quality.json").write_text("{}\n")
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
        (results / f"{name}.json").write_text("{}\n")
    before = build_bundle(_config(), root=tmp_path)["bundle_id"]
    (results / "quality.json").write_text('{"accuracy":0.9}\n')
    after = build_bundle(_config(), root=tmp_path)["bundle_id"]
    assert before != after


def test_present_but_incomplete_required_artifact_still_blocks(tmp_path: Path) -> None:
    config = _config()
    config["artifacts"][2]["required_values"] = {"publication_ready": True}  # type: ignore[index]
    results = tmp_path / "results"
    results.mkdir()
    for name in ("latency", "quality"):
        (results / f"{name}.json").write_text("{}\n")
    (results / "pilot.json").write_text('{"publication_ready":false}\n')
    bundle = build_bundle(config, root=tmp_path)  # type: ignore[arg-type]
    pilot = next(row for row in bundle["artifacts"] if row["id"] == "pilot")
    assert pilot["status"] == "incomplete"
    assert bundle["publication_ready"] is False
    assert bundle["blockers"] == [
        {"artifact_id": "pilot", "reason": "publication_ready must equal True"}
    ]


def test_rejects_paths_outside_root(tmp_path: Path) -> None:
    config = _config()
    config["artifacts"][0]["path"] = "../latency.json"  # type: ignore[index]
    with pytest.raises(ValueError, match="repository root"):
        build_bundle(config, root=tmp_path)  # type: ignore[arg-type]


def test_cli_writes_incomplete_bundle_but_strict_check_fails(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config()))
    output = tmp_path / "full.json"
    markdown = tmp_path / "full.md"
    assert main(["--config", str(config_path), "--root", str(tmp_path), "--output", str(output), "--markdown", str(markdown)]) == 0
    assert main(["--config", str(config_path), "--root", str(tmp_path), "--output", str(output), "--markdown", str(markdown), "--require-complete"]) == 1
    assert json.loads(output.read_text())["publication_ready"] is False
    assert "Publication blockers" in render_markdown(json.loads(output.read_text()))
