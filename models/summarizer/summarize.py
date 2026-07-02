"""Text summarization pipeline using BART-large-CNN.
 
Reads Class-1 pages (Letter, Form/Application, Text Only) from the `pages`
table written by the page type classifier, assembles them into per-document
text, generates summaries, and writes results to a `summaries` table in the
same database file.
 
Supports resumable processing — already-summarized documents are skipped on
re-runs.
 
Expected input (pages table written by the PII redaction stage)
---------------------------------------------------------------
  Table: pages
    page_id          TEXT  PRIMARY KEY
    doc_id           TEXT
    page_number      INT
    page_type        TEXT  e.g. "Letter", "Form/Application"
    page_type_class  INT   only class 1 pages are summarized
    redacted_text    TEXT  PII-redacted OCR text for this page
 
Output (written into the same .db file)
----------------------------------------
  Table: summaries
    id             INTEGER  PRIMARY KEY AUTOINCREMENT
    doc_id         TEXT
    page_count     INTEGER  number of class-1 pages assembled
    page_types     TEXT     distinct page_type values, comma-separated
    page_ids       TEXT     comma-separated contributing page_ids
    combined_text  TEXT     assembled redacted text that was summarized
    summary        TEXT     generated summary
 
Usage
-----
    python summarizer.py
    python summarizer.py --db pipeline_output.db
    python summarizer.py --db pipeline_output.db --batch-size 8
"""
 
import argparse
import os
import sqlite3
 
import torch
from dotenv import load_dotenv
from transformers import (
    AutoModelForSeq2SeqLM,
    AutoTokenizer,
    PreTrainedModel,
    PreTrainedTokenizerBase,
)
 
load_dotenv()
 
# ── configuration ──────────────────────────────────────────────────────────────
DEFAULT_DB         = "pipeline_output.db"
DEFAULT_BATCH_SIZE = 32
MODEL_NAME         = "facebook/bart-large-cnn"
TARGET_CLASS       = 1   # Letter / Form/Application / Text Only
 
 
# ── db setup ───────────────────────────────────────────────────────────────────
 
def ensure_summaries_table(db: str) -> None:
    """Create the summaries table if it does not already exist."""
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS summaries (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id        TEXT    NOT NULL,
                page_count    INTEGER,
                page_types    TEXT,
                page_ids      TEXT,
                combined_text TEXT    NOT NULL,
                summary       TEXT    NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_summaries_doc_id ON summaries(doc_id)"
        )
        conn.commit()
 
 
def get_processed_doc_ids(db: str) -> set[str]:
    """Return doc_ids already present in the summaries table."""
    with sqlite3.connect(db) as conn:
        try:
            rows = conn.execute("SELECT doc_id FROM summaries").fetchall()
            return {row[0] for row in rows}
        except sqlite3.OperationalError:
            return set()
 
 
# ── data assembly ──────────────────────────────────────────────────────────────
 
def fetch_document_batch(db: str, batch_size: int) -> list[dict]:
    """Fetch the next batch of unprocessed Class-1 documents.
 
    Queries the pages table for all class-1 pages that have redacted text,
    groups them by doc_id in page_number order, concatenates their text, and
    skips any doc_id already in the summaries table.
 
    Pages with NULL or empty redacted_text are excluded — run PII redaction
    before this stage.
 
    Returns a list of dicts with keys:
        doc_id, page_count, page_types, page_ids, combined_text
    """
    already_done = get_processed_doc_ids(db)
 
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
 
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pages'"
        ).fetchone()
        if not row:
            raise ValueError(
                "Table 'pages' not found. "
                "Run the page type classifier stage before the summarizer."
            )
 
        pages = conn.execute(
            """
            SELECT doc_id, page_id, page_number, page_type, redacted_text
            FROM   pages
            WHERE  page_type_class = ?
              AND  redacted_text   IS NOT NULL
              AND  redacted_text   != ''
            ORDER BY doc_id, page_number
            """,
            (TARGET_CLASS,),
        ).fetchall()
 
    # Group pages into per-document records
    docs: dict[str, dict] = {}
    for page in pages:
        doc_id = page["doc_id"]
        if doc_id in already_done:
            continue
        if doc_id not in docs:
            docs[doc_id] = {"page_ids": [], "page_types": [], "texts": []}
        docs[doc_id]["page_ids"].append(page["page_id"])
        docs[doc_id]["page_types"].append(page["page_type"] or "")
        docs[doc_id]["texts"].append(page["redacted_text"])
 
    # Assemble into flat records
    assembled = []
    for doc_id, data in docs.items():
        seen: set[str] = set()
        unique_types = [pt for pt in data["page_types"] if not (pt in seen or seen.add(pt))]
        assembled.append({
            "doc_id":        doc_id,
            "page_count":    len(data["texts"]),
            "page_types":    ", ".join(unique_types),
            "page_ids":      ", ".join(data["page_ids"]),
            "combined_text": "\n\n".join(data["texts"]),
        })
 
    return assembled[:batch_size]
 
 
