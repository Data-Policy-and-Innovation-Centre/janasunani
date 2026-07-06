"""Sample N English-subject complaints and pack their documents into a zip.

Selects complaints from the Parquet lake whose grievance subject is written in
English — not Odia script and not romanized Odia — and which have at least one
STANDARD-storage-class document in the S3 documents bucket (parts of the bucket
are GLACIER-archived and can't be downloaded directly). Downloads those
documents and writes one zip containing:

    documents/<s3-key>...     the complaint documents (key paths preserved,
                              so nested tickets keep their directory structure
                              for the pipeline's ticket parsing)
    complaints.parquet        the sampled complaints' full metadata rows

Needs the lake locally (`dvc pull` / `janasunani-materialize`) and AWS
credentials (default chain). langdetect is not a base dependency — run with:

    uv run --with langdetect python scripts/sample_english_complaints.py \
        [--n 10] [--seed 7] [--out data/output/english_complaints_sample.zip]
"""

from __future__ import annotations

import argparse
import random
import re
import tempfile
import zipfile
from pathlib import Path

import polars as pl
from loguru import logger

from janasunani.config import INTERIM_DATA_DIR, OUTPUT_DATA_DIR
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


def _standard_class_documents(s3: S3Service, ticket_no: str) -> list[str]:
    """S3 keys of the ticket's documents that are directly downloadable."""
    objects = s3.list_objects(prefix=f"{ticket_no}_complaint_")
    return [
        obj["Key"]
        for obj in objects
        if obj.get("StorageClass", "STANDARD") == "STANDARD"
    ]


def sample_complaints(n: int, seed: int) -> tuple[pl.DataFrame, dict[str, list[str]]]:
    """Pick ``n`` English-subject complaints with downloadable documents.

    Returns (metadata rows, {ticket_no: [s3 keys]}). Candidates are shuffled
    deterministically and language-checked lazily, so the expensive work only
    runs on rows actually considered.
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
    picked_rows: list[int] = []
    picked_keys: dict[str, list[str]] = {}
    checked = 0
    for idx in order:
        row = complaints.row(idx, named=True)
        checked += 1
        if not is_english(row["grievance"]):
            continue
        keys = _standard_class_documents(s3, row["ticket_no"])
        if not keys:
            logger.info(f"{row['ticket_no']}: no STANDARD-class document, skipping")
            continue
        picked_rows.append(idx)
        picked_keys[row["ticket_no"]] = keys
        logger.info(f"picked {row['ticket_no']} ({len(keys)} document(s))")
        if len(picked_rows) == n:
            break

    if len(picked_rows) < n:
        raise SystemExit(
            f"only found {len(picked_rows)}/{n} qualifying complaints "
            f"after checking {checked} candidates"
        )
    logger.info(f"selected {n} complaints after checking {checked} candidates")
    return complaints[picked_rows], picked_keys


def build_zip(out_path: Path, metadata: pl.DataFrame, keys: dict[str, list[str]]) -> None:
    s3 = S3Service()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        parquet_path = tmp_dir / "complaints.parquet"
        metadata.write_parquet(parquet_path)

        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(parquet_path, "complaints.parquet")
            for ticket_no, ticket_keys in sorted(keys.items()):
                for key in ticket_keys:
                    local = tmp_dir / "doc"
                    if not s3.download_file(key, str(local)):
                        raise SystemExit(f"download failed for s3 key {key}")
                    zf.write(local, f"documents/{key}")
                    local.unlink()
    logger.success(
        f"wrote {out_path} ({out_path.stat().st_size / 1e6:.1f} MB): "
        f"{metadata.height} complaints, {sum(len(v) for v in keys.values())} document(s)"
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

    metadata, keys = sample_complaints(n=args.n, seed=args.seed)
    build_zip(args.out, metadata, keys)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
