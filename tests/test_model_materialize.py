from types import SimpleNamespace

import pytest

from janasunani.tracking.materialize import materialize_release
from janasunani.tracking.release import (
    ReleaseManifestError,
    active_manifest_path,
    load_manifest,
)


APPROVED_ARTIFACT_SHA256 = (
    "542c971fe8899506c71941c54893b529eca20fa6c0b76e2d0a448a2d45665592"
)


class FakeClient:
    def __init__(self, *, tags=None):
        self.tags = (
            {"dvc.path": "models/actionability", "dvc.hash": "a" * 32 + ".dir"}
            if tags is None
            else tags
        )
        self.calls = []

    def get_model_version_by_alias(self, name, alias):
        self.calls.append((name, alias))
        return SimpleNamespace(
            version="12", source="fake:/actionability", tags=self.tags
        )


def _spec():
    return {
        "release_id": "release-12",
        "git_sha": "a" * 40,
        "models": {
            "actionability": {
                "registry_name": "janasunani-actionability",
                "alias": "production",
                "provider": "local_sklearn",
                "trust_tier": "local",
                "artifact_sha256": APPROVED_ARTIFACT_SHA256,
                "parameters": {"review_threshold": 0.7},
                "benchmark_run_id": "bench-1",
                "dataset_id": "redacted-v1",
                "gold_id": "gold-v1",
            },
            "sarvam_digitise": {
                "provider": "sarvam",
                "trust_tier": "authorized_hosted",
                "version": "observed-2026-08-01",
                "endpoint": "sarvam-digitise",
                "parameters": {"mode": "accurate"},
                "benchmark_run_id": "sarvam-cached-1",
            },
        },
    }


def _downloader(*, artifact_uri, dst_path):
    assert artifact_uri == "fake:/actionability"
    target = dst_path / "downloaded-model"
    target.mkdir()
    (target / "model.joblib").write_bytes(b"weights")
    return str(target)


def test_materialize_pins_alias_to_version_and_can_activate(tmp_path):
    client = FakeClient()

    path = materialize_release(
        spec=_spec(),
        release_root=tmp_path,
        activate=True,
        client=client,
        downloader=_downloader,
    )

    manifest = load_manifest(path)
    actionability = manifest.models["actionability"]
    assert client.calls == [("janasunani-actionability", "production")]
    assert actionability.alias == "production"
    assert actionability.version == "12"
    assert actionability.dvc_path == "models/actionability"
    assert actionability.artifact_sha256
    assert manifest.models["sarvam_digitise"].endpoint == "sarvam-digitise"
    assert active_manifest_path(tmp_path) == path


def test_materialize_rejects_registry_version_without_dvc_provenance(tmp_path):
    client = FakeClient(tags={})

    with pytest.raises(ReleaseManifestError, match="DVC provenance"):
        materialize_release(
            spec=_spec(), release_root=tmp_path, client=client, downloader=_downloader
        )

    assert not (tmp_path / "release-12").exists()


def test_materialize_checks_approved_download_digest(tmp_path):
    spec = _spec()
    spec["models"]["actionability"]["artifact_sha256"] = "0" * 64

    with pytest.raises(ReleaseManifestError, match="approved"):
        materialize_release(
            spec=spec,
            release_root=tmp_path,
            client=FakeClient(),
            downloader=_downloader,
        )

    assert not (tmp_path / "release-12").exists()


def test_materialize_requires_approved_digest_before_registry_access(tmp_path):
    spec = _spec()
    del spec["models"]["actionability"]["artifact_sha256"]
    client = FakeClient()

    with pytest.raises(ReleaseManifestError, match="approved artifact_sha256"):
        materialize_release(
            spec=spec, release_root=tmp_path, client=client, downloader=_downloader
        )

    assert client.calls == []


def test_materialize_rejects_model_path_traversal_before_any_write(tmp_path):
    spec = _spec()
    model = spec["models"].pop("actionability")
    model["registry_name"] = "janasunani-actionability"
    spec["models"]["../../escaped"] = model
    client = FakeClient()

    with pytest.raises(ReleaseManifestError, match="invalid model name"):
        materialize_release(
            spec=spec, release_root=tmp_path, client=client, downloader=_downloader
        )

    assert client.calls == []
    assert not (tmp_path / "escaped").exists()


