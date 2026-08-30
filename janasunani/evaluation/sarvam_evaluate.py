"""Sarvam benchmark runner — digitise / extract / both arms.

This is the ``janasunani-evaluate-sarvam`` CLI (Unit E). It reuses the
same-pixels-to-both-engines invariant from ``scripts/analysis/sarvam_sample.py``
but promotes it to a proper evaluation module with:

* ``--arm {digitise,extract,both}``  (digitise = OCR markdown vs pytesseract,
  extract = structured fields vs gold category + BART summary, both = ₹1.50/page)
* ``--join-metadata``  (lake/OLTP slice for gold_category, pipeline_category,
  pipeline_summary, handwritten, language — redacted metadata only)
* ``--schema-version v1``  (pins :mod:`sarvam_grievance_schema` before any
  output is read, per #127)

Live Sarvam calls are identical to the sample runner: one async job per page
via :class:`SarvamVisionAdapter`, audit-logged with ticket/stage/provider/
bytes and resilient to the kill switch (falls back to ``pytesseract``). CI /
recorded-transport tests use ``--dry-run`` or inject a fake transport; no live
network call is made in tests.

Cost model (ROADMAP §5.5): digitise ₹0.50/page, extract ₹1.00/page,
both ₹1.50/page. The scorecard JSON records pages, arm, cost and schema
version so ``benchmark_report`` can compare runs without re-reading pages.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import contextlib
import json
import os
import sys
from pathlib import Path
import tempfile
from typing import Any

from loguru import logger

from janasunani.config import DEMO_SLICE_LABEL
from janasunani.evaluation.sarvam_grievance_schema import (
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    get_schema,
)
from janasunani.evaluation.sarvam_scorecard import (
    PageRecord,
    build_scorecard,
    render_markdown,
    write_outputs,
)


PRICE_DIGITISE = 0.50
PRICE_EXTRACT = 1.00
PRICE_BOTH = 1.50
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif"}
PROGRESS_SCHEMA_VERSION = "janasunani.sarvam-progress/v1"
PROGRESS_FILENAME = "sarvam_progress.json"


def _price_for_arm(arm: str, n_pages: int) -> float:
    if arm == "digitise":
        return n_pages * PRICE_DIGITISE
    if arm == "extract":
        return n_pages * PRICE_EXTRACT
    if arm == "both":
        return n_pages * PRICE_BOTH
    return 0.0


def _page_count(path: Path) -> int:
    if path.suffix.lower() == ".pdf":
        from pdf2image import pdfinfo_from_path

        return int(pdfinfo_from_path(str(path))["Pages"])
    return 1


def _ticket_of(path: Path) -> str:
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


def _input_snapshot_id(pages: list[tuple[Path, int]]) -> str:
    """Content-address document bytes, pages, and ticket assignments.

    Filenames can contain ticket identifiers, so never persist them or their
    individual hashes. The ticket parsed from each filename nevertheless
    determines the metadata join and must be part of the aggregate identity.
    Hash each source file's bytes once, then bind its digest, requested page,
    and an in-memory ticket digest into the final order-independent snapshot.
    Mtimes and filename decorations outside the parsed ticket are irrelevant.
    """

    digest = hashlib.sha256()
    file_digests: dict[Path, bytes] = {}
    members: list[bytes] = []
    for path, number in pages:
        resolved = path.resolve()
        file_digest = file_digests.get(resolved)
        if file_digest is None:
            source_digest = hashlib.sha256()
            with resolved.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    source_digest.update(chunk)
            file_digest = source_digest.digest()
            file_digests[resolved] = file_digest
        ticket_digest = hashlib.sha256(_ticket_of(path).encode("utf-8")).digest()
        members.append(
            file_digest + ticket_digest + b"\0" + str(number).encode("ascii")
        )
    for value in sorted(members):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return f"sha256:{digest.hexdigest()}"


def _write_progress_checkpoint(
    *,
    out_dir: Path,
    input_snapshot_id: str,
    pages_discovered: int,
    pages_processed: int,
    records: list[PageRecord],
    failures: list[dict[str, str]],
    page_lengths: list[dict[str, object]],
    arm: str,
    schema_version: str,
    slice_label: str,
    dry_run: bool,
    complete: bool,
) -> Path:
    """Atomically persist safe aggregate progress after every page.

    The checkpoint intentionally excludes page IDs, ticket IDs, transcripts,
    summaries, categories attached to a ticket, and provider response bodies.
    Accepted provider jobs remain resumable through the separately governed
    audit log; this file preserves what can be reported if credits or the
    process stop before the final scorecard write.
    """
    from janasunani.evaluation.sarvam_scorecard import normalize_text

    failure_arms = Counter(row["arm"] for row in failures)
    failure_types = Counter(row["error"] for row in failures)
    eligible_pairs = records if arm in {"digitise", "both"} else []
    divergence_n = sum(
        normalize_text(row.pytesseract_text) != normalize_text(row.sarvam_markdown)
        for row in eligible_pairs
    )
    payload = {
        "schema_version": PROGRESS_SCHEMA_VERSION,
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "complete": complete,
        "input_snapshot_id": input_snapshot_id,
        "arm": arm,
        "extract_schema_version": schema_version,
        "slice": slice_label,
        "dry_run": dry_run,
        "pages_discovered": pages_discovered,
        "pages_processed": pages_processed,
        "pages_scored": len(records),
        "pages_excluded": max(0, pages_processed - len(records)),
        "failure_events": len(failures),
        "failures_by_arm": dict(sorted(failure_arms.items())),
        "failures_by_error": dict(sorted(failure_types.items())),
        "pytesseract_normalized_characters": sum(
            int(row.get("pytesseract_chars", 0)) for row in page_lengths
        ),
        "sarvam_normalized_characters": sum(
            int(row.get("sarvam_chars", 0)) for row in page_lengths
        ),
        "paired_exact_divergence_count": divergence_n,
        "paired_exact_divergence_n": len(eligible_pairs),
        "maximum_list_price_rupees": (
            0.0 if dry_run else _price_for_arm(arm, pages_discovered)
        ),
        "actual_billing_available": False,
        "privacy": {
            "contains_page_or_ticket_ids": False,
            "contains_text_or_provider_payloads": False,
            "contains_category_or_demographic_breakdowns": False,
            "mode": "0600",
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    destination = out_dir / PROGRESS_FILENAME
    fd, temporary_name = tempfile.mkstemp(prefix=".sarvam-progress-", dir=out_dir)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def _load_metadata_join(
    lake_dir: Path | None,
    slice_label: str | None,
) -> dict[str, dict[str, Any]]:
    """Load redacted metadata for ``--join-metadata``.

    Returns mapping ``ticket -> {gold_category, pipeline_category,
    pipeline_summary, ...}`` plus per-page overrides keyed as
    ``"{ticket}:{page_number}"`` for ``handwritten``/``language`` so that
    multi-page tickets with differing page metadata are not collapsed (ticket-
    keyed overwrites corrupt the handwritten/printed splits).

    When the lake is missing or unreadable an empty dict is returned and the
    runner continues with category arm marked not measured.
    """
    if lake_dir is None:
        # Try default interim location; if missing, return empty.
        from janasunani.config import directories

        default = Path(directories.INTERIM)
        if default.is_dir():
            lake_dir = default
        else:
            return {}
    lake_dir = Path(lake_dir)
    if not lake_dir.is_dir():
        return {}
    mapping: dict[str, dict[str, Any]] = {}
    # Lazy import so the module remains importable with --extra serving only.
    try:
        from janasunani.olap import lake as lake_mod
    except Exception:  # noqa: BLE001
        return {}
    # Complaints: try grievance_category then category
    for col in ("grievance_category", "category"):
        try:
            # Slice filter if provided
            where = f"WHERE {col} IS NOT NULL"
            if slice_label and "/" in slice_label:
                district, year_s = slice_label.split("/", 1)
                try:
                    year = int(year_s.strip())
                    # Filter by district/year when available
                    district_esc = district.replace("'", "''")
                    where += f" AND lower(district) = lower('{district_esc}') AND created_year = {year}"
                except ValueError:
                    pass
            sql = f"SELECT ticket_no, {col} AS gold_category FROM complaints {where}"
            frame = lake_mod.query(sql, lake_dir, tables=("complaints",))
            for row in frame.iter_rows(named=True):
                ticket = row.get("ticket_no") or row.get("ticketNo")
                gold = row.get("gold_category")
                if ticket and gold:
                    mapping.setdefault(str(ticket), {})["gold_category"] = str(gold)
            if mapping:
                break
        except FileNotFoundError:
            continue
        except Exception:  # noqa: BLE001
            continue
    # Documents: pipeline outputs for paired accuracy (grievance_category + summary)
    # This fixes fabricated zero pipeline accuracy when gold is present but
    # documents.parquet is ignored.
    try:
        sql = "SELECT ticket_number, grievance_category, summary FROM documents WHERE ticket_number IS NOT NULL"
        frame = lake_mod.query(sql, lake_dir, tables=("documents",))
        for row in frame.iter_rows(named=True):
            ticket = row.get("ticket_number")
            if not ticket:
                continue
            ticket_s = str(ticket)
            cat = row.get("grievance_category") or row.get("category")
            summ = row.get("summary")
            if cat:
                mapping.setdefault(ticket_s, {})["pipeline_category"] = str(cat)
            if summ:
                mapping.setdefault(ticket_s, {})["pipeline_summary"] = str(summ)
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001
        pass
    # Pages: handwritten, language — preserve page identity via (ticket, page_number)
    # Ticket-keyed overwrites corrupt splits for multi-page tickets, so store
    # per-page overrides under composite key "{ticket}:{page_number}" and keep
    # ticket-level fallback for single-page or when page_number missing.
    for col in ("handwritten", "language"):
        try:
            # Select per-page identity; page_number may be NULL for legacy rows
            sql = (
                "SELECT ticket_number, page_number, page_id, "
                f"{col} FROM pages WHERE ticket_number IS NOT NULL AND {col} IS NOT NULL"
            )
            frame = lake_mod.query(sql, lake_dir, tables=("pages",))
            for row in frame.iter_rows(named=True):
                ticket = row.get("ticket_number")
                val = row.get(col)
                if not ticket or not val:
                    continue
                ticket_s = str(ticket)
                val_s = str(val)
                page_number = row.get("page_number")
                # Always keep ticket-level fallback (first wins for backwards compat)
                mapping.setdefault(ticket_s, {}).setdefault(col, val_s)
                # Per-page override when page_number available
                if page_number is not None:
                    try:
                        pn = int(page_number)
                        key = f"{ticket_s}:{pn}"
                        mapping.setdefault(key, {})[col] = val_s
                    except (ValueError, TypeError):
                        pass
                else:
                    # page_id fallback if page_number absent
                    page_id = row.get("page_id")
                    if page_id:
                        mapping.setdefault(f"{ticket_s}#{page_id}", {})[col] = val_s
        except FileNotFoundError:
            continue
        except Exception:  # noqa: BLE001
            continue
    return mapping


def _checked_record_destination(path: Path) -> Path:
    """Resolve ``--save-records`` and refuse anywhere outside ``data/``.

    The dump is **unredacted**: every record carries the citizen's own words
    plus whatever the provider returned. ``data/`` is the one tree the data
    policy governs and the one git ignores wholesale, so a dump landing
    anywhere else is a file nobody is watching. ``docs/records.jsonl`` or a
    bare ``records.jsonl`` at the repo root would previously have been created
    without complaint and would then sit in `git status` waiting to be added.

    Resolved before comparison so ``data/../docs/x.jsonl`` and a symlink into
    the tree are both rejected rather than passing a prefix check.
    """
    from janasunani.config import DATA_DIR

    resolved = Path(path).expanduser().resolve()
    root = DATA_DIR.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(
            f"--save-records must write inside {root} (the governed data tree), "
            f"not {resolved}. The dump is unredacted citizen text: it may not be "
            "written where it could be committed or shared by accident."
        )
    return resolved


def _probe_record_destination(destination: Path) -> None:
    """Fail now if the dump could not be written, without destroying anything.

    Writability is a property of the directory plus, where the target already
    exists, of that file. Neither can be established from the path alone --
    ``_checked_record_destination`` proves only that the location is
    permitted -- and finding out at the write costs a whole paid run.

    Never truncates. An existing dump from a previous run is evidence; the
    probe checks it is writable and leaves the bytes alone. For a new file it
    creates and removes a uniquely named sibling, which tests the directory
    that the real ``os.open`` will use without inventing a partial file at
    the destination.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not destination.is_file():
            raise OSError(f"{destination} exists and is not a regular file")
        if not os.access(destination, os.W_OK):
            raise OSError(f"{destination} exists and is not writable")
        return
    probe = destination.parent / f".{destination.name}.probe-{os.getpid()}"
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    finally:
        try:
            probe.unlink()
        except FileNotFoundError:
            pass