# ── model ──────────────────────────────────────────────────────────────────────
 
def load_model(device: str) -> tuple[PreTrainedTokenizerBase, PreTrainedModel]:
    """Load BART tokenizer and model onto the target device."""
    hf_token  = os.getenv("HF_TOKEN")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, token=hf_token)
    model     = AutoModelForSeq2SeqLM.from_pretrained(MODEL_NAME, token=hf_token).to(device)
    return tokenizer, model
 
 
def summarize_text(
    text: str,
    tokenizer: PreTrainedTokenizerBase,
    model: PreTrainedModel,
    device: str,
    max_input_length: int = 1024,
    max_summary_length: int = 100,
    min_summary_length: int = 20,
) -> str:
    """Generate a summary for a single document's redacted text."""
    inputs = tokenizer(
        str(text),
        return_tensors="pt",
        truncation=True,
        max_length=max_input_length,
    ).to(device)
 
    summary_ids = model.generate(
        inputs["input_ids"],
        max_length=max_summary_length,
        min_length=min_summary_length,
        forced_bos_token_id=0,
    )
    return tokenizer.decode(summary_ids[0], skip_special_tokens=True)
 
 
# ── process + save ─────────────────────────────────────────────────────────────
 
def process_batch(
    db: str,
    docs: list[dict],
    tokenizer: PreTrainedTokenizerBase,
    model: PreTrainedModel,
    device: str,
    start_index: int,
) -> None:
    """Summarize each document and write results to the summaries table.
 
    Commits after each row so a mid-batch crash does not lose completed work.
    """
    with sqlite3.connect(db) as conn:
        for i, doc in enumerate(docs):
            summary = summarize_text(doc["combined_text"], tokenizer, model, device)
            conn.execute(
                """
                INSERT INTO summaries
                    (doc_id, page_count, page_types, page_ids, combined_text, summary)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    doc["doc_id"],
                    doc["page_count"],
                    doc["page_types"],
                    doc["page_ids"],
                    doc["combined_text"],
                    summary,
                ),
            )
            conn.commit()
            print(
                f"  [{start_index + i + 1}] {doc['doc_id']}"
                f"  |  pages: {doc['page_count']}"
                f"  |  types: {doc['page_types']}"
            )
 
 
# ── entry point ────────────────────────────────────────────────────────────────
 
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize Class-1 pages using BART-large-CNN. "
            "Reads redacted_text from the pages table. "
            "Results are written to a summaries table in the same database file."
        )
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help=f"Pipeline database (default: {DEFAULT_DB}).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Documents to process per run (default: {DEFAULT_BATCH_SIZE}).",
    )
    return parser.parse_args()
 
 
def main() -> None:
    args   = parse_args()
    device = "cuda" if torch.cuda.is_available() else "cpu"
 
    ensure_summaries_table(args.db)
 
    already_processed = len(get_processed_doc_ids(args.db))
    print(f"\nDatabase : {args.db}")
    print(f"Device   : {device}")
    print(f"Already summarized: {already_processed}  |  Fetching next {args.batch_size} ...\n")
 
    docs = fetch_document_batch(args.db, args.batch_size)
 
    if not docs:
        print("No new documents to process.")
        print("Note: pages must have redacted_text populated — run PII redaction first.")
        return
 
    print(f"Loading {MODEL_NAME} ...")
    tokenizer, model = load_model(device)
 
    process_batch(
        db=args.db,
        docs=docs,
        tokenizer=tokenizer,
        model=model,
        device=device,
        start_index=already_processed,
    )
 
    total = already_processed + len(docs)
    print(f"\nDone. Total summarized: {total}  |  Table: summaries  |  DB: {args.db}\n")
 
 
if __name__ == "__main__":
    main()