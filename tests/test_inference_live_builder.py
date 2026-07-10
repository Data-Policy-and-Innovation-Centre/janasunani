from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("python_multipart")
# The OCR preflight (below) now resolves tesseract exactly as
# `pytesseract_backend` does, so exercising it needs the real package, not
# just stdlib `shutil.which`.
pytest.importorskip("pytesseract")

import pytesseract  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from janasunani.config import Settings  # noqa: E402
from janasunani.inference import serve  # noqa: E402
from janasunani.inference import service  # noqa: E402
from janasunani.inference.service import (  # noqa: E402
    InferenceInputError,
    _required_model_files,
    build_processor,
    preflight,
)
from janasunani.pipeline.stages.ocr_extraction import page_renderer  # noqa: E402
from janasunani.serving.api import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_ocr_binary_resolution(monkeypatch):
    """`_configure_tesseract` mutates a process-global
    (`pytesseract.pytesseract.tesseract_cmd`) the first time it resolves a
    binary, and `TESSERACT_CMD` can leak in from a real dev/CI environment.
    Reset both before every test in this module so one test's resolved value
    can never leak into another, regardless of run order."""
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")
    monkeypatch.delenv("TESSERACT_CMD", raising=False)


def _write_dummy_model_artifacts(root: Path) -> None:
    """Satisfy `build_processor`'s artifact checks with placeholder files so a
    test can reach the code that runs after them (e.g. the OCR-dependency
    preflight) without needing real DVC-mirrored model weights. Must cover
    every requirement in `_required_model_files` -- including the tokenizer/
    weight files, not just config -- or the checks abort before those points."""
    categorizer_dir = root / "categorizer"
    categorizer_dir.mkdir(parents=True)
    (categorizer_dir / "config.json").write_text("{}")
    (categorizer_dir / "label_encoder_ROS_wDOCS_english.pkl").write_bytes(b"")
    (categorizer_dir / "model.safetensors").write_bytes(b"")
    (categorizer_dir / "tokenizer.json").write_text("{}")
    page_type_dir = root / "page_type_classifier" / "vit_type_classifier"
    page_type_dir.mkdir(parents=True)
    (page_type_dir / "config.json").write_text("{}")
    (page_type_dir / "model.safetensors").write_bytes(b"")
    (page_type_dir / "preprocessor_config.json").write_text("{}")


def test_build_processor_fails_closed_when_local_models_are_missing(tmp_path):
    with pytest.raises(RuntimeError, match="missing local categorizer config artifact"):
        build_processor(tmp_path)


def test_build_processor_fails_closed_on_partial_mirror_missing_weights(tmp_path):
    """(Codex P2 on PR #26) A partial DVC mirror -- config + label encoder
    present but the HF weights (`model.safetensors`/`pytorch_model.bin`)
    missing -- must fail closed, not crash mid-warm-up inside
    `AutoModel...from_pretrained`."""
    categorizer_dir = tmp_path / "categorizer"
    categorizer_dir.mkdir(parents=True)
    (categorizer_dir / "config.json").write_text("{}")
    (categorizer_dir / "tokenizer.json").write_text("{}")
    (categorizer_dir / "label_encoder_ROS_wDOCS_english.pkl").write_bytes(b"")

    with pytest.raises(RuntimeError, match="missing local categorizer weights artifact"):
        build_processor(tmp_path)


def test_build_processor_fails_closed_when_only_tokenizer_config_present(tmp_path):
    """(Codex P2 re-review on PR #26) `tokenizer_config.json` holds only
    tokenizer settings, not the vocabulary. A mirror with the config but no
    `tokenizer.json`/`vocab.txt` must fail closed rather than crash inside
    `AutoTokenizer.from_pretrained`."""
    categorizer_dir = tmp_path / "categorizer"
    categorizer_dir.mkdir(parents=True)
    (categorizer_dir / "config.json").write_text("{}")
    (categorizer_dir / "model.safetensors").write_bytes(b"")
    (categorizer_dir / "label_encoder_ROS_wDOCS_english.pkl").write_bytes(b"")
    (categorizer_dir / "tokenizer_config.json").write_text("{}")  # settings only

    with pytest.raises(
        RuntimeError, match="missing local categorizer tokenizer artifact"
    ):
        build_processor(tmp_path)


def test_preflight_flags_partial_mirror_missing_weights(tmp_path, monkeypatch):
    """(Codex P2 on PR #26) The fast preflight must also catch the partial
    mirror -- a green preflight has to mean the model dir is actually usable."""
    _write_dummy_model_artifacts(tmp_path)
    (tmp_path / "categorizer" / "model.safetensors").unlink()  # weights gone
    monkeypatch.setattr(service.shutil, "which", lambda _binary: "/usr/bin/fake")
    monkeypatch.setattr(page_renderer, "POPPLER_PATH", None)

    by_name = {c.name: c.ok for c in preflight(tmp_path)}

    assert by_name["categorizer weights"] is False
    assert by_name["categorizer config"] is True


