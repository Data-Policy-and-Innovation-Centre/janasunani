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
    # Only the required checks: the advisory ones (routing mappings, history
    # lake, OLTP store) depend on DVC data and environment, not on this
    # fixture, and are exercised separately below.
    assert all(check.ok for check in checks if check.required)


def test_preflight_reports_missing_artifacts_without_raising(tmp_path, monkeypatch):
    """Unlike `build_processor` (which raises on the first missing artifact),
    preflight never raises: it reports each missing model file as `ok=False`
    so an operator sees the full picture in one pass."""
    monkeypatch.setattr(service.shutil, "which", lambda _binary: "/usr/bin/fake")
    monkeypatch.setattr(page_renderer, "POPPLER_PATH", None)

    checks = preflight(tmp_path)  # empty models dir -> artifacts absent

    model_checks = [
        c
        for c in checks
        if c.required and c.name != "tesseract" and "pdf" not in c.name
    ]
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
    reported = {
        c.detail
        for c in preflight(tmp_path)
        if c.required and c.name not in {"tesseract", "pdfinfo/pdftoppm"}
    }
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
    # `create_live_app` resolves the URL through `service.resolve_explicit_oltp_url`,
    # which is also what preflight reports — one definition, so the two cannot drift.
    monkeypatch.setattr(service, "Settings", lambda: Settings(_env_file=env_file))
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


# --- advisory readiness checks (#30 bring-up) --------------------------------
#
# These three fail in ways the running demo does not surface: routing quietly
# on method:"fallback", /history empty rather than erroring, submissions kept
# in memory and lost on restart. They are advisory by default so local
# `make up` still works, and fatal under --strict for a box bring-up.
#
# Each is exercised in both directions with the environment stubbed, so the
# result does not depend on whether this machine has run `dvc pull`.


def _advisory(checks, name):
    by_name = {c.name: c for c in checks}
    assert name in by_name, f"{name} missing from preflight; got {sorted(by_name)}"
    check = by_name[name]
    assert not check.required, f"{name} must be advisory, not required"
    return check


def test_preflight_flags_absent_routing_mappings(tmp_path, monkeypatch):
    """Without the master CSVs the router silently answers `method:"fallback"`
    while the API stays healthy — the exact state #30 warns about for a box
    that skipped the scoped `dvc pull`."""
    monkeypatch.setattr(
        service, "_routing_mappings_check", service._routing_mappings_check
    )
    monkeypatch.setattr(
        "janasunani.routing.mappings.load_mapping_tables", lambda *a, **k: None
    )
    check = _advisory(preflight(tmp_path), "routing mappings")
    assert check.ok is False
    assert "fallback" in check.detail


def test_preflight_passes_when_routing_mappings_load(tmp_path, monkeypatch):
    class _Tables:
        categories = ("a", "b", "c")
        category_to_department = {"1": "5"}

    monkeypatch.setattr(
        "janasunani.routing.mappings.load_mapping_tables", lambda *a, **k: _Tables()
    )
    check = _advisory(preflight(tmp_path), "routing mappings")
    assert check.ok is True
    assert "3 categories" in check.detail


def test_preflight_flags_mapping_tables_that_load_empty(tmp_path, monkeypatch):
    """A header-only or truncated CSV pull parses without error -- `tables` is
    not None -- but MappingRouter has nothing to route with and falls back
    exactly as it would with no tables at all. Present-but-empty must not
    read as a healthy strict preflight (Codex review on #88)."""

    class _EmptyTables:
        categories = ()
        category_to_department = {}

    monkeypatch.setattr(
        "janasunani.routing.mappings.load_mapping_tables", lambda *a, **k: _EmptyTables()
    )
    check = _advisory(preflight(tmp_path), "routing mappings")
    assert check.ok is False
    assert "fallback" in check.detail


def test_preflight_flags_mapping_tables_with_no_derivable_department(tmp_path, monkeypatch):
    """Categories present but every one lacking a department is the same
    dead end for routing as no categories at all."""

    class _NoDepartments:
        categories = ("a", "b", "c")
        category_to_department = {}

    monkeypatch.setattr(
        "janasunani.routing.mappings.load_mapping_tables", lambda *a, **k: _NoDepartments()
    )
    check = _advisory(preflight(tmp_path), "routing mappings")
    assert check.ok is False


