"""Immutable, local-only model release manifests.

MLflow aliases are intentionally resolved by the deployment control plane, not
by a serving process.  The serving side consumes this manifest and verifies the
bytes it is about to load.  That makes an alias promotion reviewable and keeps a
moving registry or a network outage out of the request path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping


SCHEMA_VERSION = "janasunani.release/v1"
RELEASE_MANIFEST_ENV_VAR = "JANASUNANI_RELEASE_MANIFEST"
RELEASE_ROOT_ENV_VAR = "JANASUNANI_RELEASE_ROOT"
ACTIVE_POINTER = "active.json"
_RELEASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_TRUST_TIERS = frozenset({"local", "authorized_hosted", "experimental"})


class ReleaseManifestError(ValueError):
    """The manifest or the artifact it pins is invalid."""


def validate_release_identifier(value: object, *, field: str) -> str:
    """Return a filesystem-safe release identifier or raise.

    Materialization uses this before any download or filesystem mutation; the
    manifest parser uses the same rule when reading an existing release.
    """

    if not isinstance(value, str) or _RELEASE_ID.fullmatch(value) is None:
        raise ReleaseManifestError(f"invalid {field} {value!r}")
    return value


def artifact_sha256(path: Path) -> str:
    """Return a stable digest for one file or an entire artifact directory.

    Directory hashes include relative POSIX paths, file sizes, and file bytes.
    Symlinks are rejected so an artifact cannot escape its pinned directory
    after validation.
    """
    path = Path(path)
    if path.is_symlink():
        raise ReleaseManifestError(f"artifact is a symlink: {path}")
    path = path.resolve()
    if path.is_file():
        return _file_sha256(path)
    if not path.is_dir():
        raise ReleaseManifestError(f"artifact is not a file or directory: {path}")

    digest = hashlib.sha256()
    files: list[Path] = []
    for item in path.rglob("*"):
        # Check every node before filtering for files. A symlink to a directory
        # is not ``is_file()``, and filtering first silently excluded exactly
        # the kind of link that can escape a checksummed artifact tree.
        if item.is_symlink():
            raise ReleaseManifestError(f"artifact contains a symlink: {item}")
        if item.is_file():
            files.append(item)
    files.sort()
    if not files:
        raise ReleaseManifestError(f"artifact directory is empty: {path}")
    for item in files:
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(item.stat().st_size.to_bytes(8, "big"))
        with item.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class ModelRelease:
    """One immutable model/provider choice in a release."""

    name: str
    provider: str
    trust_tier: str
    version: str
    artifact_path: str | None = None
    artifact_sha256: str | None = None
    alias: str | None = None
    endpoint: str | None = None
    parameters: Mapping[str, Any] | None = None
    input_schema_version: str | None = None
    output_schema_version: str | None = None
    dvc_path: str | None = None
    dvc_hash: str | None = None
    benchmark_run_id: str | None = None
    dataset_id: str | None = None
    gold_id: str | None = None

    @classmethod
    def from_dict(cls, name: str, payload: Mapping[str, Any]) -> ModelRelease:
        allowed = {
            "provider",
            "trust_tier",
            "version",
            "artifact_path",
            "artifact_sha256",
            "alias",
            "endpoint",
            "parameters",
            "input_schema_version",
            "output_schema_version",
            "dvc_path",
            "dvc_hash",
            "benchmark_run_id",
            "dataset_id",
            "gold_id",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ReleaseManifestError(
                f"model {name!r} has unknown fields: {sorted(unknown)}"
            )
        required = ("provider", "trust_tier", "version")
        for field in required:
            value = payload.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ReleaseManifestError(
                    f"model {name!r} {field} must be a non-empty string"
                )
        trust_tier = payload["trust_tier"]
        if trust_tier not in _TRUST_TIERS:
            raise ReleaseManifestError(
                f"model {name!r} has unsupported trust_tier {trust_tier!r}"
            )
        artifact_path = payload.get("artifact_path")
        endpoint = payload.get("endpoint")
        artifact_digest = payload.get("artifact_sha256")
        if artifact_path is not None and (
            not isinstance(artifact_path, str) or not artifact_path.strip()
        ):
            raise ReleaseManifestError(
                f"model {name!r} artifact_path must be a non-empty string"
            )
        if endpoint is not None and (
            not isinstance(endpoint, str) or not endpoint.strip()
        ):
            raise ReleaseManifestError(
                f"model {name!r} endpoint must be a non-empty string"
            )
        if bool(artifact_path) == bool(endpoint):
            raise ReleaseManifestError(
                f"model {name!r} must pin exactly one of artifact_path or endpoint"
            )
        if artifact_path and not artifact_digest:
            raise ReleaseManifestError(
                f"model {name!r} with artifact_path must pin artifact_sha256"
            )
        if artifact_digest is not None and (
            not isinstance(artifact_digest, str)
            or _SHA256.fullmatch(artifact_digest) is None
        ):
            raise ReleaseManifestError(
                f"model {name!r} artifact_sha256 must be 64 lowercase hex characters"
            )
        if endpoint and trust_tier != "authorized_hosted":
            raise ReleaseManifestError(
                f"hosted model {name!r} must use trust_tier='authorized_hosted'"
            )
        parameters = payload.get("parameters")
        if parameters is not None and not isinstance(parameters, Mapping):
            raise ReleaseManifestError(f"model {name!r} parameters must be an object")
        return cls(name=name, **dict(payload))

    def as_dict(self) -> dict[str, Any]:
        payload = {
            field: getattr(self, field)
            for field in (
                "provider",
                "trust_tier",
                "version",
                "artifact_path",
                "artifact_sha256",
                "alias",
                "endpoint",
                "parameters",
                "input_schema_version",
                "output_schema_version",
                "dvc_path",
                "dvc_hash",
                "benchmark_run_id",
                "dataset_id",
                "gold_id",
            )
            if getattr(self, field) is not None
        }
        return payload


@dataclass(frozen=True)
class ReleaseManifest:
    release_id: str
    created_at: str
    git_sha: str
    models: Mapping[str, ModelRelease]
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ReleaseManifest:
        allowed = {"schema_version", "release_id", "created_at", "git_sha", "models"}
        unknown = set(payload) - allowed
        if unknown:
            raise ReleaseManifestError(f"unknown manifest fields: {sorted(unknown)}")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ReleaseManifestError(
                f"unsupported schema_version {payload.get('schema_version')!r}"
            )
        release_id = validate_release_identifier(
            payload.get("release_id"), field="release_id"
        )
        created_at = str(payload.get("created_at", ""))
        try:
            parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ReleaseManifestError("created_at must be ISO-8601") from exc
        if parsed.tzinfo is None:
            raise ReleaseManifestError("created_at must include a timezone")
        git_sha = payload.get("git_sha")
        if not isinstance(git_sha, str) or _GIT_SHA.fullmatch(git_sha) is None:
            raise ReleaseManifestError("git_sha must be a full 40- or 64-character SHA")
        model_payload = payload.get("models")
        if not isinstance(model_payload, Mapping) or not model_payload:
            raise ReleaseManifestError("models must be a non-empty object")
        models: dict[str, ModelRelease] = {}
        for name, model in model_payload.items():
            validate_release_identifier(name, field="model name")
            if not isinstance(model, Mapping):
                raise ReleaseManifestError(f"model {name!r} must be an object")
            models[name] = ModelRelease.from_dict(name, model)
        return cls(
            release_id=release_id,
            created_at=created_at,
            git_sha=git_sha,
            models=models,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "release_id": self.release_id,
            "created_at": self.created_at,
            "git_sha": self.git_sha,
            "models": {
                name: self.models[name].as_dict() for name in sorted(self.models)
            },
        }


def load_manifest(path: Path) -> ReleaseManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseManifestError(
            f"cannot read release manifest {path}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ReleaseManifestError("release manifest root must be an object")
    return ReleaseManifest.from_dict(payload)


def write_manifest(path: Path, manifest: ReleaseManifest) -> None:
    """Atomically write a validated manifest; never overwrite a release."""
    validated = ReleaseManifest.from_dict(manifest.as_dict())
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    encoded = json.dumps(validated.as_dict(), indent=2, sort_keys=True) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        Path(temporary).unlink()
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def release_root(root: Path | None = None) -> Path:
    if root is not None:
        return root
    return Path(os.environ.get(RELEASE_ROOT_ENV_VAR, "models/releases"))


def active_manifest_path(root: Path | None = None) -> Path | None:
    explicit = os.environ.get(RELEASE_MANIFEST_ENV_VAR)
    if explicit:
        return Path(explicit)
    base = release_root(root)
    pointer = base / ACTIVE_POINTER
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        relative = payload["manifest"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None
    if not isinstance(relative, str) or Path(relative).is_absolute():
        return None
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def activate_manifest(path: Path, *, root: Path | None = None) -> None:
    """Atomically point serving at an already validated immutable manifest."""
    manifest = load_manifest(path)
    base = release_root(root).resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(base)
    except ValueError as exc:
        raise ReleaseManifestError("manifest must live under the release root") from exc
    _validate_local_models(manifest, resolved)
    base.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            {"release_id": manifest.release_id, "manifest": relative.as_posix()},
            sort_keys=True,
        )
        + "\n"
    )
    fd, temporary = tempfile.mkstemp(prefix=".active.", dir=base)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, base / ACTIVE_POINTER)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def resolve_manifest_artifact(
    name: str,
    *,
    manifest_path: Path | None = None,
    root: Path | None = None,
    verified_artifacts: dict[tuple[Path, str], Path] | None = None,
) -> Path | None:
    """Resolve and checksum one pinned artifact.

    ``verified_artifacts`` is deliberately caller-owned: create it fresh for
    one startup or preflight operation, then discard it. This avoids hashing
    multi-gigabyte model bytes repeatedly without creating a process-global
    cache that could outlive an activation or rollback.
    """
    path = manifest_path or active_manifest_path(root)
    if path is None:
        return None
    cache_key = (path.resolve(), name)
    if verified_artifacts is not None and cache_key in verified_artifacts:
        return verified_artifacts[cache_key]
    manifest = load_manifest(path)
    model = manifest.models.get(name)
    if model is None or model.artifact_path is None:
        return None
    artifact = _artifact_path(path, model.artifact_path)
    if artifact_sha256(artifact) != model.artifact_sha256:
        raise ReleaseManifestError(f"artifact checksum mismatch for model {name!r}")
    if verified_artifacts is not None:
        verified_artifacts[cache_key] = artifact
    return artifact


def _artifact_path(manifest_path: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute():
        raise ReleaseManifestError("artifact_path must be relative to its manifest")
    candidate = manifest_path.parent / relative
    resolved = candidate.resolve()
    try:
        resolved.relative_to(manifest_path.parent.resolve())
    except ValueError as exc:
        raise ReleaseManifestError(
            "artifact_path escapes its release directory"
        ) from exc
    return candidate


def _validate_local_models(manifest: ReleaseManifest, manifest_path: Path) -> None:
    for name, model in manifest.models.items():
        if model.artifact_path is None:
            continue
        artifact = _artifact_path(manifest_path, model.artifact_path)
        if artifact_sha256(artifact) != model.artifact_sha256:
            raise ReleaseManifestError(f"artifact checksum mismatch for model {name!r}")


def new_manifest(
    *, release_id: str, git_sha: str, models: Mapping[str, ModelRelease]
) -> ReleaseManifest:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return ReleaseManifest(
        release_id=release_id,
        created_at=now,
        git_sha=git_sha,
        models=models,
    )