def test_build_processor_fails_when_tesseract_binary_is_missing(tmp_path, monkeypatch):
    """(Codex P2 on PR #25) With model artifacts present but no `tesseract`
    executable resolvable via PATH *or* the backend's own mechanism
    (`TESSERACT_CMD` / bundled `~/.local/tesseract` install), startup must
    fail loudly instead of `/health` reporting `pipeline` healthy and uploads
    only failing later, mid-request."""
    _write_dummy_model_artifacts(tmp_path)
    monkeypatch.setattr(service.shutil, "which", lambda _binary: None)
    # Null the backend's `~/.local/tesseract` fallback too, regardless of
    # what happens to exist on the machine actually running this test.
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fake-home")

    with pytest.raises(
        RuntimeError, match="missing required OCR system binary 'tesseract'"
    ):
        build_processor(tmp_path)


def test_build_processor_fails_when_pdftoppm_binary_is_missing(tmp_path, monkeypatch):
    """Same as above for Poppler's `pdftoppm`, needed by the PDF page
    renderer -- nulls both PATH and the backend's `POPPLER_PATH` mechanism."""
    _write_dummy_model_artifacts(tmp_path)
    monkeypatch.setattr(
        service.shutil,
        "which",
        lambda binary: "/usr/bin/tesseract" if binary == "tesseract" else None,
    )
    monkeypatch.setattr(page_renderer, "POPPLER_PATH", None)

    with pytest.raises(
        RuntimeError, match="missing required OCR system binary 'pdfinfo/pdftoppm'"
    ):
        build_processor(tmp_path)


def test_ocr_preflight_passes_when_binaries_are_present(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda _binary: "/usr/bin/fake")

    service._require_ocr_dependencies()  # must not raise


def test_tesseract_available_via_backend_env_when_absent_from_path(
    tmp_path, monkeypatch
):
    """(Codex P2 on PR #25) `TESSERACT_CMD` is exactly how
    `pytesseract_backend._configure_tesseract` resolves a non-PATH install;
    the preflight must honor it instead of false-aborting startup."""
    fake_tesseract = tmp_path / "tesseract"
    fake_tesseract.write_bytes(b"")
    monkeypatch.setenv("TESSERACT_CMD", str(fake_tesseract))
    monkeypatch.setattr(service.shutil, "which", lambda _binary: None)

    assert service._tesseract_available() is True


