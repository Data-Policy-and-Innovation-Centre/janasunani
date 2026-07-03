#!/usr/bin/env python3
"""Redact PII from English documents in the pages table.
 
Reads from the `pages` table written by the OCR extraction stage,
redacts PII from English pages, and writes results back to the same
table in `redacted_text` and `redaction_date` columns.
 
This stage sits between OCR extraction and the page type classifier:
  OCR extraction  →  PII redaction (this script)  →  page type classifier
 
The page type classifier is image-based and does not consume text, so
this stage only needs to write cleanly to `pages` without touching
`page_path`, `full_path`, or `page_type`.
"""
 
import logging
import sqlite3
import traceback
from datetime import datetime
from pathlib import Path
 
import pandas as pd
import torch
 
from PII_tagger.src.inference.model_loader import load_pii_model
from PII_tagger.src.redaction.batch_redactor import redact_dataframe_column_fast
 
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
 
 
def _ensure_redaction_columns(cursor: sqlite3.Cursor) -> None:
    """Add redacted_text and redaction_date columns to pages if they don't exist.
 
    Safe to call on every run — uses ALTER TABLE only when the column is absent.
    """
    existing = {row[1] for row in cursor.execute("PRAGMA table_info(pages)")}
    if "redacted_text" not in existing:
        cursor.execute("ALTER TABLE pages ADD COLUMN redacted_text TEXT")
        logger.info("  Added column: pages.redacted_text")
    if "redaction_date" not in existing:
        cursor.execute("ALTER TABLE pages ADD COLUMN redaction_date TEXT")
        logger.info("  Added column: pages.redaction_date")
 
 
