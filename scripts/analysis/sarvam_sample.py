"""Run a paired Sarvam Vision vs pytesseract sample (#84, #127).

Renders each page of every document once, sends the same rendered page to both
engines, and scores the pair with ``janasunani.evaluation.sarvam_scorecard``.

Two rules this script exists to enforce:

* **Same pixels to both engines.** Re-rendering, or letting Sarvam read the
  source PDF while pytesseract reads a raster, would measure the renderer as
  much as the OCR. Both read exactly one ``render_page`` output.
* **No page text is ever written to disk.** The inputs are real grievance
  documents. Only aggregate metrics leave this process; the transcripts stay
  in memory and are dropped when it exits. ``--dump-text`` exists for
  debugging a single page and refuses to run over more than one.

Live Sarvam calls cost money (Rs 0.5/page) and send citizen PII to an external
provider under the recorded acceptance on the route. ``--dry-run`` renders and
runs pytesseract only, so the sample and the cost can be checked first.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from loguru import logger

from janasunani.egress.sarvam import (
    PROVIDER_REGISTRY,
    SarvamAuditContext,
    SarvamVisionAdapter,
    SqliteAuditLog,
)
from janasunani.evaluation.sarvam_scorecard import (
    PageRecord,
    build_scorecard,
    normalize_text,
)
from janasunani.pipeline.stages.ocr_extraction.page_renderer import render_page

PRICE_PER_PAGE_RUPEES = 0.5
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif"}


def _page_count(path: Path) -> int:
    if path.suffix.lower() == ".pdf":
        from pdf2image import pdfinfo_from_path

        return int(pdfinfo_from_path(str(path))["Pages"])
    return 1


def _ticket_of(path: Path) -> str:
    """Ticket id from the filename prefix, e.g. CMO20251190995_complaint_x.pdf."""
    return path.stem.split("_", 1)[0]


def discover_pages(input_dir: Path, limit: int | None) -> list[tuple[Path, int]]:
    pages: list[tuple[Path, int]] = []
    for path in sorted(input_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in IMAGE_SUFFIXES and path.suffix.lower() != ".pdf":
            continue
        for number in range(1, _page_count(path) + 1):
            pages.append((path, number))
            if limit is not None and len(pages) >= limit:
                return pages
    return pages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Directory of documents.")
    parser.add_argument("--out", type=Path, required=True, help="Where to write aggregates.")
    parser.add_argument("--limit", type=int, default=None, help="Cap pages (cost control).")
    parser.add_argument(
        "--language", default="en-IN", help="Language hint passed to Sarvam."
    )
    parser.add_argument(
        "--audit-db",
        type=Path,
        default=None,
        help="Sarvam egress audit log (default: <out>/sarvam_audit.sqlite).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render and run pytesseract only. No Sarvam call, no spend.",
    )
    parser.add_argument(
        "--dump-text",
        action="store_true",
        help="Print both transcripts. Debugging only; refuses more than one page.",
    )
    args = parser.parse_args(argv)

    pages = discover_pages(args.input, args.limit)
    if not pages:
        logger.error(f"no documents found under {args.input}")
        return 1

    cost = len(pages) * PRICE_PER_PAGE_RUPEES
    logger.info(f"{len(pages)} page(s) across {len({p for p, _ in pages})} document(s)")
    logger.info(f"Sarvam cost if run: Rs {cost:.2f} at Rs {PRICE_PER_PAGE_RUPEES}/page")

    if args.dump_text and len(pages) > 1:
        logger.error(
            "--dump-text prints real grievance text and is limited to one page; "
            "narrow the sample with --limit 1"
        )
        return 1

    adapter = None
    if not args.dry_run:
        route = PROVIDER_REGISTRY["sarvam-vision"]
        if not route.egress_permitted:
            logger.error(
                "Sarvam egress is not permitted: "
                f"unverified controls {route.unverified_controls} and no accepted risk"
            )
            return 1
        logger.warning(
            f"LIVE RUN: sending {len(pages)} page(s) of real grievance documents to "
            f"{route.provider} on basis '{route.egress_basis}'"
        )
        args.out.mkdir(parents=True, exist_ok=True)
        audit_db = args.audit_db or (args.out / "sarvam_audit.sqlite")
        adapter = SarvamVisionAdapter(enabled=True, audit_log=SqliteAuditLog(audit_db))

    from janasunani.pipeline.stages.ocr_extraction.pytesseract_backend import extract_text

    records: list[PageRecord] = []
    failures: list[dict[str, str]] = []
    page_lengths: list[dict[str, object]] = []
    for path, number in pages:
        page_id = f"{path.stem}:p{number}"
        image = render_page(path, number)

        local = extract_text(image)

        remote = ""
        if adapter is not None:
            from io import BytesIO

            buffer = BytesIO()
            image.save(buffer, format="PNG")
            context = SarvamAuditContext(
                ticket=_ticket_of(path),
                stage="ocr_extraction",
                document_id=f"{path.name}:{number}",
            )
            try:
                remote = adapter.digitise(
                    buffer.getvalue(), f"{page_id}.png", args.language, context
                )
            except Exception as exc:  # noqa: BLE001 - recorded, run continues
                failures.append({"page_id": page_id, "error": type(exc).__name__})
                logger.error(f"{page_id}: {type(exc).__name__}: {exc}")

        records.append(
            PageRecord(
                ticket=_ticket_of(path),
                page_id=page_id,
                pytesseract_text=local,
                sarvam_markdown=remote,
            )
        )
        # Report normalized lengths, because those are what the scorecard
        # compares. Raw counts are wildly misleading here: Sarvam embeds figure
        # crops as base64 data URIs, so a page whose text is ~700 characters
        # arrives as ~36,000 and reads like a 100x blow-up. normalize_text
        # strips them, and the raw number is kept only to show how much of the
        # payload was never text.
        local_n = len(normalize_text(local))
        remote_n = len(normalize_text(remote))
        page_lengths.append(
            {
                "page_id": page_id,
                "pytesseract_chars": local_n,
                "sarvam_chars": remote_n,
                "sarvam_raw_chars": len(remote),
            }
        )
        logger.info(
            f"{page_id}: pytesseract {local_n} chars, sarvam {remote_n} chars "
            f"(sarvam raw {len(remote)}, {100 * (1 - remote_n / len(remote)) if remote else 0:.0f}% non-text)"
        )

        if args.dump_text:
            print("---- pytesseract ----")
            print(local)
            print("---- sarvam ----")
            print(remote)

    args.out.mkdir(parents=True, exist_ok=True)
    report = build_scorecard(records)
    payload = asdict(report)
    payload["failures"] = failures
    payload["pages_submitted"] = len(pages)
    payload["cost_rupees"] = 0.0 if args.dry_run else cost
    # Lengths only. Never the text: these documents are real citizen grievances.
    payload["page_lengths"] = page_lengths

    destination = args.out / "sarvam_scorecard.json"
    destination.write_text(json.dumps(payload, indent=2, default=str))
    logger.success(f"scorecard -> {destination}")

    if failures:
        logger.warning(f"{len(failures)} page(s) failed; they score as empty remote text")
    return 0


if __name__ == "__main__":
    sys.exit(main())
