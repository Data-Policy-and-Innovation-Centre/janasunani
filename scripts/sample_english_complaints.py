"""Sample N English complaints (subject AND document) + documents into a zip.

Selects complaints from the Parquet lake where BOTH sides are English:

- the **grievance subject** is written in English — not Odia script and not
  romanized Odia;
- the **document** is largely English and substantive — judged by the
  pipeline's own models: the format classifier's language prediction (majority
  of pages exactly "English") and the page-type ViT (at least one
  signal-class page: Letter / Form/Application / Text Only). Documents that
  are nothing but PII — an Aadhaar or voter ID, a bill — have only
  noise-class pages and are dropped.

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
"""

from __future__ import annotations

import os
import sys

# macOS: xgboost (format classifier) and torch (page-type ViT) each load their
# own OpenMP runtime; interleaving them makes the first ViT conv2d after an
# xgboost predict spin forever (same family as the segfault noted in
# tests/conftest.py). One OMP thread sidesteps it — must be set before either
# library loads libomp.
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
_MIN_ENGLISH_PAGE_SHARE = 0.5

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
    """Pure verdict logic over per-page predictions (kept import-light so the
    tests can exercise it without models)."""
    from janasunani.pipeline.stages.page_type_classifier import (
        PAGE_TYPE_CLASS_BY_LABEL,
    )

    langs = tuple(languages)
    types = tuple(page_types)
    if not langs:
        return DocVerdict(False, "no readable pages", langs, types)
    english_share = sum(1 for lang in langs if lang == "English") / len(langs)
    if english_share < _MIN_ENGLISH_PAGE_SHARE:
        return DocVerdict(
            False, f"not largely English (share {english_share:.2f})", langs, types
        )
    if not any(PAGE_TYPE_CLASS_BY_LABEL.get(t) == 1 for t in types):
        return DocVerdict(
            False, f"no substantive page — only {sorted(set(types))}", langs, types
        )
    return DocVerdict(True, "ok", langs, types)


class DocumentGates:
    """Judge a downloaded document with the pipeline's own models.

    Loads the format classifier (language) and the page-type ViT once; the
    XGBoost pickle loads before torch, matching the pipeline's stage order
    (relevant on macOS — see tests/conftest.py's OMP note).
    """

    def __init__(self, models_dir: Path = MODELS_DIR) -> None:
        from janasunani.pipeline.stages.format_classifier.model import FormatClassifier
        from janasunani.pipeline.stages.page_type_classifier import (
            _PageTypeClassifier,
        )

        self._format = FormatClassifier(
            models_dir / "format_classifier" / "page_split_v3.0_doc_split.pkl"
        )
        vit_local = models_dir / "page_type_classifier" / "vit_type_classifier"
        model_id = (
            str(vit_local)
            if (vit_local / "config.json").exists()
            else "DPIC-Pipeline/vit_type_classifier"
        )
        self._page_type = _PageTypeClassifier(model_id)

    def assess(self, doc_path: Path) -> DocVerdict:
        import numpy as np

        languages: list[str] = []
        page_types: list[str] = []
        for image in _page_images(doc_path, _MAX_PAGES_CHECKED):
            bgr = np.array(image.convert("RGB"))[:, :, ::-1]
            prediction = self._format.predict(bgr)
            if prediction is None or not prediction.get("language"):
                continue
            languages.append(prediction["language"])
            page_types.append(self._page_type.predict(image))
        return assess_document(languages, page_types)


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
    logger.info(f"{complaints.height} complaints with a subject and a document URL")

    order = list(range(complaints.height))
    random.Random(seed).shuffle(order)

    s3 = S3Service()
    gates = DocumentGates()
    picked_rows: list[int] = []
    evidence: list[dict[str, object]] = []
    picked_paths: dict[str, list[Path]] = {}
    checked = 0
    for idx in order:
        row = complaints.row(idx, named=True)
        ticket = row["ticket_no"]
        checked += 1
        if not is_english(row["grievance"]):
            continue
        keys = _standard_class_documents(s3, ticket)
        if not keys:
            logger.info(f"{ticket}: no STANDARD-class document, skipping")
            continue

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
        logger.info(f"picked {ticket} ({len(kept)} document(s))")
        if len(picked_rows) == n:
            break

    if len(picked_rows) < n:
        raise SystemExit(
            f"only found {len(picked_rows)}/{n} qualifying complaints "
            f"after checking {checked} candidates"
        )
    logger.info(f"selected {n} complaints after checking {checked} candidates")
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