def main() -> None:
    """Run batch PII redaction on English pages."""
    ROOT_DIR = Path(__file__).resolve().parent.parent
 
    output_dir = ROOT_DIR / "PII_tagger" / "outputs"
    db_path    = ROOT_DIR / "data" / "classified_master.db"
 
    logger.info("=" * 80)
    logger.info("PII Batch Redaction - English Pages")
    logger.info("=" * 80)
    logger.info("")
 
    logger.info("STEP 1: Preparing database...")
    logger.info(f"Connecting to: {db_path}")
 
    conn   = sqlite3.connect(db_path)
    cursor = conn.cursor()
 
    # Ensure the columns this stage writes to actually exist in `pages`
    _ensure_redaction_columns(cursor)
    conn.commit()
 
    # ── Query unredacted English pages from the `pages` table ──────────────
    # Column mapping from pages table → names the rest of this script uses:
    #   page_id        → unique_id
    #   doc_id         → document
    #   ocr_model      → extraction_model
    #   extracted_date → extraction_date
    #   language       → Language  (stored lowercase in pages)
    logger.info("STEP 2: Querying pages table for English pages needing redaction...")
 
    query = """
        SELECT
            page_id        AS unique_id,
            doc_id         AS document,
            page_number,
            extracted_text,
            ocr_model      AS extraction_model,
            extracted_date AS extraction_date
        FROM pages
        WHERE language = 'English'
          AND extracted_text IS NOT NULL
          AND extracted_text != ''
          AND (redacted_text IS NULL OR redacted_text = '')
        ORDER BY doc_id, page_number
    """
 
    pages_df = pd.read_sql_query(query, conn)
    logger.info(f"✓ Found {len(pages_df)} English pages needing redaction")
 
    if len(pages_df) == 0:
        logger.info("✓ No English pages need redaction - all done!")
        conn.close()
        return
 
    logger.info("")
    logger.info("Sample of pages to be redacted:")
    logger.info(pages_df[["document", "page_number", "extraction_model"]].head(10).to_string())
    logger.info("")
 
    # ── Load model ──────────────────────────────────────────────────────────
    logger.info("STEP 3: Loading PII redaction model...")
    logger.info(f"Model directory: {output_dir}")
 
    try:
        model, tokenizer, tag2id = load_pii_model(
            output_dir=str(output_dir), model_name="xlm-roberta-base"
        )
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model  = model.to(device)
        logger.info(f"✓ Model loaded on device: {device}")
        logger.info("")
    except Exception as e:
        logger.error(f"ERROR loading model: {e}")
        logger.error(traceback.format_exc())
        conn.close()
        return
 
    # ── Run redaction ───────────────────────────────────────────────────────
    logger.info("STEP 4: Running PII redaction...")
    logger.info("  Batch size   : 32")
    logger.info("  Max token len: 512")
    logger.info("")
 
    try:
        redacted_df = redact_dataframe_column_fast(
            extracted_text_df=pages_df,
            text_col="extracted_text",
            model=model,
            tokenizer=tokenizer,
            tag2id=tag2id,
            batch_size=32,
            wrap_width=95,
            max_len=512,
            new_col="redacted_text",
        )
        logger.info(f"✓ Redaction complete for {len(redacted_df)} pages")
        logger.info("")
    except Exception as e:
        logger.error(f"ERROR during redaction: {e}")
        logger.error(traceback.format_exc())
        conn.close()
        return
 
    # ── Write results back to pages table ───────────────────────────────────
    logger.info("STEP 5: Writing redacted text back to pages table...")
 
    current_timestamp = datetime.now().isoformat()
    updated_count     = 0
    error_count       = 0
 
    for _idx, row in redacted_df.iterrows():
        try:
            cursor.execute(
                """
                UPDATE pages
                SET    redacted_text  = ?,
                       redaction_date = ?
                WHERE  page_id        = ?
                """,
                (row["redacted_text"], current_timestamp, row["unique_id"]),
            )
            updated_count += 1
 
            if updated_count % 100 == 0:
                conn.commit()
                logger.info(f"  Updated {updated_count}/{len(redacted_df)} pages...")
 
        except Exception as e:
            error_count += 1
            logger.error(f"  Error updating page_id={row['unique_id']}: {e}")
            continue
 
    conn.commit()
 
    logger.info(f"✓ Successfully updated {updated_count} pages")
    if error_count > 0:
        logger.warning(f"⚠ Encountered {error_count} errors during writes")
    logger.info("")
 
    # ── Verify ──────────────────────────────────────────────────────────────
    logger.info("STEP 6: Verifying results...")
 
    cursor.execute("""
        SELECT COUNT(*)
        FROM   pages
        WHERE  language      = 'English'
          AND  redacted_text IS NOT NULL
          AND  redacted_text != ''
    """)
    total_redacted = cursor.fetchone()[0]
    logger.info(f"✓ Total English pages with redacted_text: {total_redacted}")
 
    sample_df = pd.read_sql_query(
        """
        SELECT
            doc_id         AS document,
            page_number,
            ocr_model      AS extraction_model,
            redaction_date,
            substr(extracted_text, 1, 40) AS text_sample,
            substr(redacted_text,  1, 40) AS redacted_sample
        FROM pages
        WHERE language      = 'English'
          AND redacted_text IS NOT NULL
        ORDER BY redaction_date DESC
        LIMIT 5
        """,
        conn,
    )
    logger.info("")
    logger.info("Sample of redacted pages (most recent):")
    logger.info(sample_df.to_string())
    logger.info("")
 
    conn.close()
 
    logger.info("=" * 80)
    logger.info(" REDACTION COMPLETE ")
    logger.info("=" * 80)
    logger.info(f"Processed : {updated_count} English pages")
    logger.info(f"Errors    : {error_count}")
    logger.info(f"Total in DB: {total_redacted} English pages with redacted_text")
    logger.info(f"Timestamp : {current_timestamp}")
    logger.info("")
    logger.info(
        "Query to view: "
        "SELECT * FROM pages WHERE language = 'English' AND redacted_text IS NOT NULL"
    )
    logger.info("=" * 80)
 
 
if __name__ == "__main__":
    main()