from pathlib import Path

import pytest

from janasunani.pipeline.stages.ocr_extraction.deepseek_backend import (
    DEFAULT_MODEL_NAME,
    _resolve_model_source as resolve_deepseek_source,
)
from janasunani.tracking.artifacts import ALLOW_REMOTE_MODELS_ENV_VAR


def test_deepseek_requires_local_artifact_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("JANASUNANI_MODELS_DIR", str(tmp_path / "missing"))
    monkeypatch.delenv("JANASUNANI_DEEPSEEK_OCR_ARTIFACT", raising=False)
    monkeypatch.delenv(ALLOW_REMOTE_MODELS_ENV_VAR, raising=False)

    with pytest.raises(RuntimeError, match="no local DeepSeek OCR artifact"):
        resolve_deepseek_source()


def test_deepseek_resolves_local_override_without_network(tmp_path, monkeypatch):
    artifact = tmp_path / "deepseek"
    artifact.mkdir()
    (artifact / "config.json").write_text("{}")
    monkeypatch.setenv("JANASUNANI_DEEPSEEK_OCR_ARTIFACT", str(artifact))
    monkeypatch.delenv(ALLOW_REMOTE_MODELS_ENV_VAR, raising=False)

    assert resolve_deepseek_source() == (str(artifact), True)


def test_deepseek_public_id_is_explicit_development_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("JANASUNANI_MODELS_DIR", str(tmp_path / "missing"))
    monkeypatch.delenv("JANASUNANI_DEEPSEEK_OCR_ARTIFACT", raising=False)
    monkeypatch.setenv(ALLOW_REMOTE_MODELS_ENV_VAR, "true")

    assert resolve_deepseek_source() == (DEFAULT_MODEL_NAME, False)


def test_deepseek_explicit_local_path_is_local(tmp_path, monkeypatch):
    artifact = Path(tmp_path / "deepseek")
    artifact.mkdir()
    monkeypatch.delenv(ALLOW_REMOTE_MODELS_ENV_VAR, raising=False)

    assert resolve_deepseek_source(str(artifact)) == (str(artifact), True)