def test_preflight_flags_unmaterialized_lake(tmp_path, monkeypatch):
    """`LakeHistory` returns an empty page for a missing lake, so the UI shows
    'no results' rather than an error. Preflight must say which it is."""
    monkeypatch.setattr(
        "janasunani.olap.lake.lake_path",
        lambda table, lake_dir=None: tmp_path / f"{table}.parquet",
    )
    check = _advisory(preflight(tmp_path), "history lake")
    assert check.ok is False
    assert "empty page" in check.detail


_HISTORY_ROW_SQL = (
    "SELECT 'T1' AS ticket_no, DATE '2024-01-01' AS created_on, 'Khordha' AS district, "
    "'Roads' AS category, 'Pothole' AS subcategory, 'Works' AS dept, 'Pending' AS status, "
    "'Collector Office' AS office, 'A pothole' AS grievance "
    "UNION ALL "
    "SELECT 'T2', DATE '2024-01-02', 'Cuttack', 'Water', 'Leak', 'RWSS', 'Resolved', "
    "'Collector Office', 'A leak'"
)


def test_preflight_reports_lake_row_count(tmp_path, monkeypatch):
    """A materialized lake reports its size, so an operator can tell a real
    lake from an empty-but-present Parquet."""
    pytest.importorskip("duckdb")
    import duckdb

    parquet = tmp_path / "complaints.parquet"
    duckdb.connect().execute(f"COPY ({_HISTORY_ROW_SQL}) TO '{parquet.as_posix()}' (FORMAT parquet)")
    monkeypatch.setattr(
        "janasunani.olap.lake.lake_path", lambda table, lake_dir=None: parquet
    )
    check = _advisory(preflight(tmp_path), "history lake")
    assert check.ok is True
    assert "2 complaints" in check.detail


def test_preflight_flags_lake_missing_a_column_history_selects(tmp_path, monkeypatch):
    """Rows present but a column `LakeHistory.search()` actually selects is
    missing -- a stale or malformed lake -- must not pass strict preflight
    just because the row count is nonzero; it would only surface once a real
    `/history` request hit the missing column. Codex review on #88."""
    pytest.importorskip("duckdb")
    import duckdb

    parquet = tmp_path / "complaints.parquet"
    duckdb.connect().execute(
        f"COPY (SELECT 'T1' AS ticket_no UNION ALL SELECT 'T2') "
        f"TO '{parquet.as_posix()}' (FORMAT parquet)"
    )
    monkeypatch.setattr(
        "janasunani.olap.lake.lake_path", lambda table, lake_dir=None: parquet
    )
    check = _advisory(preflight(tmp_path), "history lake")
    assert check.ok is False
    assert "unreadable" in check.detail


def test_preflight_flags_unconfigured_oltp(tmp_path, monkeypatch):
    """No explicit OLTP_DB_URL means InMemoryResultStore: the demo accepts
    submissions, returns 201, and loses them on restart."""
    monkeypatch.setattr(service, "resolve_explicit_oltp_url", lambda: None)
    check = _advisory(preflight(tmp_path), "oltp store")
    assert check.ok is False
    assert "lost on restart" in check.detail


def test_preflight_verifies_oltp_connectivity_not_just_presence(tmp_path, monkeypatch):
    """DatabaseResultStore (janasunani/serving/store.py) only creates its
    async engine at startup and never actually connects until the first
    save, so a wrong password/host/database/migration would otherwise pass
    preflight and only fail on a citizen's first submission. Stubs
    `_probe_oltp_connection` rather than touching a real database or the
    network -- the same split infra_status.py's AWS/SSH tests use. Codex
    review on #88."""
    monkeypatch.setattr(
        service, "resolve_explicit_oltp_url", lambda: "postgresql+asyncpg://u:p@h/db"
    )
    monkeypatch.setattr(service, "_probe_oltp_connection", lambda url, timeout=5.0: None)
    check = _advisory(preflight(tmp_path), "oltp store")
    assert check.ok is True
    assert "connection verified" in check.detail


