"""Resolve a trained-model artifact to a path, or to nothing at all.

``mlflow_utils`` in this package is registration-side only: it logs artifacts
and creates model versions, and nothing in it resolves an alias back to
something loadable. So a learned router or scorer has no way to find its own
weights. This module is that missing half, deliberately kept to the smallest
thing that works.

Resolution order, first hit wins:

1. ``JANASUNANI_<NAME>_ARTIFACT`` — an explicit operator override.
2. ``<models_dir>/<name>`` — the DVC-mirrored convention the rest of the repo
   already uses for the categorizer and page-type models.
3. ``None``.

**Never raises.** The degradation contract is copied from
``janasunani.routing.crosswalk.load_crosswalk``: a missing *or structurally
unusable* artifact returns ``None`` and the caller falls through to whatever
it was doing before. A model that cannot be found must not be able to take
the demo down, and an operator typo in a path must not either.

Registry resolution (``models:/name@champion``) is deliberately absent. It
would put a network call on the startup path of a box that has to come up in
front of an audience, and there is no read side in ``mlflow_utils`` to build
on. The seam is named below so the day it lands there is one place to put it.
"""

from __future__ import annotations

import os
from pathlib import Path

from loguru import logger

#: Set to route resolution through a model registry instead of the filesystem.
REGISTRY_ENV_VAR = "JANASUNANI_MODEL_REGISTRY"


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

        if os.environ.get(REGISTRY_ENV_VAR):
            resolved = _resolve_via_registry(name)
            if resolved is not None:
                return resolved

        candidate = _models_dir(models_dir) / name
        if _usable(candidate):
            return candidate
        return None
    except Exception:  # pragma: no cover — the contract is "never raises"
        logger.warning("artifact resolution for {!r} failed; treating it as absent", name)
        return None


def _resolve_via_registry(name: str) -> Path | None:
    """Resolve through a model registry alias. Not implemented before 14 Aug.

    Returns ``None`` rather than raising so that setting the env var early is
    harmless: resolution simply falls through to the filesystem.
    """
    logger.warning(
        "{} is set but registry resolution is not implemented; falling back to the filesystem",
        REGISTRY_ENV_VAR,
    )
    return None