def _write_record_dump(destination: Path, records: Any) -> Path:
    """Write the unredacted per-page dump, 0600 from the moment it exists.

    The mode has to be applied at creation, not after the write. Under the
    usual ``022`` umask ``Path.open("w")`` creates the file ``0644``, so the
    citizen text was world-readable on the host for the entire write and was
    narrowed only once the last record had landed -- a window that grows with
    the run. An interrupt before the ``chmod`` left it ``0644`` permanently.

    ``os.open`` applies the mode as the file is created. ``fchmod`` covers the
    other half: if the path already exists, ``O_CREAT``'s mode argument is
    ignored, so a dump overwriting a previously world-readable file would
    otherwise keep those permissions.

    The write goes to a temporary sibling and is renamed into place only once
    every record has landed. Writing ``O_TRUNC`` straight onto the destination
    destroyed the previous dump the instant the new one opened, so a
    serialisation error or an interrupt mid-run left a truncated file where
    complete evidence had been -- and this is unredacted per-page output that
    a paid run produced, not something a retry reconstructs for free. The
    startup probe is deliberately non-destructive for the same reason; this
    closes the other half of it.

    The sibling shares the destination's directory so the rename is on one
    filesystem and therefore atomic, and it is created 0600 like the final
    file: it holds the same citizen text.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f".{destination.name}.{os.getpid()}.partial")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(asdict(record), default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, destination)
    except BaseException:
        # Including KeyboardInterrupt: a Ctrl-C is the interrupt this exists
        # to survive, and leaving the partial sibling behind would strand
        # unredacted text at a path nothing later cleans up.
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return destination


def _unwrap_extract_result(payload: Any) -> dict[str, Any]:
    """Unwrap the various shapes ``adapter.extract`` may return.

    ``result`` (singular) is the shape the live API actually returns and is
    checked first. GET /doc-ai/v1/job/{job_id}/results answers with the
    envelope ``{job_id, type, status, usage, result, annotations, version}``
    and the schema's fields live under ``result``.

    Omitting it was silent rather than loud: the final ``return payload``
    fallback handed back the envelope, and the caller's
    ``payload.get("grievance_category")`` then read one level too high and
    got ``None``. The 2026-08-25 Sambalpur/2024 run billed 200 Extract pages
    and recorded ``sarvam_category`` as null on all 198 scored pages for
    exactly this reason, which scored as 0.000 accuracy rather than as a
    missing measurement. A shape this function does not recognise must not
    look like a confident empty answer.
    """
    if isinstance(payload, dict):
        if isinstance(payload.get("result"), dict):
            return payload["result"]
        if "results" in payload and isinstance(payload["results"], list) and payload["results"]:
            first = payload["results"][0]
            if isinstance(first, dict):
                return first
        if "data" in payload and isinstance(payload["data"], dict):
            return payload["data"]
        # Direct field dict
        return payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    return {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sarvam benchmark — digitise / extract / both (Unit E).",
        prog="janasunani-evaluate-sarvam",
    )
    parser.add_argument("--input", "--input-dir", type=Path, dest="input_dir", required=True, help="Directory of documents.")
    parser.add_argument("--out", "--output", "--output-dir", type=Path, dest="out", required=True, help="Where to write aggregates.")
    parser.add_argument(
        "--arm",
        choices=["digitise", "extract", "both"],
        default="digitise",
        help="Benchmark arm: digitise (OCR markdown vs pytesseract), extract (structured fields vs gold), or both (digitise+extract).",
    )
    parser.add_argument(
        "--schema-version",
        default=SCHEMA_VERSION,
        choices=sorted(SUPPORTED_SCHEMA_VERSIONS.keys()),
        help="Pinned Extract schema version (frozen before output is read).",
    )
    parser.add_argument(
        "--join-metadata",
        action="store_true",
        default=False,
        help="Join redacted lake/OLTP metadata (gold_category, handwritten, language, pipeline fields).",
    )
    parser.add_argument("--lake-dir", type=Path, default=None, help="Lake dir for --join-metadata (default: data/interim).")
    parser.add_argument("--slice", type=str, default=DEMO_SLICE_LABEL, help="Slice label DISTRICT/YEAR for metadata join (default: Sambalpur/2024).")
    parser.add_argument("--limit", type=int, default=None, help="Cap pages (cost control).")
    parser.add_argument("--language", default="en-IN", help="Language hint passed to Sarvam.")
    parser.add_argument("--audit-db", type=Path, default=None, help="Sarvam egress audit log (default: <out>/sarvam_audit.sqlite).")
    parser.add_argument("--dry-run", action="store_true", help="Render and run pytesseract only. No Sarvam call, no spend.")
    parser.add_argument("--dump-text", action="store_true", help="Print both transcripts. Debugging only; refuses more than one page.")
    parser.add_argument(
        "--save-records",
        type=Path,
        default=None,
        help=(
            "Persist one JSON line per scored page (ticket, page id, pytesseract "
            "text, Sarvam markdown/category/summary, pipeline category/summary, "
            "gold category, handwritten, language) to this path. Unredacted and "
            "carries real ticket ids and grievance text — write it under a "
            "controlled data/ location only, never under outputs/ or git."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    input_dir: Path = args.input_dir  # type: ignore[attr-defined]
    out_dir: Path = args.out  # type: ignore[attr-defined]

    if not input_dir.is_dir():
        logger.error(f"input dir not found: {input_dir}")
        return 1

    # Resolved here rather than at the write, which is after every page has
    # been rendered, sent to a paid provider and scored. A bad path is knowable
    # at startup, and finding out at the end costs the whole run — the command
    # then returned 1 before writing the aggregate outputs too, so the money
    # bought nothing. Held for the actual write below.
    record_destination: Path | None = None
    if args.save_records:
        try:
            record_destination = _checked_record_destination(args.save_records)
            # Prove it can actually be written, not merely that it sits in a
            # permitted directory. A parent that is an existing file, a
            # read-only directory or a bad permission all pass the location
            # check and then raise inside _write_record_dump -- after every
            # page has been rendered, submitted to a paid provider and
            # scored.
            #
            # Non-destructively. The first version of this probe called
            # _write_record_dump directly, which opens O_TRUNC: pointing
            # --save-records at a previous run's dump erased it before any
            # other check ran, and an early return then left neither the old
            # evidence nor a replacement.
            _probe_record_destination(record_destination)
        except (ValueError, OSError) as exc:
            logger.error(f"--save-records destination is unusable: {exc}")
            return 1

    try:
        schema = get_schema(args.schema_version)
    except ValueError as exc:
        parser.error(str(exc))
        return 2  # unreachable (parser.error exits)

    pages = discover_pages(input_dir, args.limit)
    if not pages:
        logger.error(f"no documents found under {input_dir}")
        return 1
    input_snapshot_id = _input_snapshot_id(pages)

    cost = _price_for_arm(args.arm, len(pages))
    logger.info(f"{len(pages)} page(s) across {len({p for p, _ in pages})} document(s) — arm={args.arm} schema={args.schema_version}")
    logger.info(f"Sarvam cost if run: Rs {cost:.2f} ({args.arm} @ {cost / len(pages) if pages else 0:.2f}/page)")

    if args.dump_text and len(pages) > 1:
        logger.error("--dump-text prints real grievance text and is limited to one page; narrow the sample with --limit 1")
        return 1

    metadata: dict[str, dict[str, Any]] = {}
    if args.join_metadata:
        metadata = _load_metadata_join(args.lake_dir, args.slice)
        logger.info(f"metadata join: {len(metadata)} ticket(s) with lake metadata (slice={args.slice})")

    adapter = None
    if not args.dry_run:
        # Lazy import so tests with --extra serving can run without pipeline-core
        from janasunani.egress.sarvam import PROVIDER_REGISTRY, SarvamAuditContext, SarvamVisionAdapter, SqliteAuditLog

        route = PROVIDER_REGISTRY["sarvam-vision"]
        if not route.egress_permitted:
            logger.error(f"Sarvam egress is not permitted: unverified controls {route.unverified_controls} and no accepted risk")
            return 1
        logger.warning(f"LIVE RUN: sending {len(pages)} page(s) of real grievance documents to {route.provider} on basis '{route.egress_basis}' (arm={args.arm})")
        out_dir.mkdir(parents=True, exist_ok=True)
        audit_db = args.audit_db or (out_dir / "sarvam_audit.sqlite")
        adapter = SarvamVisionAdapter(enabled=True, audit_log=SqliteAuditLog(audit_db))
    else:
        from janasunani.egress.sarvam import SarvamAuditContext as _Ctx  # noqa: F401  # ensure import works

    # Lazy pytesseract + renderer — only needed when actually processing
    # Under `uv run --extra serving` Pillow/pdf2image are absent; the old code
    # swallowed the ImportError and later skipped every Sarvam call with
    # image=None while still exiting 0 with full cost, producing an empty
    # benchmark. For live runs we must fail explicitly; for --dry-run (CI)
    # we allow empty rendering so `uv run --extra serving pytest` stays green.
    # Preserve already-patched render_page for tests (monkeypatch sets eval_mod.render_page)
    # so the test's FakeAdapter + fake image is used even when pdf2image is missing on CI
    _pre_patched = globals().get("render_page", None)
    render_import_error: Exception | None = None
    try:
        from janasunani.pipeline.stages.ocr_extraction.page_renderer import render_page as _real_render  # type: ignore[assignment]

        if _pre_patched is not None and callable(_pre_patched):
            render_page = _pre_patched  # type: ignore[assignment]
        else:
            render_page = _real_render  # type: ignore[assignment]
        render_import_error = None
    except Exception as exc:  # noqa: BLE001
        if _pre_patched is not None and callable(_pre_patched):
            render_page = _pre_patched  # type: ignore[assignment]
            render_import_error = None
        else:
            render_page = None  # type: ignore[assignment]
            render_import_error = exc

    # Fail fast for live runs where renderer is required (would otherwise
    # silently skip every Sarvam call and report pages_submitted with no work).
    # For --dry-run (CI) and for mocked tests that inject a fake adapter, allow
    # empty images so `uv run --extra serving pytest` stays green.
    if render_page is None and adapter is not None and not args.dry_run:
        logger.error(
            "page_renderer not available — install pipeline-core extra: "
            f"uv run --extra pipeline-core janasunani-evaluate-sarvam ({type(render_import_error).__name__}: {render_import_error})"
        )
        return 1
    if render_page is None and render_import_error is not None:
        # Dry-run or mocked test without renderer: keep going with empty images (CI)
        logger.warning(
            f"page_renderer unavailable ({type(render_import_error).__name__}); "
            "using empty images for --dry-run / test — install pipeline-core for real OCR"
        )

    # pylint: disable=import-error
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[PageRecord] = []
    failures: list[dict[str, str]] = []
    page_lengths: list[dict[str, object]] = []

    for processed, (path, number) in enumerate(pages, 1):
        page_id = f"{path.stem}:p{number}"
        ticket = _ticket_of(path)

        # Render + local OCR (or stub when renderer unavailable)
        image = None
        if render_page is not None:
            try:
                image = render_page(path, number)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"{page_id}: render failed ({type(exc).__name__}), using empty image")
                image = None

        local_text = ""
        if image is not None:
            try:
                from janasunani.pipeline.stages.ocr_extraction.pytesseract_backend import extract_text

                local_text = extract_text(image)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"{page_id}: pytesseract failed ({type(exc).__name__}), using empty text")
                local_text = ""
        else:
            # No renderer (test env) — use empty local text so divergence is still computable
            local_text = ""

        sarvam_markdown = ""
        sarvam_category: str | None = None
        sarvam_summary: str | None = None
        page_failed = False

        if adapter is not None and image is not None:
            from io import BytesIO

            from janasunani.egress.sarvam import SarvamAuditContext

            buffer = BytesIO()
            try:
                image.save(buffer, format="PNG")
                doc_bytes = buffer.getvalue()
            except Exception:  # noqa: BLE001
                doc_bytes = b""
            context = SarvamAuditContext(ticket=ticket, stage="ocr_extraction", document_id=f"{path.name}:{number}")

            if args.arm in ("digitise", "both"):
                try:
                    sarvam_markdown = adapter.digitise(doc_bytes, f"{page_id}.png", args.language, context)
                except Exception as exc:  # noqa: BLE001
                    failures.append({"page_id": page_id, "error": type(exc).__name__, "arm": "digitise"})
                    logger.error(f"{page_id} digitise: {type(exc).__name__}: {exc}")
                    sarvam_markdown = ""
                    page_failed = True
            if args.arm in ("extract", "both"):
                try:
                    raw = adapter.extract(doc_bytes, f"{page_id}.png", args.language, context, schema=schema)
                    payload = _unwrap_extract_result(raw)
                    sarvam_category = payload.get("grievance_category") or payload.get("category") or payload.get("grievanceCategory")
                    sarvam_summary = payload.get("summary") or payload.get("sarvam_summary")
                    # Do not copy grievance_text into sarvam_markdown for extract-only:
                    # that would publish Extract structured text as Digitise OCR and
                    # conflate separately billed arms. OCR is not measured for
                    # extract-only (handled in scorecard).
                except Exception as exc:  # noqa: BLE001
                    failures.append({"page_id": page_id, "error": type(exc).__name__, "arm": "extract"})
                    logger.error(f"{page_id} extract: {type(exc).__name__}: {exc}")
                    page_failed = True
        elif adapter is not None and image is None:
            # Renderer missing but Sarvam still requested — image is None
            # implies render failure; treat as per-page failure rather than
            # scoring empty text as divergence/incorrect.
            failures.append({"page_id": page_id, "error": "RenderUnavailable", "arm": args.arm})
            logger.error(f"{page_id}: cannot render page — image is None, skipping Sarvam call")
            page_failed = True

        # Metadata join overrides — page identity preserved for handwritten/language
        # Ticket-level fields: gold_category, pipeline_*
        ticket_meta = metadata.get(ticket, {})
        page_key = f"{ticket}:{number}"
        page_meta = metadata.get(page_key, {})
        # gold_category only scored for extract/both (digitise-only would fabricate 0% Sarvam)
        if args.arm in ("extract", "both"):
            gold_category = ticket_meta.get("gold_category")
            pipeline_category = ticket_meta.get("pipeline_category")
            pipeline_summary = ticket_meta.get("pipeline_summary")
        else:
            gold_category = None
            pipeline_category = None
            pipeline_summary = None
        # handwritten/language are page-level; prefer per-page override then ticket fallback
        handwritten = page_meta.get("handwritten") or ticket_meta.get("handwritten")
        language = page_meta.get("language") or ticket_meta.get("language") or args.language
        # For extract-only, OCR is not measured — clear sarvam_markdown even if fallback would set it
        if args.arm == "extract":
            sarvam_markdown = ""

        # Exclude failed Sarvam calls from paired scorecard rather than scoring
        # timeout/rate-limit as divergence or incorrect category (bias).
        if page_failed:
            logger.warning(f"{page_id}: Sarvam call failed — excluding page from scorecard (not scored as divergence/incorrect)")
            # still record lengths for observability but skip PageRecord
            try:
                from janasunani.evaluation.sarvam_scorecard import normalize_text

                local_n = len(normalize_text(local_text))
                remote_n = len(normalize_text(sarvam_markdown))
            except Exception:  # noqa: BLE001
                local_n = len(local_text)
                remote_n = len(sarvam_markdown)
            page_lengths.append({"page_id": page_id, "pytesseract_chars": local_n, "sarvam_chars": remote_n, "sarvam_raw_chars": len(sarvam_markdown), "failed": True})
            logger.info(f"{page_id}: pytesseract {local_n} chars, sarvam {remote_n} chars (raw {len(sarvam_markdown)}) — FAILED, excluded")
            if args.dump_text:
                print("---- pytesseract ----")
                print(local_text)
                print("---- sarvam ----")
                print(sarvam_markdown)
                if sarvam_summary:
                    print("---- sarvam_summary ----")
                    print(sarvam_summary)
            _write_progress_checkpoint(
                out_dir=out_dir,
                input_snapshot_id=input_snapshot_id,
                pages_discovered=len(pages),
                pages_processed=processed,
                records=records,
                failures=failures,
                page_lengths=page_lengths,
                arm=args.arm,
                schema_version=args.schema_version,
                slice_label=args.slice,
                dry_run=args.dry_run,
                complete=False,
            )
            continue

        records.append(
            PageRecord(
                ticket=ticket,
                page_id=page_id,
                handwritten=handwritten,
                language=language,
                pytesseract_text=local_text,
                sarvam_markdown=sarvam_markdown,
                pipeline_category=pipeline_category,
                sarvam_category=sarvam_category,
                gold_category=gold_category,
                sarvam_summary=sarvam_summary,
                pipeline_summary=pipeline_summary,
            )
        )

        # Length reporting — normalised lengths are what the scorecard compares
        try:
            from janasunani.evaluation.sarvam_scorecard import normalize_text

            local_n = len(normalize_text(local_text))
            remote_n = len(normalize_text(sarvam_markdown))
        except Exception:  # noqa: BLE001
            local_n = len(local_text)
            remote_n = len(sarvam_markdown)
        page_lengths.append({"page_id": page_id, "pytesseract_chars": local_n, "sarvam_chars": remote_n, "sarvam_raw_chars": len(sarvam_markdown)})
        logger.info(f"{page_id}: pytesseract {local_n} chars, sarvam {remote_n} chars (raw {len(sarvam_markdown)})")

        if args.dump_text:
            print("---- pytesseract ----")
            print(local_text)
            print("---- sarvam ----")
            print(sarvam_markdown)
            if sarvam_summary:
                print("---- sarvam_summary ----")
                print(sarvam_summary)

        _write_progress_checkpoint(
            out_dir=out_dir,
            input_snapshot_id=input_snapshot_id,
            pages_discovered=len(pages),
            pages_processed=processed,
            records=records,
            failures=failures,
            page_lengths=page_lengths,
            arm=args.arm,
            schema_version=args.schema_version,
            slice_label=args.slice,
            dry_run=args.dry_run,
            complete=False,
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    # Pass arm + slice so scorecard correctly hides non-measured arms (digitise vs extract)
    # and renders the selected slice (not demo constant).
    report = build_scorecard(records, slice_label=args.slice, arm=args.arm)

    # Optional per-page record dump. The aggregate path deliberately keeps no
    # provider payloads (`contains_text_or_provider_payloads: False` in the
    # checkpoint), so without this the transcripts, categories and summaries are
    # computed, scored and discarded. Reference examples need them kept, and the
    # help text is explicit that the file is unredacted.
    if record_destination is not None:
        _write_record_dump(record_destination, records)
        logger.success(
            f"records -> {record_destination} ({len(records)} pages, unredacted)"
        )

    # Write markdown + base scorecard first, then overwrite JSON with enriched payload
    try:
        outputs = write_outputs(report, out_dir)
        logger.success(f"markdown -> {outputs['markdown']}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"write_outputs failed: {exc}")

    payload = report.to_dict()
    # Enrich payload with run metadata (feeds benchmark_report)
    payload["failures"] = failures
    payload["pages_submitted"] = len(pages)
    payload["cost_rupees"] = 0.0 if args.dry_run else cost
    payload["arm"] = args.arm
    payload["schema_version"] = args.schema_version
    payload["slice"] = args.slice
    payload["page_lengths"] = page_lengths
    # also record schema itself for audit
    payload["schema"] = schema

    destination = out_dir / "sarvam_scorecard.json"
    destination.write_text(json.dumps(payload, indent=2, default=str))
    logger.success(f"scorecard -> {destination}")
    _write_progress_checkpoint(
        out_dir=out_dir,
        input_snapshot_id=input_snapshot_id,
        pages_discovered=len(pages),
        pages_processed=len(pages),
        records=records,
        failures=failures,
        page_lengths=page_lengths,
        arm=args.arm,
        schema_version=args.schema_version,
        slice_label=args.slice,
        dry_run=args.dry_run,
        complete=True,
    )

    # Also print markdown to stdout for pipeline visibility
    print(render_markdown(report))

    if failures:
        # Failed pages are excluded from paired metrics (not scored as divergence/incorrect)
        # but kept in failures + pages_submitted for audit; report.n_pages is scored count.
        logger.warning(f"{len(failures)} page(s) failed; excluded from scorecard (pages_submitted={len(pages)}, scored={report.n_pages})")
    # Surface scored vs submitted for verification
    if report.n_pages != len(pages):
        logger.info(f"Scored {report.n_pages}/{len(pages)} pages; {len(pages)-report.n_pages} excluded (failures or arm filter)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