def test_preflight_flags_an_explicit_but_unreachable_oltp(tmp_path, monkeypatch):
    """A configured OLTP_DB_URL that cannot actually be connected to must not
    read as ready -- exactly the gap that lets /health report
    processor=pipeline while the first submission fails on save."""
    monkeypatch.setattr(
        service, "resolve_explicit_oltp_url", lambda: "postgresql+asyncpg://u:p@h/db"
    )

    def _boom(url, timeout=5.0):
        raise ConnectionRefusedError("nope")

    monkeypatch.setattr(service, "_probe_oltp_connection", _boom)
    check = _advisory(preflight(tmp_path), "oltp store")
    assert check.ok is False
    assert "unreachable" in check.detail
    assert "ConnectionRefusedError" in check.detail


def test_preflight_never_leaks_the_oltp_url_on_success(tmp_path, monkeypatch):
    """The URL carries a DB password; the report must never print it."""
    secret = "postgresql+asyncpg://user:hunter2@db.internal/janasunani"
    monkeypatch.setattr(service, "resolve_explicit_oltp_url", lambda: secret)
    monkeypatch.setattr(service, "_probe_oltp_connection", lambda url, timeout=5.0: None)
    check = _advisory(preflight(tmp_path), "oltp store")
    assert check.ok is True
    assert "hunter2" not in check.detail
    assert secret not in check.detail


def test_preflight_never_leaks_the_oltp_url_on_connection_failure(tmp_path, monkeypatch):
    """The likelier leak vector: some DBAPI errors embed the DSN -- and its
    password -- in their own exception message. Only the exception's type
    name may be reported, never str(exc)."""
    secret = "postgresql+asyncpg://user:hunter2@db.internal/janasunani"
    monkeypatch.setattr(service, "resolve_explicit_oltp_url", lambda: secret)

    def _boom(url, timeout=5.0):
        raise RuntimeError(f"could not connect to {secret}")

    monkeypatch.setattr(service, "_probe_oltp_connection", _boom)
    check = _advisory(preflight(tmp_path), "oltp store")
    assert check.ok is False
    assert "hunter2" not in check.detail
    assert secret not in check.detail


def test_probe_oltp_connection_succeeds_against_a_real_sqlite_db(tmp_path):
    """Exercises the real probe (not the preflight wiring above, which stubs
    it) against an actual database, so the SELECT 1 round trip is proven to
    work end to end without needing network or a real Postgres."""
    db_path = tmp_path / "probe.db"
    service._probe_oltp_connection(f"sqlite+aiosqlite:///{db_path}")


def test_probe_oltp_connection_raises_for_an_unreachable_target():
    """127.0.0.1 loopback on a closed port refuses instantly -- no DNS, no
    real network needed -- which is what _oltp_check's except clause and the
    timeout bound both depend on being a real `raise`, not a hang."""
    with pytest.raises(Exception):
        service._probe_oltp_connection(
            "postgresql+asyncpg://u:p@127.0.0.1:1/nonexistent", timeout=2.0
        )


def test_advisory_failures_do_not_fail_preflight_by_default(tmp_path, monkeypatch):
    """`make up` must keep working on a dev box with no DVC data."""
    _write_dummy_model_artifacts(tmp_path)
    monkeypatch.setattr(service.shutil, "which", lambda _binary: "/usr/bin/fake")
    monkeypatch.setattr(page_renderer, "POPPLER_PATH", None)
    monkeypatch.setattr(service, "resolve_explicit_oltp_url", lambda: None)
    monkeypatch.setattr(
        "janasunani.routing.mappings.load_mapping_tables", lambda *a, **k: None
    )

    checks = preflight(tmp_path)
    assert any(not c.ok for c in checks), "test setup should produce advisory failures"
    assert all(c.ok for c in checks if c.required), (
        "no required check may fail here — only advisory ones"
    )
