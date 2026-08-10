"""Resolve a trained-model artifact to a path, or to nothing at all.

``mlflow_utils`` in this package is registration-side only. Registry aliases
are materialized into an immutable release before deploy; serving resolves
only operator overrides, that pinned local release, or the DVC mirror. This
module is deliberately kept free of registry and network clients.

Resolution order, first hit wins:

1. ``JANASUNANI_<NAME>_ARTIFACT`` — an explicit operator override.
2. The active, immutable local release manifest.
3. ``<models_dir>/<name>`` — the DVC-mirrored convention the rest of the repo
   already uses for the categorizer and page-type models.
4. ``None``.

**Never raises.** The degradation contract is copied from
``janasunani.routing.crosswalk.load_crosswalk``: a missing *or structurally
unusable* artifact returns ``None`` and the caller falls through to whatever
it was doing before. A model that cannot be found must not be able to take
the demo down, and an operator typo in a path must not either.

Registry aliases are resolved before deploy into the release manifest.  This
module never imports MLflow and never makes a network call.
"""

from __future__ import annotations

import os
from pathlib import Path
import re

from loguru import logger


ALLOW_REMOTE_MODELS_ENV_VAR = "JANASUNANI_ALLOW_REMOTE_MODELS"
_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def remote_models_allowed() -> bool:
    """Whether mutable public model IDs are allowed for development only."""

    return os.getenv(ALLOW_REMOTE_MODELS_ENV_VAR, "").strip().lower() in {
        "1",
        "true",
        "yes",
    }

def _env_var_for(name: str) -> str:
    return f"JANASUNANI_{name.upper().replace('-', '_')}_ARTIFACT"


def _models_dir(models_dir: Path | None) -> Path:
    if models_dir is not None:
        return models_dir
    configured = os.environ.get("JANASUNANI_MODELS_DIR")
    if configured:
        return Path(configured)
    return Path("models")


def _usable(path: Path) -> bool:
    """Whether the path is something a loader could actually open.

    A directory has to be non-empty. An empty directory is the shape a failed
    ``dvc pull`` leaves behind, and reporting it as present is how a demo box
    ends up loading nothing while claiming a model is live.
    """
    try:
        if path.is_file():
            return path.stat().st_size > 0
        if path.is_dir():
            return any(path.iterdir())
    except OSError:
        return False
    return False


def resolve_artifact(name: str, *, models_dir: Path | None = None) -> Path | None:
    """Return a usable artifact path for *name*, or ``None``.

    Never raises, for any input, including a name containing path separators
    or an override pointing somewhere unreadable.
    """
    try:
        if not isinstance(name, str) or _ARTIFACT_NAME.fullmatch(name) is None:
            logger.warning("invalid artifact name {!r}; treating it as absent", name)
            return None
        override = os.environ.get(_env_var_for(name))
        if override:
            candidate = Path(override)
            if _usable(candidate):
                return candidate
            logger.warning(
                "{}={} does not resolve to a usable artifact; ignoring it",
                _env_var_for(name),
                override,
            )

        from janasunani.tracking.release import resolve_manifest_artifact

        resolved = resolve_manifest_artifact(name)
        if resolved is not None:
            return resolved

        candidate = _models_dir(models_dir) / name
        if _usable(candidate):
            return candidate
        return None
    except Exception:  # pragma: no cover — the contract is "never raises"
        logger.warning("artifact resolution for {!r} failed; treating it as absent", name)
        return None
