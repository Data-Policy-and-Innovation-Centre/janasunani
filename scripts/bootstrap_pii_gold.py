"""Bootstrap a PII gold-label file from an English complaints bundle.

Takes the zip produced by scripts/sample_english_complaints.py, OCRs every
document page (pytesseract, confidence-picked language), pre-annotates the
text with the PRODUCTION Presidio analyzer (`detect_pii_spans`), and writes a
draft gold file in the JSONL format `janasunani-evaluate-pii` consumes:

    {"id": "<ticket>_p<page>", "text": "...", "entities": [
        {"start": 0, "end": 6, "entity": "NAME"}, ...]}

⚠ The draft is a STARTING POINT, not gold. It contains exactly what the
analyzer already finds, so evaluating against it unedited would score ~100%
recall by construction. The human pass is the measurement:

  1. ADD spans the analyzer missed  — these become the recall misses;
  2. DELETE spans that aren't PII   — false positives;
  3. FIX boundaries and labels      — exact-match quality.

Then: uv run --extra pipeline-core janasunani-evaluate-pii --gold <corrected>

Run (pipeline-core env: tesseract binary + presidio + spaCy model needed):

    uv run --extra pipeline-core python scripts/bootstrap_pii_gold.py \
        [--bundle data/output/english_complaints_sample.zip] \
        [--out data/output/pii_gold_draft.jsonl] [--max-pages-per-doc N]

Gold files hold real citizen text and must NEVER be committed to git. The
draft lives under data/output/ (gitignored, regenerable). The CORRECTED gold
is irreplaceable human work: promote it to data/external/pii_gold.jsonl and
DVC-track it — only the .dvc pointer enters git; the bytes go to the private
S3 remote (same posture as the raw dump). See scripts/README.md,
"Gold-file lifecycle".
"""

from __future__ import annotations

import argparse
import json
import tempfile
import zipfile
from pathlib import Path

from loguru import logger

from janasunani.config import OUTPUT_DATA_DIR

# Pages whose OCR yields less text than this are skipped — nothing there to
# label, and empty lines would just pad the eval denominator with noise.
_MIN_TEXT_CHARS = 40


def render_annotated(text: str, spans) -> str:
    """Text with detected spans marked inline: ⟦NAME:Ramesh Kumar⟧.

    For the human review pass — read this, then edit the JSONL. Overlapping
    spans are rendered first-wins (the JSONL still carries all of them).
    """
    parts: list[str] = []
    cursor = 0
    for span in sorted(spans, key=lambda s: (s.start, s.end)):
        if span.start < cursor:
            continue
        parts.append(text[cursor : span.start])
        parts.append(f"⟦{span.entity}:{text[span.start:span.end]}⟧")
        cursor = span.end
    parts.append(text[cursor:])
    return "".join(parts)


def spans_to_jsonl_line(example_id: str, text: str, spans) -> str:
    """One gold-draft JSONL line in the pii_eval input format."""
    return json.dumps(
        {
            "id": example_id,
            "text": text,
            "entities": [
                {"start": span.start, "end": span.end, "entity": span.entity}
                for span in spans
            ],
        },
        ensure_ascii=False,
    )


def _iter_document_pages(doc_path: Path):
    """(page_number, PIL image) for every page of a PDF/image document."""
    if doc_path.suffix.lower() == ".pdf":
        from pdf2image import pdfinfo_from_path

        from janasunani.pipeline.stages.page_type_classifier import (
            POPPLER_PATH,
            _render_pdf_page,
        )

        n_pages = int(
            pdfinfo_from_path(str(doc_path), poppler_path=POPPLER_PATH)["Pages"]
        )
        for page_number in range(1, n_pages + 1):
            yield page_number, _render_pdf_page(doc_path, page_number)
    else:
        from PIL import Image

        with Image.open(doc_path) as image:
            yield 1, image.convert("RGB")


def bootstrap(bundle: Path, out_path: Path, max_pages_per_doc: int | None) -> int:
    """OCR + pre-annotate every page in the bundle; returns pages written."""
    from janasunani.pipeline.stages.ocr_extraction.pytesseract_backend import (
        extract_text,
    )
    from janasunani.pipeline.stages.pii_tagger import detect_pii_spans
    from janasunani.pipeline.ticket import doc_id_from_relpath

    out_path.parent.mkdir(parents=True, exist_ok=True)
    review_path = out_path.with_suffix(".review.txt")
    written = 0
    skipped = 0

    with (
        tempfile.TemporaryDirectory() as tmp,
        out_path.open("w") as out,
        review_path.open("w") as review,
    ):
        tmp_dir = Path(tmp)
        with zipfile.ZipFile(bundle) as zf:
            doc_names = [
                name
                for name in zf.namelist()
                if name.startswith("documents/") and not name.endswith("/")
            ]
            logger.info(f"{bundle.name}: {len(doc_names)} document(s)")
            zf.extractall(tmp_dir, members=doc_names)

        for name in sorted(doc_names):
            doc_path = tmp_dir / name
            relpath = name.removeprefix("documents/")
            ticket = doc_id_from_relpath(relpath)
            for page_number, image in _iter_document_pages(doc_path):
                if max_pages_per_doc and page_number > max_pages_per_doc:
                    logger.debug(f"{ticket}: page cap reached, moving on")
                    break
                text = extract_text(image)
                if len(text.strip()) < _MIN_TEXT_CHARS:
                    logger.debug(
                        f"{ticket} p{page_number}: {len(text.strip())} chars "
                        "of OCR text — skipped"
                    )
                    skipped += 1
                    continue
                spans = detect_pii_spans(text)
                example_id = f"{ticket}_p{page_number}"
                out.write(spans_to_jsonl_line(example_id, text, spans) + "\n")
                review.write(
                    f"{'=' * 70}\n{example_id}  "
                    f"({len(spans)} span(s))\n{'=' * 70}\n"
                    f"{render_annotated(text, spans)}\n\n"
                )
                written += 1
                logger.info(
                    f"{ticket} p{page_number}: {len(text)} chars, "
                    f"{len(spans)} pre-annotated span(s) "
                    f"({', '.join(sorted({s.entity for s in spans})) or 'none'})"
                )

    logger.success(
        f"wrote {out_path}: {written} page(s) pre-annotated, {skipped} skipped "
        "(too little text)"
    )
    logger.warning(
        "This draft scores ~100% against the analyzer BY CONSTRUCTION. "
        "Label pass required: ADD missed PII, DELETE false hits, FIX "
        "boundaries — then run janasunani-evaluate-pii --gold <corrected file>."
    )
    logger.info(
        "When the label pass is done, promote the corrected file to the "
        "DVC-tracked gold set (survives this machine; content stays out of "
        "git):\n"
        "  mkdir -p data/external && mv <corrected> data/external/pii_gold.jsonl\n"
        "  dvc add data/external/pii_gold.jsonl && dvc push\n"
        "  git add data/external/pii_gold.jsonl.dvc  # commit the pointer"
    )
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--bundle",
        type=Path,
        default=OUTPUT_DATA_DIR / "english_complaints_sample.zip",
        help="zip from scripts/sample_english_complaints.py",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=OUTPUT_DATA_DIR / "pii_gold_draft.jsonl",
    )
    parser.add_argument(
        "--max-pages-per-doc",
        type=int,
        default=None,
        help="cap pages OCR'd per document (default: all)",
    )
    args = parser.parse_args()

    if not args.bundle.exists():
        raise SystemExit(
            f"{args.bundle} missing — run scripts/sample_english_complaints.py first."
        )
    written = bootstrap(args.bundle, args.out, args.max_pages_per_doc)
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