def test_tesseract_unavailable_when_neither_env_nor_path_resolve(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(service.shutil, "which", lambda _binary: None)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fake-home")

    assert service._tesseract_available() is False


def test_poppler_available_via_backend_path_when_absent_from_path(
    tmp_path, monkeypatch
):
    """(Codex P2 on PR #25) `page_renderer.POPPLER_PATH` is exactly how the
    renderer resolves a non-PATH Poppler install; the preflight must honor
    it instead of false-aborting startup. Both `pdfinfo` and `pdftoppm` must
    be present in the configured dir -- `pdf2image.convert_from_path` needs
    both."""
    fake_bin_dir = tmp_path / "poppler-bin"
    fake_bin_dir.mkdir()
    (fake_bin_dir / "pdfinfo").write_bytes(b"")
    (fake_bin_dir / "pdftoppm").write_bytes(b"")
    monkeypatch.setattr(page_renderer, "POPPLER_PATH", str(fake_bin_dir))
    monkeypatch.setattr(service.shutil, "which", lambda _binary: None)

    assert service._poppler_available() is True


def test_poppler_unavailable_when_neither_backend_path_nor_path_resolve(monkeypatch):
    monkeypatch.setattr(page_renderer, "POPPLER_PATH", None)
    monkeypatch.setattr(service.shutil, "which", lambda _binary: None)

    assert service._poppler_available() is False


def test_poppler_unavailable_when_pdfinfo_missing_from_path(monkeypatch):
    """(Codex P2 re-review on PR #25) A partial Poppler install that only
    exposes `pdftoppm` on PATH must still fail preflight: `pdf2image` also
    shells out to `pdfinfo` to read the page count, and a partial install
    previously passed here only to fail later, mid-request, with
    `PDFInfoNotInstalledError`."""
    monkeypatch.setattr(page_renderer, "POPPLER_PATH", None)
    monkeypatch.setattr(
        service.shutil,
        "which",
        lambda binary: "/usr/bin/pdftoppm" if binary == "pdftoppm" else None,
    )

    assert service._poppler_available() is False


def test_poppler_available_when_both_binaries_are_on_path(monkeypatch):
    monkeypatch.setattr(page_renderer, "POPPLER_PATH", None)
    monkeypatch.setattr(service.shutil, "which", lambda _binary: "/usr/bin/fake")

    assert service._poppler_available() is True


def test_preflight_all_ok_when_dependencies_present(tmp_path, monkeypatch):
    """With the model artifacts on disk and both OCR binaries resolvable,
    every preflight check passes -- the same set of conditions under which
    `build_processor` reaches the model warm-up."""
    _write_dummy_model_artifacts(tmp_path)
    monkeypatch.setattr(service.shutil, "which", lambda _binary: "/usr/bin/fake")
    monkeypatch.setattr(page_renderer, "POPPLER_PATH", None)

    checks = preflight(tmp_path)

    assert checks, "preflight must report at least one dependency"
    assert all(check.ok for check in checks)


def test_preflight_reports_missing_artifacts_without_raising(tmp_path, monkeypatch):
    """Unlike `build_processor` (which raises on the first missing artifact),
    preflight never raises: it reports each missing model file as `ok=False`
    so an operator sees the full picture in one pass."""
    monkeypatch.setattr(service.shutil, "which", lambda _binary: "/usr/bin/fake")
    monkeypatch.setattr(page_renderer, "POPPLER_PATH", None)

    checks = preflight(tmp_path)  # empty models dir -> artifacts absent

    model_checks = [c for c in checks if c.name != "tesseract" and "pdf" not in c.name]
    assert model_checks and all(not c.ok for c in model_checks)


def test_preflight_reports_missing_binaries(tmp_path, monkeypatch):
    """Model artifacts present but no OCR binaries -> only the binary checks
    fail, and preflight still returns a full report."""
    _write_dummy_model_artifacts(tmp_path)
    monkeypatch.setattr(service.shutil, "which", lambda _binary: None)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fake-home")
    monkeypatch.setattr(page_renderer, "POPPLER_PATH", None)

    by_name = {c.name: c.ok for c in preflight(tmp_path)}

    assert by_name["tesseract"] is False
    assert by_name["pdfinfo/pdftoppm"] is False
    assert by_name["categorizer config"] is True
    assert by_name["categorizer weights"] is True


def test_preflight_binary_probe_error_becomes_failed_check(tmp_path, monkeypatch):
    """A probe that raises (e.g. the pipeline-core extra is not installed, so
    importing pytesseract fails) is reported as a failed check rather than
    crashing the whole preflight."""
    _write_dummy_model_artifacts(tmp_path)

    def _boom() -> bool:
        raise ImportError("pytesseract not installed")

    monkeypatch.setattr(service, "_tesseract_available", _boom)
    monkeypatch.setattr(service, "_poppler_available", _boom)

    by_name = {c.name: c for c in preflight(tmp_path)}

    assert by_name["tesseract"].ok is False
    assert "unavailable" in by_name["tesseract"].detail


def test_preflight_reports_every_shared_required_model_file(tmp_path):
    """`preflight` must report on exactly the model files in the shared
    `_required_model_files` list (no dropped/renamed/reformatted entry). This
    guards preflight's *use* of the shared list; the companion test below
    guards `build_processor`'s use of it -- together they close the
    green-preflight-then-failed-startup drift gap."""
    reported = {c.detail for c in preflight(tmp_path) if c.name not in {
        "tesseract",
        "pdfinfo/pdftoppm",
    }}
    required = {
        " | ".join(str(path) for path in candidates)
        for candidates, _ in _required_model_files(Path(tmp_path))
    }

    assert reported == required


def test_build_processor_requires_exactly_the_shared_model_files(tmp_path, monkeypatch):
    """Load-bearing drift guard: record every artifact `build_processor`
    actually hard-requires and assert it equals the shared list preflight
    reports on. Catches a hard requirement added *inline* in `build_processor`
    (bypassing `_required_model_files`), which would let a green preflight
    precede a failed warm-up -- the exact failure the shared list prevents."""
    _write_dummy_model_artifacts(tmp_path)

    recorded: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        service,
        "_require_model_artifact",
        lambda candidates, _component: recorded.append(
            tuple(str(path) for path in candidates)
        ),
    )

    class _StopAfterArtifactChecks(Exception):
        pass

    # OCR-dependency check runs immediately after the model-file loop; raise
    # here to stop before the (heavy, real) model construction.
    def _stop() -> None:
        raise _StopAfterArtifactChecks

    monkeypatch.setattr(service, "_require_ocr_dependencies", _stop)

    with pytest.raises(_StopAfterArtifactChecks):
        build_processor(tmp_path)

    required = {
        tuple(str(path) for path in candidates)
        for candidates, _ in _required_model_files(Path(tmp_path))
    }
    assert set(recorded) == required


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


def test_live_app_always_uses_lake_history_regardless_of_real_history_flag(
    monkeypatch,
):
    """(Codex P1 on PR #25) `JANASUNANI_REAL_HISTORY` only gates the
    module-level mock app in `janasunani.serving.api`. `janasunani-api-live`
    is a deliberate real-data run and must keep using `LakeHistory`
    unconditionally, whether the flag is unset, off, or on."""
    history = object()
    captured = {}

    monkeypatch.delenv("OLTP_DB_URL", raising=False)
    monkeypatch.delenv("JANASUNANI_REAL_HISTORY", raising=False)
    monkeypatch.setattr(serve, "build_processor", object)
    monkeypatch.setattr(serve, "LakeHistory", lambda: history)
    monkeypatch.setattr(serve, "InMemoryResultStore", object)
    monkeypatch.setattr(serve, "create_app", lambda **kwargs: captured.update(kwargs))

    serve.create_live_app()
    assert captured["history"] is history

    captured.clear()
    monkeypatch.setenv("JANASUNANI_REAL_HISTORY", "0")
    serve.create_live_app()
    assert captured["history"] is history
