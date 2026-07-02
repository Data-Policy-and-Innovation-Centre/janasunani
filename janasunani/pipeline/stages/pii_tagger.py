from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import torch

from janasunani.pipeline.config import PipelineConfig
from janasunani.pipeline.db import connect


logger = logging.getLogger(__name__)

BATCH_SIZE = 32
MAX_LEN = 512


def run_pii_tagger(config: PipelineConfig) -> None:
    """Add PII-redacted text into `pages.redacted_text`.

    This stage uses the legacy PII tagger code in `models/pii_tagger`, but
    reads and writes the current pipeline SQLite schema:

      pages.extracted_text -> pages.redacted_text

    The model artifacts are expected in `models/pii_tagger/outputs`:
      - pii_crf_model.pt
      - tag2id.json
      - tokenizer/
    """
    output_dir = _find_model_output_dir(config.models_dir / "pii_tagger")
    _ensure_model_artifacts(output_dir)
    _ensure_legacy_import_alias(config.models_dir / "pii_tagger")

    pages_df = _load_pending_pages(config.db_path)
    if pages_df.empty:
        print("pii_tagger: nothing to do")
        return

    print(f"pii_tagger: redacting {len(pages_df)} English page(s)")
    sys.stdout.flush()

    from PII_tagger.src.inference.model_loader import load_pii_model
    from PII_tagger.src.redaction.batch_redactor import redact_dataframe_column_fast

    model, tokenizer, tag2id = load_pii_model(
        output_dir=str(output_dir),
        model_name="xlm-roberta-base",
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device)

    redacted_df = redact_dataframe_column_fast(
        extracted_text_df=pages_df,
        text_col="extracted_text",
        model=model,
        tokenizer=tokenizer,
        tag2id=tag2id,
        batch_size=BATCH_SIZE,
        max_len=MAX_LEN,
        new_col="redacted_text",
    )

    _write_redactions(config.db_path, redacted_df)
    print(f"pii_tagger done: redacted={len(redacted_df)}")


def _find_model_output_dir(model_dir: Path) -> Path:
    candidates = [model_dir / "outputs", model_dir]
    for candidate in candidates:
        if (candidate / "pii_crf_model.pt").exists():
            return candidate
    return model_dir / "outputs"


def _ensure_model_artifacts(output_dir: Path) -> None:
    missing = [
        path
        for path in (
            output_dir / "pii_crf_model.pt",
            output_dir / "tag2id.json",
            output_dir / "tokenizer",
        )
        if not path.exists()
    ]
    if missing:
        missing_list = "\n  - ".join(str(path) for path in missing)
        raise FileNotFoundError(
            "PII tagger model artifacts are missing. Expected:\n"
            f"  - {missing_list}"
        )


def _ensure_legacy_import_alias(model_dir: Path) -> None:
    """Expose `models/pii_tagger` as import name `PII_tagger`.

    The legacy PII code imports `PII_tagger.src...`, while the repository
    stores it under `models/pii_tagger`. This alias keeps the legacy code
    untouched.
    """
    package_parent = model_dir.parent
    if str(package_parent) not in sys.path:
        sys.path.insert(0, str(package_parent))
    import pii_tagger

    sys.modules.setdefault("PII_tagger", pii_tagger)


def _load_pending_pages(db_path: Path) -> pd.DataFrame:
    with connect(db_path) as connection:
        return pd.read_sql_query(
            """
            SELECT page_id, doc_id, page_number, extracted_text
            FROM pages
            WHERE language = 'English'
              AND extracted_text IS NOT NULL
              AND extracted_text != ''
              AND (redacted_text IS NULL OR redacted_text = '')
            ORDER BY doc_id, page_number
            """,
            connection,
        )


def _write_redactions(db_path: Path, redacted_df: pd.DataFrame) -> None:
    now = datetime.now().isoformat()
    rows = [
        (row["redacted_text"], now, row["page_id"])
        for _, row in redacted_df.iterrows()
    ]
    with connect(db_path) as connection:
        connection.executemany(
            """
            UPDATE pages
            SET redacted_text = ?
            WHERE page_id = ?
            """,
            [(redacted_text, page_id) for redacted_text, _now, page_id in rows],
        )
        connection.commit()
