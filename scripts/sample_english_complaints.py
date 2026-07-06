"""Sample N English complaints (subject AND document) + documents into a zip.

Selects complaints from the Parquet lake where BOTH sides are English:

- the **grievance subject** is written in English — not Odia script and not
  romanized Odia;
- the **document** is English and substantive — judged per page by the
  pipeline's own components: tesseract language dominance (eng vs ori
  confidence, via ``perform_ocr``) and the page-type ViT. ANY Odia-dominant
  page rejects the document; sparse pages (stamps, signatures) are tolerated
  but ≥1 confidently-English page and ≥1 signal-class page (Letter /
  Form/Application / Text Only) are required. Documents that are nothing but
  PII — an Aadhaar or voter ID, a bill — have only noise-class pages and are
  dropped.

Only complaints with ≥1 STANDARD-storage-class document in S3 qualify (parts
of the bucket are GLACIER-archived). The output zip contains:

    documents/<s3-key>...     the qualifying documents (key paths preserved,
                              so nested tickets keep their directory structure
                              for the pipeline's ticket parsing)
    complaints.parquet        the sampled complaints' metadata rows, plus the
                              per-document gate evidence (doc_languages,
                              doc_page_types, doc_english_share)

Needs the lake locally (`dvc pull` / `janasunani-materialize`), the DVC model
mirrors under models/, the `tesseract` binary (+ `ori` traineddata), and AWS
credentials. The document gates use pipeline models, so run in the
pipeline-core env:

    uv run --extra pipeline-core python scripts/sample_english_complaints.py \
        [--n 10] [--seed 7] [--out data/output/english_complaints_sample.zip]

Logging: decisions/progress at INFO; per-page model verdicts and timings at
DEBUG (shown by loguru's default sink); per-candidate subject rejections are
high-volume and sit at TRACE — run with LOGURU_LEVEL=TRACE to see them.
"""

from __future__ import annotations

import os
import sys

# macOS: mixing OpenMP-linked libraries (xgboost, torch) in one process makes
# kernels hang or segfault (see tests/conftest.py). This script now only loads
# torch, but the guard stays as insurance against reintroducing the mix — it
# must be set before any such library loads libomp.
if sys.platform == "darwin":
    os.environ.setdefault("OMP_NUM_THREADS", "1")

import argparse
import random
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import polars as pl
from loguru import logger

from janasunani.config import INTERIM_DATA_DIR, MODELS_DIR, OUTPUT_DATA_DIR
from janasunani.ingestion.s3service import S3Service

# Words that appear in essentially any English sentence but not in romanized
# Odia (which langdetect often can't classify reliably). Requiring a few of
# these is the cheap, explainable guard against "mo ghara pakhare nala..."
# style subjects slipping through as "English".
_ENGLISH_STOPWORDS = frozenset(
    """a an and are as at be been by for from has have he her his i in is it
    my not of on or our please she sir that the their there this to was we
    which will with you your""".split()
)
_MIN_STOPWORD_HITS = 2
_MIN_SUBJECT_CHARS = 30

# Document gates: cap per-document work (letters are 1-3 pages; a 40-page
# annexure doesn't need every page judged to know its character).
_MAX_PAGES_CHECKED = 5
# A page needs this many confident tesseract words (in either language) to be
# judged at all; below it the page is "Sparse" (stamps, signatures, photos)
# and is ignored rather than counted for or against.
_MIN_CONFIDENT_WORDS = 10

_ODIA_RANGE = re.compile(r"[଀-୿]")
_WORD = re.compile(r"[a-z']+")


