"""Warm, single-grievance inference behind the frozen serving contract.

The module stays import-light so the default mock API does not require any ML
extras.  ``build_processor`` is the strict production constructor: it checks
for the locally mirrored model artifacts, then imports and warms every heavy
component exactly once.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from janasunani.config import DEFAULT_OLTP_DB_URL, MODELS_DIR, Settings
from janasunani.inference.ocr import OcrQualityError, OcrResult
from janasunani.inference.timing import NullTimer, StageTimer, TimingSink
from janasunani.pipeline.spam import is_content_free_abuse
from janasunani.pipeline.stages.page_type_classifier import PAGE_TYPE_CLASS_BY_LABEL
from janasunani.routing.provider import RoutingProvider, router_from_env, router_status
from janasunani.serving.schemas import (
    ClassificationResult,
    ExtractionResult,
    GrievanceResult,
    PIIEntity,
    RedactionResult,
    RoutingResult,
)
from janasunani.serving.triage import (
    TriageProvider,
    TriageUnavailableError,
    UnwiredTriageProvider,
    triage_provider_from_env,
    triage_status,
    unavailable_triage,
)

logger = logging.getLogger(__name__)

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
LOW_SIGNAL_SUMMARY = (
    "Summary not generated for a low-signal submission; officer review requested."
)


def _low_signal_manual_route(district: Optional[str]) -> RoutingResult:
    """Abstain from automated routing while keeping the submission reviewable.

    A bounded spam heuristic is not validated to select a department.  When it
    identifies the exact content-free regression case, send the submission to
    the generic grievance-cell intake with zero confidence instead of feeding a
    fabricated ``Uncategorized`` label into whichever automated router is live.
    """

    from janasunani.routing.rules import FALLBACK_DEPT, FALLBACK_DESIGNATION

    district_name = " ".join((district or "").split()) or "State"
    return RoutingResult(
        dept=FALLBACK_DEPT,
        office=f"Public Grievance Cell, {district_name}",
        designation=FALLBACK_DESIGNATION,
        escalation_authority=f"Collectorate, {district_name}",
        confidence=0.0,
        method="fallback",
    )


class InferenceInputError(ValueError):
    """A submitted grievance cannot safely be processed as inference input."""


class _Categorizer(Protocol):
    def predict(self, text: str) -> str: ...


class _Summarizer(Protocol):
    def summarize(self, text: str) -> str: ...


#: The routing seam now lives with the routers it describes. Kept as an alias
#: because this private name is the annotation on the processor constructor and
#: is imported by tests.
_Router = RoutingProvider


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
        triage_provider: TriageProvider | None = None,
        now: Callable[[], datetime] | None = None,
        timing_sink: TimingSink | None = None,
    ) -> None:
        self._ocr = ocr
        self._redact = redact
        self._detect_pii = detect_pii
        self._categorizer = categorizer
        self._summarizer = summarizer
        self._router = router
        self._is_english_compatible = is_english_compatible
        self._detect_language = detect_language
        self._triage_provider = triage_provider or UnwiredTriageProvider()
        self._now = now or (lambda: datetime.now(UTC))
        # Side channel only. Timings must never reach GrievanceResult, which is
        # the frozen serving contract the frontend and result store depend on.
        self._timing_sink = timing_sink

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
        timer = StageTimer() if self._timing_sink is not None else NullTimer()
        # Tracks whether `process` reached its normal return, so the emitted
        # timings are distinguishable from a submission that stopped partway
        # through a raised stage -- otherwise a latency report would silently
        # mix completed and partial measurements under the same label.
        completed = False
        try:
            if text is not None:
                extracted_text = text
                extraction = ExtractionResult(source="text", extracted_text=text)
            else:
                assert document_name is not None and document_bytes is not None
                try:
                    with timer.stage("ocr"):
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
                with timer.stage("redact"):
                    model_text_source = "\n\n".join(
                        self._redact(page_text) for page_text in relevant_pages
                    )
                extraction = ExtractionResult(
                    source="document",
                    extracted_text=extracted_text,
                    ocr_model="pytesseract",
                    pages=ocr_result.pages,
                )

            with timer.stage("redact"):
                redacted_text = self._redact(extracted_text)
            with timer.stage("detect_pii"):
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
            submitted_on = self._now()
            with timer.stage("triage"):
                try:
                    # The provider receives the same redacted grievance-bearing
                    # text as the downstream models. For documents this excludes
                    # identification, bill, and miscellaneous pages; raw OCR and
                    # typed citizen text never reach triage.
                    triage = self._triage_provider.assess(
                        redacted_text=classifier_text,
                        district=district,
                        submitted_on=submitted_on,
                    )
                except TriageUnavailableError:
                    # A triage outage cannot reject or delay a grievance.  Do not
                    # surface the provider exception: it may contain infrastructure
                    # details and is not an officer-facing explanation.
                    logger.warning(
                        "advisory triage unavailable; accepting grievance without it"
                    )
                    triage = unavailable_triage()

            with timer.stage("detect_language"):
                language = self._detect_language(classifier_text)
                is_english = self._is_english_compatible(classifier_text)
            bounded_no_grievance_review = (
                triage.spam.decision == "review"
                and triage.spam.reason_code == "low_signal_no_grievance"
            )
            observed_content_free_regression = (
                bounded_no_grievance_review
                and is_content_free_abuse(classifier_text)
            )
            if observed_content_free_regression:
                # Triage runs on the redacted model input before downstream
                # models. A named, content-free regression does not need model
                # calls, and a language-detector guess must not become the
                # explanation for why no summary was produced. Broader bounded-
                # spam reviews stay advisory and continue through the normal
                # category/routing path; only this exact regression takes manual
                # intake.
                category = "Uncategorized"
                summary = LOW_SIGNAL_SUMMARY
                with timer.stage("route"):
                    routing = _low_signal_manual_route(district)
            elif is_english:
                with timer.stage("categorize"):
                    category = self._categorizer.predict(classifier_text)
                with timer.stage("summarize"):
                    summary = self._summarizer.summarize(classifier_text)
                with timer.stage("route"):
                    routing = self._router.route(category=category, district=district)
            else:
                # Same gate as the categorizer: BART is only warmed for the
                # English demo target, and would otherwise hallucinate a summary
                # over text it was never validated on (both the typed-text and
                # document paths share this single check).
                category = "Uncategorized"
                summary = UNSUPPORTED_LANGUAGE_SUMMARY
                with timer.stage("route"):
                    routing = self._router.route(category=category, district=district)

            result = GrievanceResult(
                id=grievance_id,
                ticket_no=ticket_no,
                status="Submitted",
                submitted_on=submitted_on,
                extraction=extraction,
                redaction=redaction,
                classification=ClassificationResult(
                    category=category,
                    language=language,
                ),
                summary=summary,
                routing=routing,
                triage=triage,
            )
            completed = True
            return result
        finally:
            # An outer `finally`, not a call at the end of the happy path: a
            # stage's own `finally` (StageTimer.stage) already records elapsed
            # time when that stage raises, but the previous code only reached
            # `emit` after a normal return, so a raised stage discarded
            # exactly the measurement that design was for. The original
            # exception, if any, propagates unchanged -- this only ever adds a
            # side-effecting emit, never swallows or replaces it.
            timer.emit(self._timing_sink, ok=completed)


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
    # langdetect is unstable on very short strings (the observed regression
    # labelled "i am an idiot" as Welsh).  Abstain until there is enough text
    # for a language label to be meaningful; actionability/low-signal triage
    # still runs before this on the redacted input.
    if not isinstance(text, str) or len(text.strip()) < 30:
        return "unknown"
    try:
        from langdetect import DetectorFactory, detect

        DetectorFactory.seed = 42
        return str(detect(text))
    except Exception:
        return "unknown"


def _usable_model_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _require_model_artifact(candidates: tuple[Path, ...], component: str) -> None:
    if not any(_usable_model_file(path) for path in candidates):
        shown = " | ".join(str(path) for path in candidates)
        raise RuntimeError(
            f"missing local {component} artifact or artifact is empty: {shown}. "
            "Run `dvc pull` for the mirrored models before starting the live API."
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


def _resolved_model_dirs(
    root: Path,
    *,
    verified_artifacts: dict[tuple[Path, str], Path] | None = None,
) -> tuple[Path, Path, Path]:
    """Resolve operator override -> pinned release -> DVC for live models."""

    from janasunani.tracking.artifacts import resolve_artifact

    unresolved = root / ".unresolved"
    categorizer = resolve_artifact(
        "categorizer", models_dir=root, verified_artifacts=verified_artifacts
    ) or (unresolved / "categorizer")
    page_type = resolve_artifact(
        "page_type_classifier",
        models_dir=root,
        verified_artifacts=verified_artifacts,
    ) or (unresolved / "page_type_classifier")
    if not (page_type / "config.json").is_file():
        page_type = page_type / "vit_type_classifier"
    summarizer = resolve_artifact(
        "summarizer", models_dir=root, verified_artifacts=verified_artifacts
    ) or (unresolved / "summarizer")
    return categorizer, page_type, summarizer


def _required_model_files(
    root: Path,
    *,
    resolved_dirs: tuple[Path, Path, Path] | None = None,
) -> list[tuple[tuple[Path, ...], str]]:
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
    categorizer_dir, page_type_dir, summarizer_dir = (
        resolved_dirs if resolved_dirs is not None else _resolved_model_dirs(root)
    )
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
        ((summarizer_dir / "config.json",), "summarizer config"),
        (
            (
                summarizer_dir / "model.safetensors",
                summarizer_dir / "pytorch_model.bin",
            ),
            "summarizer weights",
        ),
        (
            (
                summarizer_dir / "tokenizer.json",
                summarizer_dir / "vocab.json",
            ),
            "summarizer tokenizer",
        ),
        ((summarizer_dir / "merges.txt",), "summarizer merges"),
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


def _router_check() -> DependencyCheck:
    """Which routing rung will actually answer the first live submission.

    ``_routing_mappings_check`` above covers the CSV masters, which are the
    *second* rung. This one covers the selection itself, because those are
    different failures: the masters can be present and healthy while the
    crosswalk artifact is missing, and the demo then serves ``method:"rules"``
    or ``"fallback"`` while everyone believes routing is learned.
    PERFORMANCE.md recorded exactly that state on 7 August.
    """
    try:
        name, ok, detail = router_status()
    except Exception as exc:  # pragma: no cover - defensive; never raise
        return DependencyCheck("router", False, f"unavailable: {exc}", required=False)
    return DependencyCheck(f"router ({name})", ok, detail, required=False)


def _triage_check(
    root: Path,
    *,
    verified_artifacts: dict[tuple[Path, str], Path] | None = None,
) -> DependencyCheck:
    """Which spam scorer is live, and whether it is learned or heuristic.

    Reported so the distinction cannot be lost between the code and the
    narrative: the bounded ``spam-v1.1-bounded`` cascade remains the safe
    fallback, while an optional checksummed actionability model is a separate
    advisory output. Neither may block a submission.
    """
    try:
        name, ok, detail = triage_status(
            models_dir=root,
            verified_artifacts=verified_artifacts,
        )
    except Exception as exc:  # pragma: no cover - defensive; never raise
        return DependencyCheck("triage", False, f"unavailable: {exc}", required=False)
    return DependencyCheck(f"triage ({name})", ok, detail, required=False)


def _model_release_check(
    *, verified_artifacts: dict[tuple[Path, str], Path] | None = None
) -> DependencyCheck:
    """Expose the active immutable model release without revealing endpoints.

    Serving never resolves MLflow aliases here. This check only reads the
    local active pointer/manifest, validates every pinned local artifact, and
    reports immutable versions plus short checksums for operator visibility.
    """
    try:
        from janasunani.tracking.release import (
            active_manifest_path,
            load_manifest,
            resolve_manifest_artifact,
        )
        from janasunani.tracking.artifacts import artifact_override_env_var

        path = active_manifest_path()
        if path is None:
            return DependencyCheck(
                "model release",
                False,
                "no active immutable release manifest; using operator/DVC resolution",
                required=False,
            )
        manifest = load_manifest(path)
        versions = []
        shadowed = []
        for name in sorted(manifest.models):
            model = manifest.models[name]
            if os.environ.get(artifact_override_env_var(name)):
                shadowed.append(name)
            if model.artifact_path is not None:
                resolve_manifest_artifact(
                    name,
                    manifest_path=path,
                    verified_artifacts=verified_artifacts,
                )
                identity = f"sha256:{model.artifact_sha256[:12]}"
            else:
                identity = "authorized-hosted"
            versions.append(f"{name}@{model.version} ({identity})")
    except Exception as exc:  # pragma: no cover - defensive; never raise
        return DependencyCheck(
            "model release", False, f"invalid active release: {exc}", required=False
        )
    if shadowed:
        return DependencyCheck(
            "model release",
            False,
            f"release_id={manifest.release_id}; operator override shadows "
            f"manifest model(s): {', '.join(shadowed)}; effective served bytes "
            "must be verified separately; "
            + ", ".join(versions),
            required=False,
        )
    return DependencyCheck(
        "model release",
        True,
        f"release_id={manifest.release_id}; " + ", ".join(versions),
        required=False,
    )


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
    """Open a real connection, then confirm `live_grievances` exists; raises
    on failure of either.

    Separated from `_oltp_check` so tests can stub this out rather than
    touching a real database or the network -- the same collection/judgement
    split `scripts/infra_status.py` uses for its own AWS/SSH/HTTP calls, for
    the same reason: a test suite must never depend on real infrastructure
    being reachable, and preflight tests must never point at a real database.
    Bounded by `timeout` so an unreachable host degrades this one check
    within a few seconds rather than hanging preflight indefinitely.

    The table check is existence only, not a migration-head check: `SELECT`
    against `LiveGrievance.__table__` (janasunani/db/models.py, exactly what
    `DatabaseResultStore.save` writes to) with `LIMIT 0`, so it raises the
    same way an unreadable connection does if the table is absent, without
    reading any row. Deliberately does not run or verify Alembic migrations
    -- no migration is authored by this check, and none should be; this only
    confirms the one table `DatabaseResultStore` needs is there, the same way
    `SELECT 1` above only confirms the connection is there.
    """
    import asyncio

    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import create_async_engine

    from janasunani.db.models import LiveGrievance

    async def _probe() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                await conn.execute(select(LiveGrievance.id).limit(0))
        finally:
            await engine.dispose()

    asyncio.run(asyncio.wait_for(_probe(), timeout=timeout))


def _oltp_check() -> DependencyCheck:
    """Whether live submissions will survive a restart -- verified with a
    real connection and the `live_grievances` table's existence, not just an
    explicit URL. `DatabaseResultStore` (janasunani/serving/store.py) only
    creates its async engine at startup and never actually connects until
    the first save, so a wrong password, host, database, or a migration that
    never ran would otherwise pass preflight clean and only surface when a
    citizen's submission fails to save.
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
        # actionable (a connection failure and a missing table raise
        # different exception types) without that risk; never the URL
        # itself, which is what resolve_explicit_oltp_url returns and must
        # not appear here either.
        return DependencyCheck(
            "oltp store",
            False,
            f"explicit OLTP_DB_URL set but not usable ({type(exc).__name__}: "
            "unreachable, or live_grievances is missing -- run the Alembic migration)",
            required=False,
        )
    return DependencyCheck(
        "oltp store",
        True,
        "explicit OLTP_DB_URL -> DatabaseResultStore, connection and "
        "live_grievances both verified",
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
    verified_artifacts: dict[tuple[Path, str], Path] = {}
    resolved_dirs = _resolved_model_dirs(
        root, verified_artifacts=verified_artifacts
    )
    checks = [
        DependencyCheck(
            component,
            any(_usable_model_file(path) for path in candidates),
            " | ".join(str(path) for path in candidates),
        )
        for candidates, component in _required_model_files(
            root, resolved_dirs=resolved_dirs
        )
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
    checks.append(_model_release_check(verified_artifacts=verified_artifacts))
    checks.append(_router_check())
    checks.append(
        _triage_check(root, verified_artifacts=verified_artifacts)
    )
    checks.append(_lake_check())
    checks.append(_oltp_check())
    return checks


def build_processor(
    models_dir: str | Path | None = None,
    *,
    router: "RoutingProvider | None" = None,
    triage_provider: TriageProvider | None = None,
    timing_sink: TimingSink | None = None,
) -> PipelineGrievanceProcessor:
    """Strictly construct and warm the production processor.

    Local page-type and categorizer artifacts are mandatory.  Any missing
    dependency, artifact, public BART load, or Presidio initialization error
    propagates and aborts startup; this function never substitutes the mock.

    ``router`` and ``triage_provider`` are the swap points. Both default to
    the environment factories rather than to a hard-coded singleton, which is
    what lets a trained model replace either one without a call-site change.
    Explicit injection still wins over the environment, matching the idiom
    ``create_app`` already uses for its providers.
    """
    root = _resolve_models_root(models_dir)
    verified_artifacts: dict[tuple[Path, str], Path] = {}
    resolved_dirs = _resolved_model_dirs(
        root, verified_artifacts=verified_artifacts
    )
    categorizer_dir, page_type_dir, summarizer_dir = resolved_dirs

    for candidates, component in _required_model_files(
        root, resolved_dirs=resolved_dirs
    ):
        _require_model_artifact(candidates, component)
    _require_ocr_dependencies()

    # Import and construct in pipeline order. This also keeps the module-level
    # mock app usable when none of the ML extras are installed.
    from janasunani.pipeline.stages.page_type_classifier import _PageTypeClassifier

    page_type = _PageTypeClassifier(str(page_type_dir))

    from janasunani.pipeline.stages.summarizer import Summarizer

    summarizer = Summarizer(summarizer_dir)

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
        router=router if router is not None else router_from_env(),
        is_english_compatible=_is_english,
        detect_language=_detect_language,
        triage_provider=(
            triage_provider
            if triage_provider is not None
            else triage_provider_from_env(
                models_dir=root,
                verified_artifacts=verified_artifacts,
            )
        ),
        timing_sink=timing_sink,
    )
