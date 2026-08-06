"""Warm, single-grievance inference behind the frozen serving contract.

The module stays import-light so the default mock API does not require any ML
extras.  ``build_processor`` is the strict production constructor: it checks
for the locally mirrored model artifacts, then imports and warms every heavy
component exactly once.
"""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from janasunani.config import DEFAULT_OLTP_DB_URL, MODELS_DIR, Settings
from janasunani.inference.ocr import OcrQualityError, OcrResult
from janasunani.pipeline.stages.page_type_classifier import PAGE_TYPE_CLASS_BY_LABEL
from janasunani.serving.schemas import (
    ClassificationResult,
    ExtractionResult,
    GrievanceResult,
    PIIEntity,
    RedactionResult,
    RoutingResult,
)

# Derived from the canonical label->class map (class 1 = grievance-bearing)
# instead of a parallel hardcoded literal, so the two can't drift apart.
RELEVANT_PAGE_TYPES = frozenset(
    label for label, cls in PAGE_TYPE_CLASS_BY_LABEL.items() if cls == 1
)
SUPPORTED_DOCUMENT_SUFFIXES = frozenset(
    {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif"}
)
# Matches the categorizer's own non-English fallback (`category="Uncategorized"`,
# see PHASE-8C.md): the first real-model demo targets English, so a non-English
# summary would otherwise be a hallucinated BART guess over unsupported input.
UNSUPPORTED_LANGUAGE_SUMMARY = "Summary unavailable for non-English submission."


class InferenceInputError(ValueError):
    """A submitted grievance cannot safely be processed as inference input."""


class _Categorizer(Protocol):
    def predict(self, text: str) -> str: ...


class _Summarizer(Protocol):
    def summarize(self, text: str) -> str: ...


class _Router(Protocol):
    def route(
        self,
        *,
        category: str,
        subcategory: Optional[str] = None,
        district: Optional[str] = None,
    ) -> RoutingResult: ...


class PipelineGrievanceProcessor:
    """Warm real processor with dependency injection for deterministic tests."""

    name = "pipeline"

    def __init__(
        self,
        *,
        ocr: Callable[[bytes, str], OcrResult],
        redact: Callable[[str], str],
        detect_pii: Callable[[str], list[Any]],
        categorizer: _Categorizer,
        summarizer: _Summarizer,
        router: _Router,
        is_english_compatible: Callable[[str], bool],
        detect_language: Callable[[str], str],
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._ocr = ocr
        self._redact = redact
        self._detect_pii = detect_pii
        self._categorizer = categorizer
        self._summarizer = summarizer
        self._router = router
        self._is_english_compatible = is_english_compatible
        self._detect_language = detect_language
        self._now = now or (lambda: datetime.now(UTC))

    def process(
        self,
        *,
        grievance_id: str,
        ticket_no: str,
        text: Optional[str] = None,
        document_name: Optional[str] = None,
        document_bytes: Optional[bytes] = None,
        district: Optional[str] = None,
    ) -> GrievanceResult:
        """Process exactly one typed grievance or uploaded document."""
        _validate_input(
            text=text,
            document_name=document_name,
            document_bytes=document_bytes,
        )

        if text is not None:
            extracted_text = text
            extraction = ExtractionResult(source="text", extracted_text=text)
        else:
            assert document_name is not None and document_bytes is not None
            try:
                ocr_result = self._ocr(document_bytes, document_name)
            except OcrQualityError as exc:
                # Quality-rejected input is a legitimate client error -> 422.
                raise InferenceInputError(
                    f"document OCR failed the quality check: {exc}"
                ) from exc
            except Exception as exc:
                # Only genuine render/parse failures (corrupt or unsupported
                # file) map to a 422 here. `_is_document_input_failure` is
                # deliberately narrow: the injected page-type predictor also
                # runs inside `self._ocr` and is wrapped (see
                # `_guard_page_type_predict`) so its own bugs raise
                # `_PageTypeModelError` instead of a bare `ValueError`/
                # `IndexError` that could otherwise be misread as a corrupt
                # document — those propagate unchanged and become a 5xx.
                if not _is_document_input_failure(exc):
                    raise
                raise InferenceInputError(
                    f"document is corrupt or unsupported: {exc}"
                ) from exc

            if ocr_result.truncated:
                raise InferenceInputError(
                    "document exceeds the live page limit; no partial result was used"
                )
            if not ocr_result.full_text.strip():
                raise InferenceInputError("document OCR produced no text")

            relevant_pages = [
                page_text
                for page_text, page_type in ocr_result.per_page
                if page_type in RELEVANT_PAGE_TYPES and page_text.strip()
            ]
            if not relevant_pages:
                raise InferenceInputError(
                    "document contains no grievance-bearing pages"
                )

            extracted_text = ocr_result.full_text
            # Redact each selected page before joining so no irrelevant page
            # (identification, bill, miscellaneous) reaches either model.
            model_text_source = "\n\n".join(
                self._redact(page_text) for page_text in relevant_pages
            )
            extraction = ExtractionResult(
                source="document",
                extracted_text=extracted_text,
                ocr_model="pytesseract",
                pages=ocr_result.pages,
            )

        redacted_text = self._redact(extracted_text)
        pii_entities = [
            PIIEntity(entity=span.entity, start=span.start, end=span.end)
            for span in self._detect_pii(extracted_text)
        ]
        redaction = RedactionResult(
            redacted_text=redacted_text,
            entities=pii_entities,
        )

        classifier_text = (
            redacted_text if extraction.source == "text" else model_text_source
        )
        language = self._detect_language(classifier_text)
        is_english = self._is_english_compatible(classifier_text)
        if is_english:
            category = self._categorizer.predict(classifier_text)
            summary = self._summarizer.summarize(classifier_text)
        else:
            # Same gate as the categorizer: BART is only warmed for the
            # English demo target, and would otherwise hallucinate a summary
            # over text it was never validated on (both the typed-text and
            # document paths share this single check).
            category = "Uncategorized"
            summary = UNSUPPORTED_LANGUAGE_SUMMARY
        routing = self._router.route(category=category, district=district)

        return GrievanceResult(
            id=grievance_id,
            ticket_no=ticket_no,
            status="Submitted",
            submitted_on=self._now(),
            extraction=extraction,
            redaction=redaction,
            classification=ClassificationResult(
                category=category,
                language=language,
            ),
            summary=summary,
            routing=routing,
        )


def _validate_input(
    *,
    text: Optional[str],
    document_name: Optional[str],
    document_bytes: Optional[bytes],
) -> None:
    has_document_field = document_name is not None or document_bytes is not None
    if text is not None and has_document_field:
        raise InferenceInputError("provide exactly one of text or document")
    if text is None and not has_document_field:
        raise InferenceInputError("provide exactly one of text or document")
    if text is not None:
        if not text.strip():
            raise InferenceInputError("text is blank")
        return
    if not document_name or document_bytes is None:
        raise InferenceInputError("document name and bytes are both required")
    if not document_bytes:
        raise InferenceInputError("document is empty")
    suffix = Path(document_name).suffix.casefold()
    if suffix not in SUPPORTED_DOCUMENT_SUFFIXES:
        raise InferenceInputError(f"unsupported document type: {suffix or '(none)'}")


def _is_document_input_failure(exc: Exception) -> bool:
    """Recognize renderer/parser failures without importing their libraries."""
    return isinstance(exc, (ValueError, IndexError)) or exc.__class__.__name__ in {
        "PDFInfoNotInstalledError",
        "PDFPageCountError",
        "PDFSyntaxError",
        "PDFPopplerTimeoutError",
        "UnidentifiedImageError",
    }


class _PageTypeModelError(RuntimeError):
    """A page-type predictor bug caught mid-OCR.

    Deliberately not a `ValueError`/`IndexError` (and not one of the named
    renderer/parser exceptions) so `_is_document_input_failure` never mistakes
    it for a corrupt/unsupported document: page-type classification runs
    interleaved with page rendering inside `ocr_document`, and its own model
    bugs can surface as the same exception types a bad PDF does.
    """


def _guard_page_type_predict(
    predict: Callable[[Any], str],
) -> Callable[[Any], str]:
    """Wrap an injected page-type predictor so its failures can't be
    misreported as a corrupt/unsupported document (see `_PageTypeModelError`)."""

    def guarded(image: Any) -> str:
        try:
            return predict(image)
        except Exception as exc:
            raise _PageTypeModelError(str(exc)) from exc

    return guarded


def _detect_language(text: str) -> str:
    try:
        from langdetect import DetectorFactory, detect

        DetectorFactory.seed = 42
        return str(detect(text))
    except Exception:
        return "unknown"


def _require_model_artifact(candidates: tuple[Path, ...], component: str) -> None:
    if not any(path.is_file() for path in candidates):
        shown = " | ".join(str(path) for path in candidates)
        raise RuntimeError(
            f"missing local {component} artifact: {shown}. Run `dvc pull` "
            "for the mirrored models before starting the live API."
        )


def _require_binary(present: bool, binary: str, install_hint: str) -> None:
    if not present:
        raise RuntimeError(
            f"missing required OCR system binary {binary!r}. {install_hint}"
        )


def _tesseract_available() -> bool:
    """Resolve tesseract exactly as `pytesseract_backend` does, not just PATH.

    Reuses `pytesseract_backend._configure_tesseract` -- the backend's own
    resolver -- so this preflight can never drift from what OCR calls will
    actually find. That function tries, in order, `TESSERACT_CMD`, `PATH`,
    and the bundled `~/.local/tesseract` install, and sets
    `pytesseract.pytesseract.tesseract_cmd` as a side effect when one exists.
    A bare PATH-only check would false-abort startup on a box where the
    binary is only reachable via one of the other two mechanisms.
    """
    import pytesseract
    from janasunani.pipeline.stages.ocr_extraction.pytesseract_backend import (
        _configure_tesseract,
    )

    _configure_tesseract()
    resolved = pytesseract.pytesseract.tesseract_cmd
    return Path(resolved).is_file() or shutil.which(resolved) is not None


def _poppler_available() -> bool:
    """Resolve Poppler exactly as `page_renderer` does, not just PATH.

    Reuses `page_renderer.POPPLER_PATH` -- computed at import time from the
    bundled `~/.local/poppler` install (falling back to `/usr/bin`) -- the
    same value `page_renderer.render_page` passes to `pdf2image` as
    `poppler_path`. Falls back to a plain PATH lookup when that isn't set.

    `pdf2image.convert_from_path` (what `render_page` calls) shells out to
    BOTH `pdfinfo` (to read the page count) and `pdftoppm` (to rasterize
    pages) -- a partial install with only one of the two still raises
    `PDFInfoNotInstalledError` at request time. Both binaries must resolve
    for either the configured `POPPLER_PATH` or the PATH fallback to count.
    """
    from janasunani.pipeline.stages.ocr_extraction import page_renderer

    poppler_dir = page_renderer.POPPLER_PATH
    if poppler_dir:
        poppler_path = Path(poppler_dir)
        if (poppler_path / "pdfinfo").is_file() and (
            poppler_path / "pdftoppm"
        ).is_file():
            return True
    return shutil.which("pdfinfo") is not None and shutil.which("pdftoppm") is not None


def _require_ocr_dependencies() -> None:
    """Fail startup when the OCR system binaries pytesseract/pdf2image shell
    out to are absent.

    Without this, `build_processor` only imports the OCR *functions* -- it
    never checks that `tesseract`/Poppler are actually installed. `/health`
    would then report the `pipeline` processor healthy, and every document
    upload would only fail later, mid-request, as "corrupt/quality-rejected"
    input, instead of failing loudly at startup.

    Deliberately does not verify the Odia (`ori`) traineddata is installed:
    that requires shelling out to `tesseract --list-langs`, whose output
    format is not consistent enough across tesseract versions/platforms to
    check reliably here. Operators should confirm it manually post-install.
    """
    _require_binary(
        _tesseract_available(),
        "tesseract",
        "Install Tesseract OCR and the Odia language pack, e.g. "
        "`apt-get install tesseract-ocr tesseract-ocr-ori` or "
        "`brew install tesseract tesseract-lang`.",
    )
    _require_binary(
        _poppler_available(),
        "pdfinfo/pdftoppm",
        "Install Poppler so PDF pages can be rendered for OCR, e.g. "
        "`apt-get install poppler-utils` or `brew install poppler`.",
    )


def _resolve_models_root(models_dir: str | Path | None) -> Path:
    """Resolve the models root the same way for preflight and build."""
    configured_dir = models_dir or os.environ.get("JANASUNANI_MODELS_DIR")
    return Path(configured_dir).expanduser() if configured_dir else MODELS_DIR


def _required_model_files(root: Path) -> list[tuple[tuple[Path, ...], str]]:
    """The mandatory local model artifacts, as (candidate-paths, component).

    Single source of truth shared by `build_processor` (which hard-requires
    each) and `preflight` (which reports on each), so a fast pre-check can
    never disagree with what the real startup actually loads.

    Each entry lists one-or-more candidate paths; the requirement is satisfied
    when *any* candidate exists (e.g. HF weights may be `model.safetensors`
    *or* `pytorch_model.bin`). This covers not just the config/label encoder
    but the actual tokenizer/weight files the HF `from_pretrained` calls load
    during warm-up -- a partial DVC mirror (config present, weights missing)
    must fail the check, not sail through to a mid-warm-up crash.

    The tokenizer candidates are the files that actually carry the vocabulary
    -- `tokenizer.json` (a self-contained fast tokenizer) *or* `vocab.txt`
    (the MuRIL/BERT vocab loaded alongside `tokenizer_config.json`).
    `tokenizer_config.json` is deliberately NOT accepted on its own: it only
    holds tokenizer *settings*, so a mirror with just the config still crashes
    in `AutoTokenizer.from_pretrained`.
    """
    categorizer_dir = root / "categorizer"
    page_type_dir = root / "page_type_classifier" / "vit_type_classifier"
    return [
        ((categorizer_dir / "config.json",), "categorizer config"),
        (
            (
                categorizer_dir / "model.safetensors",
                categorizer_dir / "pytorch_model.bin",
            ),
            "categorizer weights",
        ),
        (
            (
                categorizer_dir / "tokenizer.json",
                categorizer_dir / "vocab.txt",
            ),
            "categorizer tokenizer",
        ),
        (
            (categorizer_dir / "label_encoder_ROS_wDOCS_english.pkl",),
            "categorizer label encoder",
        ),
        ((page_type_dir / "config.json",), "page-type config"),
        (
            (
                page_type_dir / "model.safetensors",
                page_type_dir / "pytorch_model.bin",
            ),
            "page-type weights",
        ),
        (
            (page_type_dir / "preprocessor_config.json",),
            "page-type image processor",
        ),
    ]


class DependencyCheck:
    """One demo-readiness dependency and whether it is satisfied.

    ``required`` separates "the processor cannot start" from "the demo will
    start and quietly show you less than you think". A missing model artifact
    is the first kind; an unmaterialized lake is the second — `/history`
    returns an empty page rather than an error, so nothing surfaces it. The
    second kind is advisory by default and fatal under ``--strict``, which is
    what the box runbook uses.
    """

    def __init__(
        self, name: str, ok: bool, detail: str, *, required: bool = True
    ) -> None:
        self.name = name
        self.ok = ok
        self.detail = detail
        self.required = required

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"DependencyCheck(name={self.name!r}, ok={self.ok!r})"


def resolve_explicit_oltp_url() -> Optional[str]:
    """Resolve ``OLTP_DB_URL`` through the same ``Settings`` layer the rest of
    the app uses, so a value set in the project ``.env`` (not just a
    shell-exported env var) is honored -- a raw ``os.environ`` read misses it
    and store selection silently falls back to ``InMemoryResultStore``.

    Re-instantiates ``Settings()`` (rather than importing the module-level
    singleton) so this always reflects the current process environment and
    ``.env`` file at call time.

    Returns None unless OLTP is *explicitly* configured: a value equal to
    ``DEFAULT_OLTP_DB_URL`` (the built-in local-SQLite fallback ``Settings``
    itself falls back to) is treated as "not configured", matching Phase 8C's
    contract of using ``DatabaseResultStore`` only for an explicit OLTP URL.

    Lives here rather than in ``serve`` so ``preflight`` can report the same
    answer ``create_live_app`` will act on, without importing the serving
    stack (which needs the ``serving`` extra).
    """
    resolved = Settings().OLTP_DB_URL
    return resolved if resolved != DEFAULT_OLTP_DB_URL else None


def _routing_mappings_check() -> DependencyCheck:
    """The ORTPSA master tables the router needs to produce real departments.

    ``load_mapping_tables`` returns None rather than raising when the CSVs are
    absent, and ``MappingRouter`` then falls back to the illustrative rule set.
    The API stays healthy and every response carries ``method:"fallback"``.
    On a box that has not run the scoped ``dvc pull``, that is the default.
    """
    try:
        from janasunani.routing.mappings import DEFAULT_MAPPING_DIR, load_mapping_tables

        tables = load_mapping_tables()
    except Exception as exc:  # pragma: no cover - defensive; never raise
        return DependencyCheck(
            "routing mappings", False, f"unavailable: {exc}", required=False
        )
    if tables is None:
        return DependencyCheck(
            "routing mappings",
            False,
            f"absent at {DEFAULT_MAPPING_DIR} -> routing degrades to "
            'method:"fallback" (run the scoped dvc pull)',
            required=False,
        )
    detail = (
        f"{len(tables.categories)} categories, "
        f"{len(tables.category_to_department)} with a derivable department"
    )
    if not tables.categories or not tables.category_to_department:
        # Present but empty is the same runtime failure as absent: a
        # header-only or truncated CSV pull parses without error, but
        # MappingRouter has nothing to route with and falls back the same
        # way it would with no tables at all. A corrupt pull should not read
        # as a healthy strict preflight just because the files exist.
        return DependencyCheck(
            "routing mappings",
            False,
            detail + ' -> effectively empty, routing degrades to method:"fallback" '
            "(mapping pull looks truncated or corrupt)",
            required=False,
        )
    return DependencyCheck("routing mappings", True, detail, required=False)


# The exact columns janasunani/serving/history.py's LakeHistory.search()
# selects. Duplicated here (not imported) rather than sharing a constant with
# janasunani.serving.history: preflight lives in this module specifically so
# it does not need the `serving` extra (see resolve_explicit_oltp_url's
# docstring), and importing from janasunani.serving would reintroduce exactly
# that dependency. Keep in sync with history.py's `columns` if either drifts.
_HISTORY_COLUMNS = (
    "ticket_no",
    "created_on",
    "district",
    "category",
    "subcategory",
    "dept",
    "status",
    "office",
    "grievance",
)


def _lake_check() -> DependencyCheck:
    """The Parquet lake behind ``GET /history``.

    ``LakeHistory`` returns an empty page when ``complaints.parquet`` is
    missing, so an unmaterialized lake looks identical to "no results" in the
    UI. A present-but-malformed lake is a quieter version of the same
    failure: a row count alone can pass while a column
    ``LakeHistory.search()`` actually selects is missing, stale, or renamed,
    which only surfaces once a real ``/history`` request hits it. The
    ``LIMIT 0`` select below asks DuckDB to bind every one of those columns
    without materializing any rows, so a schema mismatch fails preflight
    instead of a request.
    """
    try:
        from janasunani.olap import lake

        path = lake.lake_path("complaints")
        if not path.is_file():
            return DependencyCheck(
                "history lake",
                False,
                f"{path} absent -> /history returns an empty page, not an error",
                required=False,
            )
        import duckdb

        conn = duckdb.connect()
        rows = conn.execute(
            f"SELECT count(*) FROM read_parquet('{path.as_posix()}')"
        ).fetchone()[0]
        conn.execute(
            f"SELECT {', '.join(_HISTORY_COLUMNS)} "
            f"FROM read_parquet('{path.as_posix()}') LIMIT 0"
        )
    except Exception as exc:  # pragma: no cover - defensive; never raise
        return DependencyCheck("history lake", False, f"unreadable: {exc}", required=False)
    return DependencyCheck(
        "history lake", rows > 0, f"{rows:,} complaints in {path}", required=False
    )


_OLTP_PROBE_TIMEOUT_S = 5.0


def _probe_oltp_connection(url: str, timeout: float = _OLTP_PROBE_TIMEOUT_S) -> None:
    """Open a real connection and run a harmless query; raises on failure.

    Separated from `_oltp_check` so tests can stub this out rather than
    touching a real database or the network -- the same collection/judgement
    split `scripts/infra_status.py` uses for its own AWS/SSH/HTTP calls, for
    the same reason: a test suite must never depend on real infrastructure
    being reachable, and preflight tests must never point at a real database.
    Bounded by `timeout` so an unreachable host degrades this one check
    within a few seconds rather than hanging preflight indefinitely.
    """
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _probe() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    asyncio.run(asyncio.wait_for(_probe(), timeout=timeout))


def _oltp_check() -> DependencyCheck:
    """Whether live submissions will survive a restart -- verified with a
    real connection, not just an explicit URL. `DatabaseResultStore`
    (janasunani/serving/store.py) only creates its async engine at startup
    and never actually connects until the first save, so a wrong password,
    host, database, or a migration that never ran would otherwise pass
    preflight clean and only surface when a citizen's submission fails to
    save.
    """
    # `required` is a property of the check, not of this run's outcome, so it
    # stays False on both branches below.
    url = resolve_explicit_oltp_url()
    if not url:
        return DependencyCheck(
            "oltp store",
            False,
            "OLTP_DB_URL not set -> InMemoryResultStore, submissions lost on restart",
            required=False,
        )
    try:
        _probe_oltp_connection(url)
    except Exception as exc:
        # Never str(exc): some DBAPI errors embed the DSN -- and its
        # password -- in their own message. The exception's type name is
        # actionable (auth vs. host vs. timeout look different) without that
        # risk; never the URL itself, which is what resolve_explicit_oltp_url
        # returns and must not appear here either.
        return DependencyCheck(
            "oltp store",
            False,
            f"explicit OLTP_DB_URL set but unreachable ({type(exc).__name__})",
            required=False,
        )
    return DependencyCheck(
        "oltp store",
        True,
        "explicit OLTP_DB_URL -> DatabaseResultStore, connection verified",
        required=False,
    )


def preflight(models_dir: str | Path | None = None) -> list[DependencyCheck]:
    """Report demo readiness without loading any model weights.

    Mirrors exactly what `build_processor` hard-requires -- the mandatory
    local artifacts (via `_required_model_files`) and the OCR system binaries
    -- so an operator can verify a box is demo-ready in milliseconds instead
    of discovering a missing file minutes into the model warm-up. Never raises
    for a missing dependency; each check is reported as `ok=False` instead.
    """
    root = _resolve_models_root(models_dir)
    checks = [
        DependencyCheck(
            component,
            any(path.is_file() for path in candidates),
            " | ".join(str(path) for path in candidates),
        )
        for candidates, component in _required_model_files(root)
    ]

    def _binary_check(name: str, probe: Callable[[], bool], detail: str) -> None:
        try:
            ok = probe()
        except Exception as exc:  # pipeline-core extra absent, etc.
            checks.append(DependencyCheck(name, False, f"unavailable: {exc}"))
        else:
            checks.append(DependencyCheck(name, ok, detail))

    _binary_check("tesseract", _tesseract_available, "OCR text-extraction binary")
    _binary_check(
        "pdfinfo/pdftoppm", _poppler_available, "PDF page renderer (poppler)"
    )

    # Advisory checks: none of these stop `build_processor`, and all three fail
    # in ways the running demo does not surface. See DependencyCheck.required.
    # _oltp_check is the one exception to "milliseconds" above when
    # OLTP_DB_URL is explicitly set: it opens a real, timeout-bounded
    # connection (_OLTP_PROBE_TIMEOUT_S) rather than just checking presence.
    checks.append(_routing_mappings_check())
    checks.append(_lake_check())
    checks.append(_oltp_check())
    return checks


def build_processor(models_dir: str | Path | None = None) -> PipelineGrievanceProcessor:
    """Strictly construct and warm the production processor.

    Local page-type and categorizer artifacts are mandatory.  Any missing
    dependency, artifact, public BART load, or Presidio initialization error
    propagates and aborts startup; this function never substitutes the mock.
    """
    root = _resolve_models_root(models_dir)
    categorizer_dir = root / "categorizer"
    page_type_dir = root / "page_type_classifier" / "vit_type_classifier"

    for candidates, component in _required_model_files(root):
        _require_model_artifact(candidates, component)
    _require_ocr_dependencies()

    # Import and construct in pipeline order. This also keeps the module-level
    # mock app usable when none of the ML extras are installed.
    from janasunani.pipeline.stages.page_type_classifier import _PageTypeClassifier

    page_type = _PageTypeClassifier(str(page_type_dir))

    from janasunani.pipeline.stages.summarizer import Summarizer

    summarizer = Summarizer()

    from janasunani.pipeline.stages.categorizer.model import GrievanceCategorizer

    categorizer = GrievanceCategorizer(str(categorizer_dir))

    from janasunani.pipeline.stages.pii_tagger import (
        _get_engines,
        detect_pii_spans,
        redact_text,
    )

    _get_engines()

    from janasunani.pipeline.ocr_quality import is_repetition_collapsed
    from janasunani.pipeline.stages.categorizer.stage import _is_english
    from janasunani.pipeline.stages.ocr_extraction.page_renderer import render_page
    from janasunani.pipeline.stages.ocr_extraction.pytesseract_backend import (
        extract_text,
    )
    from janasunani.routing.rules import DEFAULT_ROUTER

    def run_ocr(document_bytes: bytes, document_name: str) -> OcrResult:
        from janasunani.inference.ocr import ocr_document

        return ocr_document(
            document_bytes,
            document_name,
            extract_text_fn=extract_text,
            render_page_fn=render_page,
            page_type_predict=_guard_page_type_predict(page_type.predict),
            quality_check_fn=lambda value: bool(value.strip())
            and not is_repetition_collapsed(value),
        )

    return PipelineGrievanceProcessor(
        ocr=run_ocr,
        redact=redact_text,
        detect_pii=detect_pii_spans,
        categorizer=categorizer,
        summarizer=summarizer,
        router=DEFAULT_ROUTER,
        is_english_compatible=_is_english,
        detect_language=_detect_language,
    )