def is_english(text: str) -> bool:
    """True when ``text`` reads as English prose.

    Three gates: no Odia codepoints, langdetect says 'en', and the text
    contains common English function words (romanized Odia has none even
    when langdetect guesses 'en').
    """
    if len(text.strip()) < _MIN_SUBJECT_CHARS:
        return False
    if _ODIA_RANGE.search(text):
        return False
    words = _WORD.findall(text.lower())
    if sum(1 for w in words if w in _ENGLISH_STOPWORDS) < _MIN_STOPWORD_HITS:
        return False

    from langdetect import DetectorFactory, LangDetectException, detect

    DetectorFactory.seed = 0  # langdetect is nondeterministic by default
    try:
        return detect(text) == "en"
    except LangDetectException:
        return False


@dataclass(frozen=True)
class DocVerdict:
    ok: bool
    reason: str
    languages: tuple[str, ...]
    page_types: tuple[str, ...]

    @property
    def english_share(self) -> float:
        if not self.languages:
            return 0.0
        return sum(1 for lang in self.languages if lang == "English") / len(
            self.languages
        )


def assess_document(languages: list[str], page_types: list[str]) -> DocVerdict:
    """Pure verdict logic over per-page judgements (kept import-light so the
    tests can exercise it without models).

    ``languages`` values are "English" / "Odia" / "Sparse" (too little
    confident text to judge). The rule is strict on purpose — an earlier,
    share-based version shipped bundles with Odia-dominant pages inside:
    ANY Odia-dominant page rejects the document; sparse pages are tolerated
    but at least one confidently-English page is required.
    """
    from janasunani.pipeline.stages.page_type_classifier import (
        PAGE_TYPE_CLASS_BY_LABEL,
    )

    langs = tuple(languages)
    types = tuple(page_types)
    if not langs:
        return DocVerdict(False, "no readable pages", langs, types)
    n_odia = sum(1 for lang in langs if lang == "Odia")
    if n_odia:
        return DocVerdict(
            False, f"{n_odia} Odia-dominant page(s) of {len(langs)}", langs, types
        )
    if "English" not in langs:
        return DocVerdict(False, "no confidently-English page", langs, types)
    if not any(PAGE_TYPE_CLASS_BY_LABEL.get(t) == 1 for t in types):
        return DocVerdict(
            False, f"no substantive page — only {sorted(set(types))}", langs, types
        )
    return DocVerdict(True, "ok", langs, types)


def classify_page_language(image) -> str:
    """"English" / "Odia" / "Sparse" for one page, from the pipeline's raw
    tesseract signal (``perform_ocr``: eng + ori passes, confidence-weighted).

    Deliberately NOT the format classifier's language label — that model
    (~76% accuracy) let Odia-dominant pages through as "English"; the direct
    confidence comparison is the harder signal for script dominance.
    """
    from janasunani.pipeline.stages.format_classifier.features import perform_ocr

    ocr = perform_ocr(image)
    if max(ocr["word_count_eng"], ocr["word_count_ori"]) < _MIN_CONFIDENT_WORDS:
        return "Sparse"
    return "Odia" if ocr["predominant_lang"] == "ori" else "English"


