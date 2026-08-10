"""Materialize approved MLflow aliases into a local immutable release.

This command is the only registry read path.  It is run before deployment;
request-serving code reads only :mod:`janasunani.tracking.release`.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Callable, Mapping, Sequence

from janasunani.tracking.mlflow_utils import DVC_HASH_TAG, DVC_PATH_TAG
from janasunani.tracking.release import (
    ModelRelease,
    ReleaseManifestError,
    activate_manifest,
    artifact_sha256,
    new_manifest,
    validate_release_identifier,
    write_manifest,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_DVC_HASH = re.compile(r"^(?:(?:md5|sha256):)?(?:[0-9a-f]{32}|[0-9a-f]{64})(?:\.dir)?$")
_LOCAL_MODEL_FIELDS = frozenset(
    {
        "registry_name",
        "alias",
        "provider",
        "trust_tier",
        "artifact_sha256",
        "parameters",
        "input_schema_version",
        "output_schema_version",
        "benchmark_run_id",
        "dataset_id",
        "gold_id",
    }
)


def materialize_release(
    *,
    spec: Mapping[str, Any],
    release_root: Path,
    tracking_uri: str | None = None,
    activate: bool = False,
    client: Any | None = None,
    downloader: Callable[..., str] | None = None,
) -> Path:
    """Resolve all aliases, verify provenance, and publish one release.

    ``client`` and ``downloader`` are injectable for tests.  Production imports
    MLflow lazily here, never from the runtime resolver.
    """
    release_id, git_sha, models = _parse_spec(spec)
    release_root = release_root.resolve()
    release_root.mkdir(parents=True, exist_ok=True)
    final_dir = release_root / release_id
    if final_dir.exists():
        raise FileExistsError(final_dir)

    if client is None or downloader is None:
        import mlflow
        from mlflow.tracking import MlflowClient

        if tracking_uri:
            mlflow.set_tracking_uri(tracking_uri)
        if client is None:
            client = MlflowClient(tracking_uri=tracking_uri)
        if downloader is None:
            downloader = mlflow.artifacts.download_artifacts

    staging = Path(tempfile.mkdtemp(prefix=f".{release_id}.", dir=release_root))
    releases: dict[str, ModelRelease] = {}
    reserved_final_dir = False
    try:
        for name, config in models.items():
            if config.get("endpoint"):
                releases[name] = _hosted_release(name, config)
                continue
            releases[name] = _materialize_registry_model(
                name=name,
                config=config,
                staging=staging,
                client=client,
                downloader=downloader,
            )
        manifest = new_manifest(release_id=release_id, git_sha=git_sha, models=releases)
        write_manifest(staging / "release-manifest.json", manifest)
        final_dir.mkdir()
        reserved_final_dir = True
        for item in staging.iterdir():
            os.replace(item, final_dir / item.name)
        staging.rmdir()
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if reserved_final_dir:
            shutil.rmtree(final_dir, ignore_errors=True)
        raise

    manifest_path = final_dir / "release-manifest.json"
    if activate:
        activate_manifest(manifest_path, root=release_root)
    return manifest_path


def _parse_spec(
    spec: Mapping[str, Any],
) -> tuple[str, str, Mapping[str, Mapping[str, Any]]]:
    allowed = {"release_id", "git_sha", "models"}
    unknown = set(spec) - allowed
    if unknown:
        raise ReleaseManifestError(f"unknown materialization fields: {sorted(unknown)}")
    release_id = spec.get("release_id")
    git_sha = spec.get("git_sha") or _git_sha()
    models = spec.get("models")
    release_id = validate_release_identifier(release_id, field="release_id")
    if not isinstance(git_sha, str) or _GIT_SHA.fullmatch(git_sha) is None:
        raise ReleaseManifestError(
            "git_sha must be a full 40- or 64-character SHA"
        )
    if not isinstance(models, Mapping) or not models:
        raise ReleaseManifestError("models must be a non-empty object")
    for name, config in models.items():
        if not isinstance(name, str) or not isinstance(config, Mapping):
            raise ReleaseManifestError("each model spec must be a named object")
        validate_release_identifier(name, field="model name")
        if config.get("endpoint"):
            ModelRelease.from_dict(name, config)
        else:
            unknown = set(config) - _LOCAL_MODEL_FIELDS
            if unknown:
                raise ReleaseManifestError(
                    f"model {name!r} has unknown materialization fields: "
                    f"{sorted(unknown)}"
                )
            _validate_local_spec(name, config)
    return release_id, git_sha, models


def _concrete_identifier(value: object, *, model: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value.strip().lower().startswith("replace-with")
    ):
        raise ReleaseManifestError(f"model {model!r} must pin a concrete {field}")
    return value.strip()


def _validate_local_spec(name: str, config: Mapping[str, Any]) -> None:
    expected = config.get("artifact_sha256")
    if not isinstance(expected, str) or _SHA256.fullmatch(expected) is None:
        raise ReleaseManifestError(
            f"model {name!r} must pin an approved artifact_sha256 before download"
        )
    _concrete_identifier(
        config.get("benchmark_run_id"), model=name, field="benchmark_run_id"
    )
    _concrete_identifier(config.get("dataset_id"), model=name, field="dataset_id")
    _concrete_identifier(config.get("alias"), model=name, field="alias")
    _concrete_identifier(
        config.get("registry_name") or name, model=name, field="registry_name"
    )
    trust_tier = config.get("trust_tier") or "local"
    if trust_tier not in {"local", "experimental"}:
        raise ReleaseManifestError(
            f"local model {name!r} has invalid trust_tier {trust_tier!r}"
        )
    parameters = config.get("parameters")
    if parameters is not None and not isinstance(parameters, Mapping):
        raise ReleaseManifestError(f"model {name!r} parameters must be an object")


def _materialize_registry_model(
    *,
    name: str,
    config: Mapping[str, Any],
    staging: Path,
    client: Any,
    downloader: Callable[..., str],
) -> ModelRelease:
    registry_name = str(config.get("registry_name") or name)
    alias = config.get("alias")
    if not isinstance(alias, str) or not alias:
        raise ReleaseManifestError(f"model {name!r} must request a registry alias")
    version = client.get_model_version_by_alias(registry_name, alias)
    tags = dict(getattr(version, "tags", {}) or {})
    dvc_path = tags.get(DVC_PATH_TAG)
    dvc_hash = tags.get(DVC_HASH_TAG)
    if not dvc_path or not dvc_hash:
        raise ReleaseManifestError(
            f"model {name!r} version {version.version} lacks DVC provenance tags"
        )
    dvc_relative = Path(str(dvc_path))
    if dvc_relative.is_absolute() or ".." in dvc_relative.parts:
        raise ReleaseManifestError(
            f"model {name!r} has unsafe DVC provenance path {dvc_path!r}"
        )
    if _DVC_HASH.fullmatch(str(dvc_hash)) is None:
        raise ReleaseManifestError(
            f"model {name!r} has invalid DVC provenance hash {dvc_hash!r}"
        )
    source = getattr(version, "source", None)
    if not source:
        raise ReleaseManifestError(
            f"model {name!r} version {version.version} has no source"
        )
    destination = staging / "artifacts" / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    downloaded = Path(downloader(artifact_uri=source, dst_path=destination.parent))
    try:
        downloaded.resolve().relative_to(destination.parent.resolve())
    except ValueError as exc:
        raise ReleaseManifestError(
            f"model {name!r} download escaped its staging directory"
        ) from exc
    if downloaded.resolve() != destination.resolve():
        if destination.exists():
            raise ReleaseManifestError(
                f"download destination already exists: {destination}"
            )
        shutil.move(downloaded, destination)
    digest = artifact_sha256(destination)
    expected = config["artifact_sha256"]
    if expected != digest:
        raise ReleaseManifestError(
            f"model {name!r} downloaded checksum {digest} does not match approved {expected}"
        )
    return ModelRelease(
        name=name,
        provider=str(config.get("provider") or "local"),
        trust_tier=str(config.get("trust_tier") or "local"),
        version=str(version.version),
        artifact_path=f"artifacts/{name}",
        artifact_sha256=digest,
        alias=alias,
        parameters=config.get("parameters"),
        input_schema_version=config.get("input_schema_version"),
        output_schema_version=config.get("output_schema_version"),
        dvc_path=dvc_path,
        dvc_hash=dvc_hash,
        benchmark_run_id=config.get("benchmark_run_id") or tags.get("benchmark.run_id"),
        dataset_id=config.get("dataset_id") or tags.get("dataset.id"),
        gold_id=config.get("gold_id") or tags.get("gold.id"),
    )


def _hosted_release(name: str, config: Mapping[str, Any]) -> ModelRelease:
    version = config.get("version")
    if not isinstance(version, str) or not version:
        raise ReleaseManifestError(f"hosted model {name!r} must pin observed version")
    return ModelRelease.from_dict(name, dict(config))


def _git_sha() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _load_spec(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ReleaseManifestError("materialization spec root must be an object")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    materialize = subparsers.add_parser("materialize")
    materialize.add_argument("--spec", type=Path, required=True)
    materialize.add_argument(
        "--release-root", type=Path, default=Path("models/releases")
    )
    materialize.add_argument("--tracking-uri")
    materialize.add_argument("--activate", action="store_true")
    activation = subparsers.add_parser("activate")
    activation.add_argument("manifest", type=Path)
    activation.add_argument(
        "--release-root", type=Path, default=Path("models/releases")
    )
    args = parser.parse_args(argv)
    if args.command == "materialize":
        path = materialize_release(
            spec=_load_spec(args.spec),
            release_root=args.release_root,
            tracking_uri=args.tracking_uri,
            activate=args.activate,
        )
        print(path)
        return 0
    activate_manifest(args.manifest, root=args.release_root)
    print(args.manifest)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
