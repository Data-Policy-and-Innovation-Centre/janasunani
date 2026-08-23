import json

import pytest

from janasunani.tracking.release import (
    ACTIVE_POINTER,
    RELEASE_MANIFEST_ENV_VAR,
    ModelRelease,
    ReleaseManifest,
    ReleaseManifestError,
    activate_manifest,
    active_manifest_path,
    artifact_sha256,
    load_manifest,
    new_manifest,
    resolve_manifest_artifact,
    write_manifest,
)


def _local_model(path, *, version="7"):
    return ModelRelease(
        name="actionability",
        provider="local_sklearn",
        trust_tier="local",
        version=version,
        artifact_path="artifacts/actionability",
        artifact_sha256=artifact_sha256(path),
        alias="production",
        parameters={"threshold": 0.7},
        dvc_path="models/actionability",
        dvc_hash="dvc123",
        benchmark_run_id="run123",
        dataset_id="redacted-gold-v1",
        gold_id="actionability-gold-v1",
    )


def _release(tmp_path, *, release_id="release-1"):
    release_dir = tmp_path / release_id
    artifact = release_dir / "artifacts" / "actionability"
    artifact.mkdir(parents=True)
    (artifact / "model.joblib").write_bytes(b"weights")
    manifest = new_manifest(
        release_id=release_id,
        git_sha="a" * 40,
        models={"actionability": _local_model(artifact)},
    )
    path = release_dir / "release-manifest.json"
    write_manifest(path, manifest)
    return path, artifact


def test_manifest_round_trip_and_activation(tmp_path, monkeypatch):
    path, artifact = _release(tmp_path)

    activate_manifest(path, root=tmp_path)

    assert active_manifest_path(tmp_path) == path
    assert resolve_manifest_artifact("actionability", root=tmp_path) == artifact
    assert load_manifest(path).models["actionability"].version == "7"


def test_activation_is_atomic_and_supports_explicit_rollback(tmp_path):
    first, _ = _release(tmp_path, release_id="release-1")
    second, _ = _release(tmp_path, release_id="release-2")

    activate_manifest(first, root=tmp_path)
    activate_manifest(second, root=tmp_path)
    assert (
        json.loads((tmp_path / ACTIVE_POINTER).read_text())["release_id"] == "release-2"
    )

    activate_manifest(first, root=tmp_path)
    assert (
        json.loads((tmp_path / ACTIVE_POINTER).read_text())["release_id"] == "release-1"
    )


def test_manifest_checksum_drift_fails_closed(tmp_path):
    path, artifact = _release(tmp_path)
    (artifact / "model.joblib").write_bytes(b"changed")

    with pytest.raises(ReleaseManifestError, match="checksum"):
        resolve_manifest_artifact("actionability", manifest_path=path)
    with pytest.raises(ReleaseManifestError, match="checksum"):
        activate_manifest(path, root=tmp_path)


def test_artifact_hash_rejects_symlinked_directory(tmp_path):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "model.bin").write_bytes(b"weights")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "remote-code.py").write_text("raise RuntimeError('unchecked')")
    (artifact / "code").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ReleaseManifestError, match="contains a symlink"):
        artifact_sha256(artifact)


def test_artifact_hash_rejects_symlinked_root(tmp_path):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "model.bin").write_bytes(b"weights")
    linked = tmp_path / "linked"
    linked.symlink_to(artifact, target_is_directory=True)

    with pytest.raises(ReleaseManifestError, match="artifact is a symlink"):
        artifact_sha256(linked)


def test_manifest_rejects_path_traversal(tmp_path):
    payload = {
        "schema_version": "janasunani.release/v1",
        "release_id": "release-1",
        "created_at": "2026-08-10T10:00:00Z",
        "git_sha": "a" * 40,
        "models": {
            "actionability": {
                "provider": "local_sklearn",
                "trust_tier": "local",
                "version": "1",
                "artifact_path": "../escape",
                "artifact_sha256": "0" * 64,
            }
        },
    }
    path = tmp_path / "release-manifest.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(ReleaseManifestError, match="escapes"):
        resolve_manifest_artifact("actionability", manifest_path=path)