class DocumentGates:
    """Judge a downloaded document with the pipeline's own components:
    tesseract language dominance per page + the page-type ViT."""

    def __init__(self, models_dir: Path = MODELS_DIR) -> None:
        import time

        from janasunani.pipeline.stages.page_type_classifier import (
            _PageTypeClassifier,
        )

        t0 = time.time()
        vit_local = models_dir / "page_type_classifier" / "vit_type_classifier"
        model_id = (
            str(vit_local)
            if (vit_local / "config.json").exists()
            else "DPIC-Pipeline/vit_type_classifier"
        )
        self._page_type = _PageTypeClassifier(model_id)
        logger.info(
            f"document gates ready: page-type ViT {model_id} "
            f"({time.time() - t0:.1f}s); language via tesseract eng+ori"
        )

    def assess(self, doc_path: Path) -> DocVerdict:
        import time

        from pdf2image.exceptions import PDFPageCountError, PDFSyntaxError

        t0 = time.time()
        languages: list[str] = []
        page_types: list[str] = []
        try:
            for page_number, image in enumerate(
                _page_images(doc_path, _MAX_PAGES_CHECKED), start=1
            ):
                t_page = time.time()
                language = classify_page_language(image)
                page_type = self._page_type.predict(image)
                logger.debug(
                    f"{doc_path.name} p{page_number}: language={language!r} "
                    f"page_type={page_type!r} ({time.time() - t_page:.1f}s)"
                )
                languages.append(language)
                page_types.append(page_type)
        except (OSError, ValueError, PDFPageCountError, PDFSyntaxError) as exc:
            # The bucket contains corrupt uploads (bad bytes behind a
            # .jpeg/.pdf name): PIL raises UnidentifiedImageError/OSError,
            # pdf2image raises PDFPageCountError/PDFSyntaxError (plain
            # Exception subclasses). An unreadable file is a reject, not a
            # crash — a --n 50 run died at 19/50 on exactly this before the
            # guard existed.
            logger.warning(
                f"{doc_path.name}: unreadable document "
                f"({type(exc).__name__}: {exc}) — rejecting"
            )
            return DocVerdict(
                False,
                f"unreadable document ({type(exc).__name__})",
                tuple(languages),
                tuple(page_types),
            )
        verdict = assess_document(languages, page_types)
        logger.debug(
            f"{doc_path.name}: verdict={'ok' if verdict.ok else verdict.reason!r} "
            f"english_share={verdict.english_share:.2f} "
            f"pages_judged={len(languages)} ({time.time() - t0:.1f}s)"
        )
        return verdict


def _page_images(doc_path: Path, max_pages: int):
    """First ``max_pages`` pages of a PDF/image as PIL images."""
    if doc_path.suffix.lower() == ".pdf":
        from pdf2image import pdfinfo_from_path

        from janasunani.pipeline.stages.page_type_classifier import _render_pdf_page

        n_pages = int(pdfinfo_from_path(str(doc_path))["Pages"])
        for page_number in range(1, min(n_pages, max_pages) + 1):
            yield _render_pdf_page(doc_path, page_number)
    else:
        from PIL import Image

        with Image.open(doc_path) as image:
            yield image.convert("RGB")


def _standard_class_documents(s3: S3Service, ticket_no: str) -> list[str]:
    """S3 keys of the ticket's documents that are directly downloadable."""
    objects = s3.list_objects(prefix=f"{ticket_no}_complaint_")
    return [
        obj["Key"]
        for obj in objects
        if obj.get("StorageClass", "STANDARD") == "STANDARD"
    ]


