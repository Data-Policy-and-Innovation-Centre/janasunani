"""SQLite schema and connection helpers for the document pipeline.

The schema uses lowercase column names to match the pipeline row keys.
"""

from pathlib import Path
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    doc_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    full_path TEXT NOT NULL,
    page_id TEXT PRIMARY KEY,
    page_path TEXT,
    scanned TEXT,
    handwritten TEXT,
    printed TEXT,
    image_quality TEXT,
    color_scale TEXT,
    language TEXT,
    model TEXT,
    date_classified TEXT,
    data_access_date TEXT,
    extracted_text TEXT,
    ocr_model TEXT,
    extracted_date TEXT,
    redacted_text TEXT,
    ticket_number TEXT,
    page_type TEXT,
    page_type_class INTEGER,
    UNIQUE(doc_id, page_number)
);

CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    ticket_number TEXT,
    grievance TEXT,
    summary TEXT,
    grievance_category TEXT
);

CREATE TABLE IF NOT EXISTS unreadable_pages (
    doc_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    full_path TEXT NOT NULL,
    stage TEXT NOT NULL,
    reason TEXT,
    detected_at TEXT,
    UNIQUE(doc_id, page_number, stage)
);

CREATE INDEX IF NOT EXISTS idx_pages_doc_id ON pages(doc_id);
CREATE INDEX IF NOT EXISTS idx_pages_ticket_number ON pages(ticket_number);
CREATE INDEX IF NOT EXISTS idx_pages_page_type ON pages(page_type);
CREATE INDEX IF NOT EXISTS idx_pages_page_type_class ON pages(page_type_class);
CREATE INDEX IF NOT EXISTS idx_documents_ticket_number ON documents(ticket_number);
CREATE INDEX IF NOT EXISTS idx_documents_grievance_category ON documents(grievance_category);
CREATE INDEX IF NOT EXISTS idx_unreadable_doc_id ON unreadable_pages(doc_id);
CREATE INDEX IF NOT EXISTS idx_unreadable_stage ON unreadable_pages(stage);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection to the pipeline DB, creating the parent dir if needed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    # Reasonable defaults for a pipeline that reads and writes a lot
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=60000")
    return connection


def initialize_database(db_path: Path) -> None:
    """Create tables and indexes if they don't already exist.

    Safe to run repeatedly: CREATE ... IF NOT EXISTS leaves existing data
    untouched. Note that this does NOT migrate schemas — if columns are
    added later, existing databases need manual migration.
    """
    with connect(db_path) as connection:
        connection.executescript(SCHEMA)
