"""Ingest grievances from the complaints JSON into the `documents` table.

This is independent of every model stage. It reads the complaints JSON
(the source data that has ticket_no + grievance text), and writes one
`documents` row per document that the pipeline has already processed
(i.e. per distinct doc_id/ticket in the `pages` table), attaching the
grievance text looked up by ticket number.

Documents are the driver: we iterate over what the pipeline has actually
seen (distinct ticket numbers in `pages`) and attach a grievance if the
JSON has one for that ticket. A document with no matching grievance still
gets a `documents` row (with NULL grievance) so it's visible; it just
won't be categorizable until a grievance appears.

Includes a join-health report so mismatches between path-parsed tickets
and JSON ticket_no values are visible immediately rather than silently
producing empty categories.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ...db import connect
from loguru import logger


def _load_grievances_from_json(json_path: Path) -> dict[str, str]:
    """Read the complaints JSON (pandas column-oriented), return {ticket_no: grievance}.

    The file was written by pandas (df.to_json), so it's column-oriented:
        {"ticket_no": {"0": "...", "1": "..."}, "grievance": {"0": "...", ...}, ...}
    pandas reads its own format back reliably, so we use it rather than
    parsing the orientation by hand.
    """
    import pandas as pd

    df = pd.read_json(json_path)

    if "ticket_no" not in df.columns or "grievance" not in df.columns:
        raise ValueError(
            f"complaints JSON missing required columns. Found: {list(df.columns)}. "
            "Need 'ticket_no' and 'grievance'."
        )

    grievances: dict[str, str] = {}
    skipped = 0
    for ticket, grievance in zip(df["ticket_no"], df["grievance"]):
        if pd.isna(ticket) or str(ticket).strip() == "":
            skipped += 1
            continue
        # Keep the ticket exactly as stored (slashes and all). Last write wins.
        grievances[str(ticket)] = "" if pd.isna(grievance) else str(grievance)

    if skipped:
        logger.error(f"  ingestion: skipped {skipped} JSON records with no ticket_no")
    return grievances

def _distinct_docs_from_pages(db_path: Path) -> list[dict[str, Any]]:
    """Return distinct (doc_id, ticket_number) pairs seen in pages.

    These are the documents the pipeline has actually processed.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=60.0)
    try:
        rows = conn.execute(
            "SELECT DISTINCT doc_id, ticket_number FROM pages"
        ).fetchall()
    finally:
        conn.close()
    return [{"doc_id": r[0], "ticket_number": r[1]} for r in rows]


def ingest_grievances(db_path: Path, complaints_json: Path) -> dict[str, int]:
    """Load grievances from JSON into the documents table.

    For each distinct (doc_id, ticket_number) in pages, write a documents
    row carrying the ticket and the grievance text (looked up by ticket).
    Existing documents rows are updated (grievance/ticket filled in) without
    disturbing other columns (summary, grievance_category) a later stage may
    have set.

    Returns a stats dict and prints a join-health report.
    """
    if not complaints_json.exists():
        raise FileNotFoundError(f"complaints JSON not found: {complaints_json}")
    if not db_path.exists():
        raise FileNotFoundError(
            f"database not found: {db_path}. Run init-db and the format "
            "classifier first so the pages table exists."
        )

    grievances = _load_grievances_from_json(complaints_json)
    docs = _distinct_docs_from_pages(db_path)

    logger.info(f"grievance ingestion: {len(grievances)} grievances in JSON, "
          f"{len(docs)} distinct documents in pages")

    matched = 0
    unmatched_docs: list[str] = []
    no_ticket = 0

    conn = connect(db_path)
    rows_to_write: list[dict[str, Any]] = []
    for d in docs:
        doc_id = d["doc_id"]
        ticket = d["ticket_number"]
        if not ticket:
            no_ticket += 1
            # Still create a row so the doc is visible, but no grievance.
            rows_to_write.append(
                {"doc_id": doc_id, "ticket_number": None, "grievance": None}
            )
            continue
        grievance = grievances.get(ticket)
        if grievance is not None:
            matched += 1
        else:
            unmatched_docs.append(ticket)
        rows_to_write.append(
            {"doc_id": doc_id, "ticket_number": ticket, "grievance": grievance}
        )

    # UPSERT: insert new documents rows, or fill ticket/grievance on existing
    # ones without clobbering summary / grievance_category.
    try:
        conn.executemany(
            """INSERT INTO documents (doc_id, ticket_number, grievance)
               VALUES (:doc_id, :ticket_number, :grievance)
               ON CONFLICT(doc_id) DO UPDATE SET
                   ticket_number = excluded.ticket_number,
                   grievance = excluded.grievance""",
            rows_to_write,
        )
        conn.commit()
    finally:
        conn.close()

    # ---- join-health report ----
    logger.info(f"{'='*60}")
    logger.info("grievance ingestion — join health")
    logger.info(f"  documents written:        {len(rows_to_write)}")
    logger.info(f"  matched to a grievance:   {matched}")
    logger.info(f"  no ticket_number on page: {no_ticket}")
    logger.info(f"  ticket present but NOT in JSON: {len(unmatched_docs)}")
    if unmatched_docs:
        sample = unmatched_docs[:10]
        logger.info("  sample unmatched tickets (parsed from path, absent in JSON):")
        for t in sample:
            logger.info(f"    {t!r}")
        logger.info(
            "  ^ if these look like real tickets that SHOULD be in the JSON, "
            "the path structure or --input root may be wrong (tickets "
            "mis-parsed), or this JSON is only a sample of all grievances."
        )
    logger.info(f"{'='*60}")

    return {
        "documents_written": len(rows_to_write),
        "matched": matched,
        "no_ticket": no_ticket,
        "unmatched": len(unmatched_docs),
    }
