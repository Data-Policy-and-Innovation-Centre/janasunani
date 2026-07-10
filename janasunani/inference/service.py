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

from janasunani.config import MODELS_DIR
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
        if self._is_english_compatible(classifier_text):
            category = self._categorizer.predict(classifier_text)
        else:
            category = "Uncategorized"
        summary = self._summarizer.summarize(classifier_text)
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


def _require_file(path: Path, component: str) -> None:
    if not path.is_file():
        raise RuntimeError(
            f"missing local {component} artifact: {path}. Run `dvc pull` "
            "for the mirrored models before starting the live API."
        )


def _require_binary(binary: str, install_hint: str) -> None:
    if shutil.which(binary) is None:
        raise RuntimeError(
            f"missing required OCR system binary {binary!r}. {install_hint}"
        )


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
        "tesseract",
        "Install Tesseract OCR and the Odia language pack, e.g. "
        "`apt-get install tesseract-ocr tesseract-ocr-ori` or "
        "`brew install tesseract tesseract-lang`.",
    )
    _require_binary(
        "pdftoppm",
        "Install Poppler so PDF pages can be rendered for OCR, e.g. "
        "`apt-get install poppler-utils` or `brew install poppler`.",
    )


def build_processor(models_dir: str | Path | None = None) -> PipelineGrievanceProcessor:
    """Strictly construct and warm the production processor.

    Local page-type and categorizer artifacts are mandatory.  Any missing
    dependency, artifact, public BART load, or Presidio initialization error
    propagates and aborts startup; this function never substitutes the mock.
    """
    configured_dir = models_dir or os.environ.get("JANASUNANI_MODELS_DIR")
    root = Path(configured_dir).expanduser() if configured_dir else MODELS_DIR
    categorizer_dir = root / "categorizer"
    page_type_dir = root / "page_type_classifier" / "vit_type_classifier"

    _require_file(categorizer_dir / "config.json", "categorizer")
    _require_file(
        categorizer_dir / "label_encoder_ROS_wDOCS_english.pkl",
        "categorizer label encoder",
    )
    _require_file(page_type_dir / "config.json", "page-type model")
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
