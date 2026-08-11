import pytest

from janasunani.tracking.artifacts import artifact_override_env_var, resolve_artifact
from janasunani.tracking.release import (
    RELEASE_MANIFEST_ENV_VAR,
    ModelRelease,
    artifact_sha256,
    new_manifest,
    write_manifest,
)


def _manifest(tmp_path, artifact):
    release_dir = tmp_path / "release-1"
    release_dir.mkdir()
    copied = release_dir / "model.bin"
    copied.write_bytes(artifact.read_bytes())
    model = ModelRelease(
        name="spam_scorer",
        provider="local_sklearn",
        trust_tier="local",
        version="2",
        artifact_path="model.bin",
        artifact_sha256=artifact_sha256(copied),
    )
    path = release_dir / "release-manifest.json"
    write_manifest(
        path,
        new_manifest(
            release_id="release-1", git_sha="a" * 40, models={"spam_scorer": model}
        ),
    )
    return path, copied


def test_resolution_order_override_then_manifest_then_dvc(tmp_path, monkeypatch):
    source = tmp_path / "source.bin"
    source.write_bytes(b"manifest")
    manifest, manifest_artifact = _manifest(tmp_path, source)
    models = tmp_path / "models"
    dvc = models / "spam_scorer"
    dvc.mkdir(parents=True)
    (dvc / "model.bin").write_bytes(b"dvc")
    override = tmp_path / "override.bin"
    override.write_bytes(b"override")
    monkeypatch.setenv(RELEASE_MANIFEST_ENV_VAR, str(manifest))
    monkeypatch.setenv("JANASUNANI_SPAM_SCORER_ARTIFACT", str(override))

    assert resolve_artifact("spam_scorer", models_dir=models) == override
    monkeypatch.delenv("JANASUNANI_SPAM_SCORER_ARTIFACT")
    assert resolve_artifact("spam_scorer", models_dir=models) == manifest_artifact
    monkeypatch.delenv(RELEASE_MANIFEST_ENV_VAR)
    assert resolve_artifact("spam_scorer", models_dir=models) == dvc


def test_resolution_degrades_when_manifest_is_invalid(tmp_path, monkeypatch):
    models = tmp_path / "models"
    fallback = models / "spam_scorer"
    fallback.mkdir(parents=True)
    (fallback / "model.bin").write_bytes(b"dvc")
    broken = tmp_path / "broken.json"
    broken.write_text("not json")
    monkeypatch.setenv(RELEASE_MANIFEST_ENV_VAR, str(broken))

    assert resolve_artifact("spam_scorer", models_dir=models) is None


def test_runtime_resolver_does_not_import_mlflow():
    import ast
    import inspect
    from janasunani.tracking import artifacts, release

    for module in (artifacts, release):
        tree = ast.parse(inspect.getsource(module))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert not any(name == "mlflow" or name.startswith("mlflow.") for name in imported)


def test_artifact_name_cannot_escape_the_models_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.write_bytes(b"model")
    models = tmp_path / "models"
    models.mkdir()

    assert resolve_artifact("../outside", models_dir=models) is None


def test_override_variable_name_is_public_but_validated():
    assert (
        artifact_override_env_var("routing-incidence")
        == "JANASUNANI_ROUTING_INCIDENCE_ARTIFACT"
    )
    with pytest.raises(ValueError, match="invalid artifact name"):
        artifact_override_env_var("../escape")