def test_materialize_rejects_malformed_dvc_provenance(tmp_path):
    client = FakeClient(tags={"dvc.path": "../outside", "dvc.hash": "not-a-hash"})

    with pytest.raises(ReleaseManifestError, match="unsafe DVC provenance path"):
        materialize_release(
            spec=_spec(), release_root=tmp_path, client=client, downloader=_downloader
        )

    assert not (tmp_path / "release-12").exists()


def test_materialize_requires_dvc_model_mirror_path(tmp_path):
    client = FakeClient(
        tags={"dvc.path": "artifacts/actionability", "dvc.hash": "a" * 32 + ".dir"}
    )

    with pytest.raises(ReleaseManifestError, match="must live under models/"):
        materialize_release(
            spec=_spec(), release_root=tmp_path, client=client, downloader=_downloader
        )

    assert not (tmp_path / "release-12").exists()


def test_materialize_rejects_noncanonical_dvc_model_path(tmp_path):
    client = FakeClient(
        tags={"dvc.path": "models\\actionability", "dvc.hash": "a" * 32 + ".dir"}
    )

    with pytest.raises(ReleaseManifestError, match="unsafe DVC provenance path"):
        materialize_release(
            spec=_spec(), release_root=tmp_path, client=client, downloader=_downloader
        )

    assert not (tmp_path / "release-12").exists()


def test_materialize_never_overwrites_an_existing_release(tmp_path):
    materialize_release(
        spec=_spec(), release_root=tmp_path, client=FakeClient(), downloader=_downloader
    )

    with pytest.raises(FileExistsError):
        materialize_release(
            spec=_spec(),
            release_root=tmp_path,
            client=FakeClient(),
            downloader=_downloader,
        )


def test_materialize_rejects_invalid_git_sha_before_registry_or_write(tmp_path):
    spec = _spec()
    spec["git_sha"] = "abc123"
    client = FakeClient()
    release_root = tmp_path / "releases"

    with pytest.raises(ReleaseManifestError, match="full 40- or 64-character"):
        materialize_release(
            spec=spec,
            release_root=release_root,
            client=client,
            downloader=_downloader,
        )

    assert client.calls == []
    assert not release_root.exists()


def test_materialize_never_moves_a_download_from_outside_staging(tmp_path):
    outside = tmp_path / "outside-download"
    outside.mkdir()
    (outside / "model.joblib").write_bytes(b"weights")

    def outside_downloader(**_kwargs):
        return str(outside)

    with pytest.raises(ReleaseManifestError, match="escaped its staging"):
        materialize_release(
            spec=_spec(),
            release_root=tmp_path / "releases",
            client=FakeClient(),
            downloader=outside_downloader,
        )

    assert outside.is_dir()
    assert not (tmp_path / "releases" / "release-12").exists()


def test_materialize_validates_hosted_shape_before_creating_release_root(tmp_path):
    spec = _spec()
    spec["models"]["sarvam_digitise"]["unexpected"] = "value"
    release_root = tmp_path / "releases"

    with pytest.raises(ReleaseManifestError, match="unknown fields"):
        materialize_release(
            spec=spec,
            release_root=release_root,
            client=FakeClient(),
            downloader=_downloader,
        )

    assert not release_root.exists()


def test_materialize_rejects_hosted_version_placeholder_before_write(tmp_path):
    spec = _spec()
    spec["models"]["sarvam_digitise"]["version"] = (
        "replace-with-observed-provider-model-id"
    )
    release_root = tmp_path / "releases"
    client = FakeClient()

    with pytest.raises(ReleaseManifestError, match="concrete observed hosted version"):
        materialize_release(
            spec=spec,
            release_root=release_root,
            client=client,
            downloader=_downloader,
        )

    assert client.calls == []
    assert not release_root.exists()
