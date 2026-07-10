from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("python_multipart")

from fastapi.testclient import TestClient  # noqa: E402

from janasunani.config import Settings  # noqa: E402
from janasunani.inference import serve  # noqa: E402
from janasunani.inference import service  # noqa: E402
from janasunani.inference.service import (  # noqa: E402
    InferenceInputError,
    build_processor,
)
from janasunani.serving.api import create_app  # noqa: E402


def _write_dummy_model_artifacts(root: Path) -> None:
    """Satisfy `build_processor`'s `_require_file` artifact checks with
    placeholder files so a test can reach the code that runs after them
    (e.g. the OCR-dependency preflight) without needing real DVC-mirrored
    model weights."""
    categorizer_dir = root / "categorizer"
    categorizer_dir.mkdir(parents=True)
    (categorizer_dir / "config.json").write_text("{}")
    (categorizer_dir / "label_encoder_ROS_wDOCS_english.pkl").write_bytes(b"")
    page_type_dir = root / "page_type_classifier" / "vit_type_classifier"
    page_type_dir.mkdir(parents=True)
    (page_type_dir / "config.json").write_text("{}")


def test_build_processor_fails_closed_when_local_models_are_missing(tmp_path):
    with pytest.raises(RuntimeError, match="missing local categorizer artifact"):
        build_processor(tmp_path)


def test_build_processor_fails_when_tesseract_binary_is_missing(tmp_path, monkeypatch):
    """(Codex P2 on PR #25) With model artifacts present but no `tesseract`
    executable, startup must fail loudly instead of `/health` reporting
    `pipeline` healthy and uploads only failing later, mid-request."""
    _write_dummy_model_artifacts(tmp_path)
    monkeypatch.setattr(service.shutil, "which", lambda _binary: None)

    with pytest.raises(
        RuntimeError, match="missing required OCR system binary 'tesseract'"
    ):
        build_processor(tmp_path)


def test_build_processor_fails_when_pdftoppm_binary_is_missing(tmp_path, monkeypatch):
    """Same as above for Poppler's `pdftoppm`, needed by the PDF page renderer."""
    _write_dummy_model_artifacts(tmp_path)
    monkeypatch.setattr(
        service.shutil,
        "which",
        lambda binary: "/usr/bin/tesseract" if binary == "tesseract" else None,
    )

    with pytest.raises(
        RuntimeError, match="missing required OCR system binary 'pdftoppm'"
    ):
        build_processor(tmp_path)


def test_ocr_preflight_passes_when_binaries_are_present(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda _binary: "/usr/bin/fake")

    service._require_ocr_dependencies()  # must not raise


class RejectingProcessor:
    name = "pipeline"

    def process(self, **_kwargs):
        raise InferenceInputError("synthetic invalid document")


def test_typed_inference_input_error_becomes_http_422():
    client = TestClient(create_app(processor=RejectingProcessor()))

    response = client.post("/grievance", data={"text": "synthetic text"})

    assert response.status_code == 422
    assert response.json() == {"detail": "synthetic invalid document"}


class ExplodingProcessor:
    name = "pipeline"

    def process(self, **_kwargs):
        raise RuntimeError("synthetic model failure")


def test_unexpected_processor_failure_remains_a_server_error():
    client = TestClient(create_app(processor=ExplodingProcessor()))

    with pytest.raises(RuntimeError, match="synthetic model failure"):
        client.post("/grievance", data={"text": "synthetic text"})


def test_live_app_uses_memory_store_without_explicit_oltp(monkeypatch):
    processor = object()
    history = object()
    memory_store = object()
    captured = {}

    monkeypatch.delenv("OLTP_DB_URL", raising=False)
    monkeypatch.setattr(serve, "build_processor", lambda: processor)
    monkeypatch.setattr(serve, "LakeHistory", lambda: history)
    monkeypatch.setattr(serve, "InMemoryResultStore", lambda: memory_store)
    monkeypatch.setattr(
        serve,
        "DatabaseResultStore",
        lambda _url: pytest.fail("database store must not be constructed"),
    )
    monkeypatch.setattr(serve, "create_app", lambda **kwargs: captured.update(kwargs))

    serve.create_live_app()

    assert captured == {
        "processor": processor,
        "history": history,
        "result_store": memory_store,
    }


def test_live_app_uses_database_store_only_for_explicit_oltp(monkeypatch):
    database_store = object()
    captured = {}

    monkeypatch.setenv("OLTP_DB_URL", "sqlite+aiosqlite:////tmp/synthetic.db")
    monkeypatch.setattr(serve, "build_processor", object)
    monkeypatch.setattr(serve, "LakeHistory", object)
    monkeypatch.setattr(
        serve,
        "DatabaseResultStore",
        lambda url: (database_store, url),
    )
    monkeypatch.setattr(
        serve,
        "InMemoryResultStore",
        lambda: pytest.fail("memory store must not be constructed"),
    )
    monkeypatch.setattr(serve, "create_app", lambda **kwargs: captured.update(kwargs))

    serve.create_live_app()

    assert captured["result_store"] == (
        database_store,
        "sqlite+aiosqlite:////tmp/synthetic.db",
    )


def test_live_app_uses_database_store_for_dotenv_only_oltp(tmp_path, monkeypatch):
    """(Codex P2 on PR #25) An operator who sets `OLTP_DB_URL` only in the
    project `.env` file -- never exporting it in the shell -- must still get
    a `DatabaseResultStore`. Before this fix, `create_live_app` read raw
    `os.environ`, missed the `.env`-provided value, and silently fell back to
    `InMemoryResultStore` (live submissions would vanish on restart)."""
    database_store = object()
    captured = {}

    env_file = tmp_path / ".env"
    env_file.write_text("OLTP_DB_URL=postgresql+asyncpg://user:pass@db/janasunani\n")

    monkeypatch.delenv("OLTP_DB_URL", raising=False)  # never exported in the shell
    monkeypatch.setattr(serve, "Settings", lambda: Settings(_env_file=env_file))
    monkeypatch.setattr(serve, "build_processor", object)
    monkeypatch.setattr(serve, "LakeHistory", object)
    monkeypatch.setattr(
        serve,
        "DatabaseResultStore",
        lambda url: (database_store, url),
    )
    monkeypatch.setattr(
        serve,
        "InMemoryResultStore",
        lambda: pytest.fail("memory store must not be constructed"),
    )
    monkeypatch.setattr(serve, "create_app", lambda **kwargs: captured.update(kwargs))

    serve.create_live_app()

    assert captured["result_store"] == (
        database_store,
        "postgresql+asyncpg://user:pass@db/janasunani",
    )