def test_hosted_model_requires_authorized_trust_tier():
    payload = {
        "schema_version": "janasunani.release/v1",
        "release_id": "release-1",
        "created_at": "2026-08-10T10:00:00Z",
        "git_sha": "a" * 40,
        "models": {
            "sarvam_digitise": {
                "provider": "sarvam",
                "trust_tier": "experimental",
                "version": "observed-model-id",
                "endpoint": "sarvam-digitise",
            }
        },
    }

    with pytest.raises(ReleaseManifestError, match="authorized_hosted"):
        ReleaseManifest.from_dict(payload)


@pytest.mark.parametrize("endpoint", [["not-a-string"], {"url": "hosted"}, 7, "", "  "])
def test_hosted_model_requires_a_nonempty_string_endpoint(endpoint):
    payload = {
        "schema_version": "janasunani.release/v1",
        "release_id": "release-1",
        "created_at": "2026-08-10T10:00:00Z",
        "git_sha": "a" * 40,
        "models": {
            "sarvam_digitise": {
                "provider": "sarvam",
                "trust_tier": "authorized_hosted",
                "version": "observed-model-id",
                "endpoint": endpoint,
            }
        },
    }

    with pytest.raises(ReleaseManifestError, match="endpoint must be a non-empty string"):
        ReleaseManifest.from_dict(payload)


def test_manifest_rejects_malformed_artifact_digest():
    payload = {
        "schema_version": "janasunani.release/v1",
        "release_id": "release-1",
        "created_at": "2026-08-10T10:00:00Z",
        "git_sha": "a" * 40,
        "models": {
            "actionability": {
                "provider": "local_sklearn",
                "trust_tier": "local",
                "version": "1",
                "artifact_path": "artifacts/actionability",
                "artifact_sha256": "not-a-checksum",
            }
        },
    }

    with pytest.raises(ReleaseManifestError, match="64 lowercase hex"):
        ReleaseManifest.from_dict(payload)


def test_explicit_manifest_env_overrides_active_pointer(tmp_path, monkeypatch):
    first, _ = _release(tmp_path, release_id="release-1")
    second, _ = _release(tmp_path, release_id="release-2")
    activate_manifest(first, root=tmp_path)
    monkeypatch.setenv(RELEASE_MANIFEST_ENV_VAR, str(second))

    assert active_manifest_path(tmp_path) == second


def test_write_manifest_never_overwrites_an_immutable_release(tmp_path):
    path, _ = _release(tmp_path)
    manifest = load_manifest(path)

    with pytest.raises(FileExistsError):
        write_manifest(path, manifest)


def test_manifest_requires_a_full_git_sha(tmp_path):
    path, _ = _release(tmp_path)
    payload = json.loads(path.read_text())
    payload["git_sha"] = "abc123"

    with pytest.raises(ReleaseManifestError, match="full 40- or 64-character"):
        ReleaseManifest.from_dict(payload)


def test_manifest_resolution_rejects_symlinked_artifact_root(tmp_path):
    release_dir = tmp_path / "release-1"
    real_artifact = release_dir / "real-artifact"
    real_artifact.mkdir(parents=True)
    (real_artifact / "model.bin").write_bytes(b"weights")
    linked_artifact = release_dir / "artifacts" / "actionability"
    linked_artifact.parent.mkdir()
    linked_artifact.symlink_to(real_artifact, target_is_directory=True)
    manifest = new_manifest(
        release_id="release-1",
        git_sha="a" * 40,
        models={
            "actionability": ModelRelease(
                name="actionability",
                provider="local",
                trust_tier="local",
                version="1",
                artifact_path="artifacts/actionability",
                artifact_sha256=artifact_sha256(real_artifact),
            )
        },
    )
    manifest_path = release_dir / "release-manifest.json"
    write_manifest(manifest_path, manifest)

    with pytest.raises(ReleaseManifestError, match="artifact is a symlink"):
        resolve_manifest_artifact("actionability", manifest_path=manifest_path)