def sample_complaints(
    n: int, seed: int, workdir: Path
) -> tuple[pl.DataFrame, dict[str, list[Path]]]:
    """Pick ``n`` complaints passing every gate; download their documents.

    Returns (metadata rows + gate evidence, {ticket_no: [downloaded paths]}).
    Candidates are shuffled deterministically and checked lazily,
    cheapest gate first, so the heavy model work only runs on plausible rows.
    """
    lake_path = INTERIM_DATA_DIR / "complaints.parquet"
    if not lake_path.exists():
        raise SystemExit(
            f"{lake_path} missing — run `dvc pull` or `janasunani-materialize` first."
        )
    complaints = pl.read_parquet(lake_path).filter(
        pl.col("grievance").is_not_null()
        & (pl.col("grievance").str.len_chars() >= _MIN_SUBJECT_CHARS)
        & pl.col("document_url").is_not_null()
    )
    logger.info(
        f"{complaints.height} complaints with a subject and a document URL "
        f"(lake: {lake_path}, seed={seed}, target n={n})"
    )

    order = list(range(complaints.height))
    random.Random(seed).shuffle(order)

    s3 = S3Service()
    gates = DocumentGates()
    picked_rows: list[int] = []
    evidence: list[dict[str, object]] = []
    picked_paths: dict[str, list[Path]] = {}
    checked = 0
    subject_rejects = 0
    no_standard_doc = 0
    docs_dropped = 0
    for idx in order:
        row = complaints.row(idx, named=True)
        ticket = row["ticket_no"]
        checked += 1
        if checked % 100 == 0:
            logger.info(
                f"progress: checked={checked} picked={len(picked_rows)}/{n} "
                f"(subject rejects={subject_rejects}, docs dropped={docs_dropped})"
            )
        if not is_english(row["grievance"]):
            # High-volume: visible with LOGURU_LEVEL=TRACE.
            logger.trace(f"{ticket}: subject failed the English gate")
            subject_rejects += 1
            continue
        keys = _standard_class_documents(s3, ticket)
        if not keys:
            logger.info(f"{ticket}: no STANDARD-class document, skipping")
            no_standard_doc += 1
            continue
        logger.debug(f"{ticket}: {len(keys)} STANDARD-class document(s): {keys}")

        kept: list[Path] = []
        verdicts: list[DocVerdict] = []
        for key in keys:
            local = workdir / key
            local.parent.mkdir(parents=True, exist_ok=True)
            if not s3.download_file(key, str(local)):
                logger.warning(f"{ticket}: download failed for {key}, skipping key")
                continue

            logger.info(f"Assessing documents for language and PII for {ticket}")
            verdict = gates.assess(local)
            if verdict.ok:
                kept.append(local)
                verdicts.append(verdict)
            else:
                logger.info(f"{ticket}: dropped {key} — {verdict.reason}")
                docs_dropped += 1
                local.unlink()
        if not kept:
            continue

        picked_rows.append(idx)
        picked_paths[ticket] = kept
        evidence.append(
            {
                "ticket_no": ticket,
                "doc_languages": "; ".join(
                    ", ".join(v.languages) for v in verdicts
                ),
                "doc_page_types": "; ".join(
                    ", ".join(v.page_types) for v in verdicts
                ),
                "doc_english_share": min(v.english_share for v in verdicts),
            }
        )
        logger.info(
            f"picked {ticket} ({len(kept)} document(s), "
            f"english_share={min(v.english_share for v in verdicts):.2f}, "
            f"page_types={sorted({t for v in verdicts for t in v.page_types})})"
        )
        if len(picked_rows) == n:
            break

    if len(picked_rows) < n:
        raise SystemExit(
            f"only found {len(picked_rows)}/{n} qualifying complaints "
            f"after checking {checked} candidates "
            f"(subject rejects={subject_rejects}, docs dropped={docs_dropped})"
        )
    logger.info(
        f"selected {n} complaints after checking {checked} candidates: "
        f"subject rejects={subject_rejects}, "
        f"no-STANDARD-doc skips={no_standard_doc}, "
        f"documents dropped by gates={docs_dropped}"
    )
    metadata = complaints[picked_rows].join(
        pl.DataFrame(evidence), on="ticket_no", how="left"
    )
    return metadata, picked_paths


def build_zip(
    out_path: Path,
    metadata: pl.DataFrame,
    paths: dict[str, list[Path]],
    workdir: Path,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    parquet_path = workdir / "complaints.parquet"
    metadata.write_parquet(parquet_path)
    n_docs = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(parquet_path, "complaints.parquet")
        for _, ticket_paths in sorted(paths.items()):
            for local in ticket_paths:
                zf.write(local, f"documents/{local.relative_to(workdir)}")
                n_docs += 1
    logger.success(
        f"wrote {out_path} ({out_path.stat().st_size / 1e6:.1f} MB): "
        f"{metadata.height} complaints, {n_docs} document(s)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=10, help="complaints to sample")
    parser.add_argument("--seed", type=int, default=7, help="sampling seed")
    parser.add_argument(
        "--out",
        type=Path,
        default=OUTPUT_DATA_DIR / "english_complaints_sample.zip",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        metadata, paths = sample_complaints(n=args.n, seed=args.seed, workdir=workdir)
        build_zip(args.out, metadata, paths, workdir=workdir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
