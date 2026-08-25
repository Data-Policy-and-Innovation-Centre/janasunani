"""Benchmark pipeline latency with per-stage wall-clock and ticket-clustered SE.

Design (per demo-integration-rehearsal Part 5):

* Run each variant over n documents (default 20 text + 10 synthetic images)
  with k repeats, discarding the first warm request.
* Record wall time per stage: format, ocr, pii, spam, page_type,
  summarize, categorize, route (+ e2e). Live path uses
  ``PipelineGrievanceProcessor``; batch path uses artifact stage hooks.
  When heavy models are unavailable a deterministic fake processor is used
  so CI and unit tests stay light.
* Cluster SE by ticket/document — reuse the ticket-clustering logic from
  ``sarvam_scorecard._clustered_se`` (pages from one complaint are
  correlated; same for repeated timings on one ticket).
* Report: mean_seconds, se_seconds, n_clusters, p50, p90, p95 and throughput
  per stage/input path and end-to-end, plus run-level attempts and failures.
* Output: ``outputs/benchmark/latency.json``
* Variants: standard, sarvam_digitise, sarvam_extract, sarvam_both

Variants map:

* standard        — local pytesseract + Presidio + MuRIL + BART
* sarvam_digitise — Sarvam Vision digitise in OCR stage only
* sarvam_extract  — Sarvam Vision extract (schema) at document level
* sarvam_both     — digitise + extract (₹1.50/page)

Cost is reported separately via ``janasunani.evaluation.pricing`` when
available; this harness reports wall-clock only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import random
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from loguru import logger

from janasunani.evaluation.sarvam_sample_builder import IMAGE_SUFFIXES
from janasunani.pipeline.ticket import ticket_from_relpath

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_VARIANTS: set[str] = {
    "standard",
    "sarvam_digitise",
    "sarvam_extract",
    "sarvam_both",
}

# Canonical per-stage names for the live path.
STAGES: list[str] = [
    "format",
    "ocr",
    "pii",
    "spam",
    "page_type",
    "summarize",
    "categorize",
    "route",
]

# All stages plus end-to-end key written to JSON.
E2E_KEY = "e2e"
ALL_KEYS: list[str] = STAGES + [E2E_KEY]

# Default fixture counts (per plan: 20 text + 10 synthetic page images)
DEFAULT_N_TEXT = 20
DEFAULT_N_IMAGE = 10
DEFAULT_REPEATS = 3

# Output location (relative to repo root)
DEFAULT_OUTPUT = Path("outputs/benchmark/latency.json")
LATENCY_SCHEMA_VERSION = "janasunani.pipeline-latency/v1"

# Fake per-stage mean seconds (CPU laptop, no GPU). These are not
# performance claims — they are deterministic stand-ins so the harness
# and its statistics can be tested without warming real models.
_FAKE_STAGE_MEANS: dict[str, float] = {
    "format": 0.015,
    "ocr": 0.45,
    "pii": 0.30,
    "spam": 0.05,
    "page_type": 0.08,
    "summarize": 0.90,
    "categorize": 0.25,
    "route": 0.02,
}

# Sarvam digitise adds OCR latency (network + poll, 5s poll default in
# production; fake value is small and deterministic for tests).
_SARVAM_OCR_EXTRA = 0.55
_SARVAM_EXTRACT_EXTRA = 0.70


# ---------------------------------------------------------------------------
# Statistics — cluster-robust SE and percentiles
# ---------------------------------------------------------------------------


def _clustered_se(values: list[float], clusters: list[str]) -> float:
    """Cluster-robust SE for the mean of ``values`` clustered by ``clusters``.

    Sandwich estimator for a mean (same as
    ``janasunani.evaluation.sarvam_scorecard._clustered_se``):

        Var = sum_c (sum_{i in c} (x_i - mean))^2 / n^2 * C/(C-1)

    where n = len(values), C = number of clusters. When C == 1 fall back
    to the simple SE. Returns 0.0 for n < 2.
    """
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    cluster_sums: dict[str, float] = defaultdict(float)
    for v, c in zip(values, clusters):
        cluster_sums[c] += v - mean
    C = len(cluster_sums)
    summed_sq = sum(s * s for s in cluster_sums.values())
    if C <= 1:
        s2 = sum((v - mean) ** 2 for v in values) / (n - 1) if n > 1 else 0.0
        return math.sqrt(s2 / n) if n else 0.0
    correction = C / (C - 1)
    var = summed_sq / (n * n) * correction
    if var < 0:
        var = 0.0
    return math.sqrt(var)


def _percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile (q in [0, 100])."""
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    sorted_v = sorted(values)
    n = len(sorted_v)
    # position in [0, n-1]
    rank = q / 100.0 * (n - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return float(sorted_v[lo])
    frac = rank - lo
    return float(sorted_v[lo] * (1 - frac) + sorted_v[hi] * frac)


def compute_stage_stats(
    times: list[float],
    tickets: list[str],
) -> dict[str, Any]:
    """Compute mean/se/percentiles/throughput with ticket-clustered SE.

    Args:
        times: per-measurement wall seconds for one stage (or e2e).
        tickets: ticket/document id per measurement (same length as times).

    Returns dict with keys mean_seconds, se_seconds, n_clusters,
    p50, p90, p95, n, min_seconds, max_seconds and throughput. All floats
    are plain Python floats for JSON serialisation.
    """
    if len(times) != len(tickets):
        raise ValueError("times and tickets must have equal length")
    n = len(times)
    if n == 0:
        return {
            "mean_seconds": 0.0,
            "se_seconds": 0.0,
            "n_clusters": 0,
            "n": 0,
            "p50": 0.0,
            "p90": 0.0,
            "p95": 0.0,
            "min_seconds": 0.0,
            "max_seconds": 0.0,
            "throughput_per_second": 0.0,
        }
    total_seconds = sum(times)
    mean = total_seconds / n
    se = _clustered_se(times, tickets)
    n_clusters = len(set(tickets))
    p50 = _percentile(times, 50)
    p95 = _percentile(times, 95)
    return {
        "mean_seconds": float(mean),
        "se_seconds": float(se),
        "n_clusters": int(n_clusters),
        "n": int(n),
        "p50": float(p50),
        "p90": float(_percentile(times, 90)),
        "p95": float(p95),
        "min_seconds": float(min(times)),
        "max_seconds": float(max(times)),
        "throughput_per_second": float(n / total_seconds) if total_seconds > 0 else 0.0,
    }


def _document_kind(doc: Mapping[str, Any]) -> str:
    """What kind of input a document dict represents.

    Read off the doc's own fields, not its ticket string. This used to be
    ``_input_kind(ticket)``, matching on a ``"SYN-TXT-"``/``"SYN-IMG-"``
    prefix — a fixture-generator convention, not a property of the input.
    Every real ticket (``CMO20241020862``, ...) fell through to
    "unspecified", which made ``has_clean_full_path_coverage`` below
    structurally unsatisfiable for a real-document run: it requires the
    "document" path to have measurements, and none were ever attributed to
    it. The loader already knows what it built: bytes present means a
    scanned document, text present (and no bytes) means free text.
    ``document_path`` counts the same as ``document_bytes`` here —
    ``load_staged_documents`` hands back a path instead of pre-read bytes
    (see #308) so a document is still a "document" before its bytes are
    ever read.
    """
    if doc.get("document_bytes") is not None or doc.get("document_path") is not None:
        return "document"
    if doc.get("text") is not None:
        return "text"
    return "unspecified"


def _stats_by_input(
    times_by_stage: dict[str, list[float]],
    tickets_by_stage: dict[str, list[str]],
    kinds_by_stage: dict[str, list[str]],
) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    kinds = sorted(
        {kind for stage_kinds in kinds_by_stage.values() for kind in stage_kinds}
    )
    for kind in kinds:
        result[kind] = {}
        for stage, times in times_by_stage.items():
            tickets = tickets_by_stage.get(stage, [])
            stage_kinds = kinds_by_stage.get(stage, [])
            selected = [
                (seconds, ticket)
                for seconds, ticket, doc_kind in zip(times, tickets, stage_kinds)
                if doc_kind == kind
            ]
            result[kind][stage] = compute_stage_stats(
                [seconds for seconds, _ in selected],
                [ticket for _, ticket in selected],
            )
    return result


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------


def synthesize_documents(
    n_text: int = DEFAULT_N_TEXT,
    n_image: int = DEFAULT_N_IMAGE,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Create synthetic grievance documents for timing.

    Returns a list of dicts with keys ticket, text, document_name,
    document_bytes (None for text-only). No PII, no real citizen data.
    Ticket ids are deterministic for clustering tests.
    """
    rng = random.Random(seed)
    docs: list[dict[str, Any]] = []
    # Text grievances (English, Sambalpur district)
    for i in range(n_text):
        ticket = f"SYN-TXT-{i:04d}"
        # Vary text length deterministically so per-doc time varies.
        filler = rng.choice(
            [
                "Water supply is irregular in our ward.",
                "Drainage overflow near the school causes health hazard.",
                "Request for old age pension disbursal delay.",
                "Electricity outage for 3 days in our locality.",
                "Road repair pending near the primary health centre.",
            ]
        )
        text = f"Grievance {i}: {filler} Ticket {ticket}."
        docs.append(
            {
                "ticket": ticket,
                "text": text,
                "document_name": None,
                "document_bytes": None,
                "district": "Sambalpur",
            }
        )
    # Synthetic image documents use a one-page PDF with visible text. The old
    # fixture was a structurally valid *blank* page; the real OCR quality gate
    # correctly rejected it, so the timing harness could only run in fake mode.
    for i in range(n_image):
        ticket = f"SYN-IMG-{i:04d}"
        document_bytes = _single_page_text_pdf(
            "To,\n"
            "The District Collector, Sambalpur\n\n"
            "Subject: Request for road repair near the health centre\n\n"
            "Respected Sir or Madam,\n"
            "The road near our primary health centre has remained damaged for several weeks.\n"
            "Residents request an inspection and timely repair so ambulances can pass safely.\n\n"
            f"Reference: synthetic grievance {i}, ticket {ticket}\n\n"
            "Yours faithfully,\nA concerned resident"
        )
        docs.append(
            {
                "ticket": ticket,
                "text": None,
                "document_name": f"synthetic_{i}.pdf",
                "document_bytes": document_bytes,
                "district": "Sambalpur",
            }
        )
    # Deterministic shuffle so order is not fixture-grouped
    rng.shuffle(docs)
    return docs


# ---------------------------------------------------------------------------
# Real-document fixtures — a staged sample, not synthetic text
# ---------------------------------------------------------------------------

# Documents this loader accepts: PDFs plus the image types
# ``sarvam_sample_builder`` also treats as one-page documents. A staging
# directory is expected to hold only these plus, optionally,
# ``sample_manifest.json``.
SUPPORTED_DOCUMENT_SUFFIXES: frozenset[str] = frozenset({".pdf"}) | IMAGE_SUFFIXES


def _staged_document_paths(directory: Path) -> list[Path]:
    """Supported document files directly under ``directory``, sorted.

    Sorted so the file list — and therefore the fallback digest in
    ``_document_sample_digest`` — does not depend on filesystem iteration
    order.
    """
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_DOCUMENT_SUFFIXES
    )


def _document_sample_digest(
    directory: Path, manifest: Mapping[str, Any] | None
) -> str:
    """Stable digest identifying the on-disk sample, for provenance.

    Two components go in, for two different jobs. The manifest (when
    present) gives *identity* — the draw/seed/slice — and is stable across
    a re-stage to a different path. Every staged file's actual bytes give
    *integrity*. Hashing only the manifest (the original version of this
    function) made a content change on disk invisible: a bad re-download, a
    swapped page, or a stale manifest sitting beside different bytes than
    it describes all left the digest — and the manifest-count cross-check
    in ``load_staged_documents`` — unchanged as long as filenames and
    counts still lined up, even though the processor would read something
    the manifest never claimed. Hashing filename + content per file (not
    the full path) keeps the re-stage stability the manifest-only version
    had, while an equal-count substitution now moves the digest.
    """
    hasher = hashlib.sha256()
    if manifest is not None:
        hasher.update(json.dumps(manifest, sort_keys=True).encode("utf-8"))
    for path in _staged_document_paths(directory):
        hasher.update(path.name.encode("utf-8"))
        hasher.update(hashlib.sha256(path.read_bytes()).digest())
    return hasher.hexdigest()


def load_staged_documents(
    directory: Path | str,
    manifest: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Load a real staged document sample for benchmarking.

    Returns dicts shaped like ``synthesize_documents``'s (ticket, text,
    document_name, document_bytes, district) plus one extra key,
    ``document_path``. Unlike ``synthesize_documents`` — whose synthetic
    bytes are already in memory, generated, not read — a staged sample's
    bytes live on disk, and a real slice-sized draw is large enough that
    reading every file up front is not free: the staged Sambalpur/2024
    sample alone averages 627 KB/document, and #308 was filed when a
    69,844-document, 60 GB corpus made that arithmetic a bounded-RAM problem
    rather than a theoretical one. So ``document_bytes`` here is always
    ``None`` and ``document_path`` carries the file instead; the actual read
    happens later, one document at a time, in
    ``_measure_with_processor`` — deliberately outside every per-stage
    timer, so disk I/O is never attributed to a model stage (see that
    function for where). Reading real bytes (whenever it happens) means the
    OCR stage actually runs against real scans, unlike the text-only half of
    the synthetic fixture.

    ``directory`` is a flat staging directory of supported document files
    (see ``SUPPORTED_DOCUMENT_SUFFIXES``) named
    ``"<ticket>_complaint_<timestamp>.<ext>"`` — the layout
    ``janasunani.evaluation.sarvam_sample_builder.build_sample`` writes.

    Ticket ids come from the manifest's ``documents`` array (each entry has
    ``ticket``/``s3_key``/``file``) when one is available, matched by
    filename; only a file the manifest does not cover falls back to
    ``janasunani.pipeline.ticket.ticket_from_relpath`` on the bare filename
    (the same parser the pipeline itself uses, so a fallback ticket here
    still clusters with the same key it would downstream). The fallback is
    lossy for hierarchical tickets: ~2% of complaints have a ticket like
    ``OR159/P/2021/00535``, but ``sarvam_sample_builder`` stages files under
    ``Path(key).name`` — the directory part of the ticket is discarded
    before the file ever reaches disk, so without a manifest entry the
    parser can only recover the trailing segment (``00535``). Two distinct
    complaints that happen to share that trailing segment then collapse
    into one ticket, and therefore one cluster, corrupting the
    ticket-clustered SEs this harness exists to report. Stage with the
    manifest to avoid this; the fallback is a best-effort floor, not a fix.

    If ``manifest`` is not given and ``directory/sample_manifest.json``
    exists, it is loaded and used for: per-file ticket ids (above), the
    sample's district (from its ``"slice"`` field, formatted
    ``"<district>/<year>"``), and a document-count cross-check against what
    is actually staged. A missing manifest is not an error — the loader
    still works from the directory's filenames alone, just without a
    district, the cross-check, or protection against the hierarchical-
    ticket collision above.

    Raises:
        FileNotFoundError: ``directory`` does not exist or is not a
            directory.
        ValueError: the directory has no supported documents, or a
            filename cannot be parsed into a ticket. An empty document list
            must never reach ``run_benchmark`` silently — its own "no
            documents" error talks about ``n_text + n_image``, which would
            misdescribe a directory problem as a document-count-flag
            problem.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(
            f"--documents-dir {directory} does not exist or is not a directory"
        )

    if manifest is None:
        manifest_path = directory / "sample_manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text())

    files = _staged_document_paths(directory)
    if not files:
        raise ValueError(
            f"no supported documents ({sorted(SUPPORTED_DOCUMENT_SUFFIXES)}) "
            f"found in {directory}; refusing to silently benchmark an empty "
            "sample. Point --documents-dir at a staged sample, e.g. one "
            "written by janasunani-build-sarvam-sample."
        )

    district: str | None = None
    if manifest:
        slice_label = manifest.get("slice")
        if slice_label:
            district = str(slice_label).split("/", 1)[0]
    if not district:
        district = "unspecified"

    # The manifest's own ticket, keyed by staged filename, is authoritative
    # where it exists: it is the ticket that was actually drawn, before
    # sarvam_sample_builder's Path(key).name staging step discarded any
    # directory prefix. Only a file missing from the manifest (or no
    # manifest at all) falls back to parsing the filename itself.
    file_to_ticket: dict[str, str] = {}
    if manifest is not None:
        manifest_documents = manifest.get("documents")
        if isinstance(manifest_documents, list):
            for entry in manifest_documents:
                if not isinstance(entry, Mapping):
                    continue
                file_name = entry.get("file")
                manifest_ticket = entry.get("ticket")
                if file_name and manifest_ticket:
                    file_to_ticket[file_name] = manifest_ticket

    docs: list[dict[str, Any]] = []
    unparsed: list[str] = []
    for path in files:
        ticket = file_to_ticket.get(path.name) or ticket_from_relpath(path.name)
        if ticket is None:
            unparsed.append(path.name)
            continue
        docs.append(
            {
                "ticket": ticket,
                "text": None,
                "document_name": path.name,
                "document_bytes": None,
                "document_path": path,
                "district": district,
            }
        )
    if unparsed:
        raise ValueError(
            f"{len(unparsed)} file(s) in {directory} do not match the staged "
            "naming convention '<ticket>_complaint_<timestamp>.<ext>' and "
            f"cannot be assigned a ticket: {unparsed[:5]}"
            + (", ..." if len(unparsed) > 5 else "")
        )

    if manifest is not None:
        manifest_documents = manifest.get("documents")
        if isinstance(manifest_documents, list) and len(manifest_documents) != len(docs):
            logger.warning(
                "sample_manifest.json at {} lists {} document(s) but {} are "
                "staged; the sample may be incomplete or stale",
                directory,
                len(manifest_documents),
                len(docs),
            )

    return docs


def _single_page_text_pdf(text: str) -> bytes:
    """Build a deterministic one-page Helvetica PDF using only the stdlib."""
    commands = ["BT /F1 14 Tf 54 740 Td 20 TL"]
    for line in text.splitlines():
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"({escaped}) Tj T*")
    commands.append("ET")
    content = ("\n".join(commands) + "\n").encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
        + content
        + b"endstream",
    ]
    payload = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode("ascii"))
        payload.extend(obj)
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(payload)


# ---------------------------------------------------------------------------
# Fake processor — deterministic per-stage sleep for tests/CI
# ---------------------------------------------------------------------------


@dataclass
class FakeStageTimings:
    """Per-stage wall seconds for one synthetic processing call."""

    per_stage: dict[str, float]
    e2e: float


def _fake_timings_for_variant(
    variant: str,
    ticket: str,
    repeat_idx: int,
    rng: random.Random,
) -> FakeStageTimings:
    """Generate deterministic fake per-stage timings for a variant."""
    per_stage: dict[str, float] = {}
    for stage in STAGES:
        base = _FAKE_STAGE_MEANS.get(stage, 0.05)
        # Sarvam variants add OCR/extract latency
        if stage == "ocr" and variant in ("sarvam_digitise", "sarvam_both"):
            base += _SARVAM_OCR_EXTRA
        if stage in ("summarize", "categorize") and variant in (
            "sarvam_extract",
            "sarvam_both",
        ):
            # Extract replaces both summarizer and categorizer with one call;
            # model as single extra shared across stages for fake timing.
            base = base * 0.6 + _SARVAM_EXTRACT_EXTRA / 2
        # Small deterministic jitter per ticket+repeat (±15%)
        jitter = rng.uniform(0.85, 1.15)
        per_stage[stage] = max(0.001, base * jitter)
    e2e = sum(per_stage.values())
    # Add small extra e2e overhead not attributed to a stage
    e2e += rng.uniform(0.005, 0.02)
    return FakeStageTimings(per_stage=per_stage, e2e=e2e)


def _fake_process(
    variant: str,
    ticket: str,
    repeat_idx: int,
    seed: int = 0,
) -> FakeStageTimings:
    """Return fake timings without sleeping (tests run fast).

    Real wall-clock is not consumed; the harness can still exercise the
    statistics path deterministically. When callers want real sleep, set
    ``BENCHMARK_FAKE_SLEEP=1``.
    """
    # Seed per ticket+repeat for deterministic per-doc variation. Python's
    # hash() is salted per-interpreter via PYTHONHASHSEED, so it must not
    # feed the RNG here — the same --seed would silently produce different
    # fake latency means/SEs across machines and runs. A stable digest keeps
    # outputs/benchmark/latency.json reproducible for a given seed+docs.
    digest = hashlib.sha256(
        f"{seed}:{ticket}:{repeat_idx}:{variant}".encode()
    ).digest()
    rng_seed = int.from_bytes(digest[:8], "big") & 0xFFFFFFFF
    rng = random.Random(rng_seed)
    return _fake_timings_for_variant(variant, ticket, repeat_idx, rng)


# ---------------------------------------------------------------------------
# Core benchmark runner
# ---------------------------------------------------------------------------


def _get_git_sha() -> str | None:
    """Return current git HEAD short SHA, or None if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
            return sha if sha else None
    except Exception:
        pass
    return None


def _measure_with_processor(
    variant: str,
    docs: list[dict[str, Any]],
    repeats: int,
    discard_warm: bool,
    processor_factory: Callable[[str], Any] | None,
    sleep_fake: bool = False,
) -> dict[str, Any]:
    """Run processor over docs repeats times, collect per-stage wall seconds.

    Returns dict stage -> list of wall seconds (one entry per measured
    invocation). Each list length is n_docs * (repeats - warm) when
    discard_warm is True.

    If ``processor_factory`` is provided it should be a callable
    ``(variant) -> processor`` where processor has a ``process(...)``
    method with the same signature as ``PipelineGrievanceProcessor.process``.
    The factory is called once per variant (models warmed once). If it is
    None, fake timings are used (no real processor).
    """
    per_stage_times: dict[str, list[float]] = {k: [] for k in ALL_KEYS}
    per_stage_tickets: dict[str, list[str]] = {k: [] for k in ALL_KEYS}
    per_stage_kinds: dict[str, list[str]] = {k: [] for k in ALL_KEYS}
    temperature_times: dict[str, list[float]] = {"cold": [], "warm": []}
    temperature_tickets: dict[str, list[str]] = {"cold": [], "warm": []}
    has_completed_request = False
    attempts = 0
    completed_attempts = 0
    failures_by_error: defaultdict[str, int] = defaultdict(int)

    # Optionally warm a real processor once per variant
    processor = None
    if processor_factory is not None:
        processor = processor_factory(variant)

    for doc in docs:
        ticket = doc["ticket"]
        kind = _document_kind(doc)
        # Resolve this document's bytes once, here — before the per-repeat
        # loop and its perf_counter() calls below, never inside them. A read
        # inside the timed region would land inside whichever stage runs
        # first (ocr) and inflate its measured latency with disk I/O that
        # has nothing to do with the model (#308). load_staged_documents()
        # hands back document_path instead of pre-read bytes for exactly
        # this reason: reading here, one document at a time, keeps at most
        # one document's bytes resident rather than the whole staged
        # sample. Read once and reuse for every repeat of this document
        # (not re-read per repeat) — still outside the timer either way,
        # but no reason to hit disk more than once for the same file. The
        # fake path (processor is None) never touches document bytes, so
        # the read is skipped entirely there.
        document_bytes = doc.get("document_bytes")
        document_path = doc.get("document_path")
        if document_bytes is None and document_path is not None and processor is not None:
            document_bytes = Path(document_path).read_bytes()
        for r in range(repeats):
            attempts += 1
            is_warm = discard_warm and r == 0
            # Timing path: real processor vs fake
            if processor is not None:
                # Real processor path. Per-stage now comes from the processor's
                # timing sink (janasunani/inference/timing.py) rather than being
                # left not_measured: the sink reports what each stage actually
                # took, so nothing here is synthesized from proportions.
                captured: dict[str, float] = {}
                processor._timing_sink = captured.update  # noqa: SLF001 - benchmark seam
                start = time.perf_counter()
                try:
                    processor.process(
                        grievance_id=f"bench-{ticket}-{r}",
                        ticket_no=ticket,
                        text=doc.get("text"),
                        document_name=doc.get("document_name"),
                        document_bytes=document_bytes,
                        district=doc.get("district"),
                    )
                except Exception as exc:  # noqa: BLE001
                    # Failed attempts are reliability evidence, not latency
                    # samples. Record only the exception type so processor
                    # messages cannot leak grievance content, then continue.
                    failures_by_error[type(exc).__name__] += 1
                    continue
                elapsed = time.perf_counter() - start
                completed = captured.pop("ok", 1.0)
                if completed != 1.0:
                    failures_by_error["IncompleteTimingRecord"] += 1
                    continue
                completed_attempts += 1
                # "Cold" means the first successful request after processor
                # construction, not the first repeat of every document.  The
                # latter would relabel already-warm requests as cold.
                temperature = "warm" if has_completed_request else "cold"
                temperature_times[temperature].append(captured.get(E2E_KEY, elapsed))
                temperature_tickets[temperature].append(ticket)
                has_completed_request = True
                if is_warm:
                    continue
                # Prefer the sink's own e2e; fall back to the outer wall clock
                # if the sink produced nothing (a processor built without one).
                # Record whatever the live path actually ran, creating keys on
                # demand. STAGES above is the *batch* pipeline's vocabulary
                # (format / pii / spam / page_type); the live processor's stages
                # are different (redact / detect_pii / triage / detect_language).
                # Coercing one into the other would mislabel real measurements,
                # so a real run reports its own stage names and the fake path
                # keeps the old ones.
                for stage_name, seconds in captured.items():
                    per_stage_times.setdefault(stage_name, []).append(seconds)
                    per_stage_tickets.setdefault(stage_name, []).append(ticket)
                    per_stage_kinds.setdefault(stage_name, []).append(kind)
                if E2E_KEY not in captured:
                    per_stage_times[E2E_KEY].append(elapsed)
                    per_stage_tickets[E2E_KEY].append(ticket)
                    per_stage_kinds[E2E_KEY].append(kind)
            else:
                # Fake timing path — deterministic, fast, no sleep unless
                # BENCHMARK_FAKE_SLEEP is set (for manual wall-clock checks).
                timings = _fake_process(variant, ticket, r)
                completed_attempts += 1
                temperature = "warm" if has_completed_request else "cold"
                temperature_times[temperature].append(timings.e2e)
                temperature_tickets[temperature].append(ticket)
                has_completed_request = True
                if sleep_fake:
                    # Optionally sleep a tiny fraction so wall-clock is real
                    time.sleep(min(timings.e2e * 0.01, 0.005))
                if is_warm:
                    continue
                per_stage_times[E2E_KEY].append(timings.e2e)
                per_stage_tickets[E2E_KEY].append(ticket)
                per_stage_kinds[E2E_KEY].append(kind)
                for stage in STAGES:
                    per_stage_times[stage].append(timings.per_stage[stage])
                    per_stage_tickets[stage].append(ticket)
                    per_stage_kinds[stage].append(kind)

    # Return merged structure for compute_stage_stats
    return {
        "times": per_stage_times,  # type: ignore[return-value]
        "tickets": per_stage_tickets,  # type: ignore[return-value]
        "kinds": per_stage_kinds,  # type: ignore[return-value]
        "attempts": attempts,
        "completed_attempts": completed_attempts,
        "failed_attempts": attempts - completed_attempts,
        "failures_by_error": dict(sorted(failures_by_error.items())),
        "temperature_times": temperature_times,
        "temperature_tickets": temperature_tickets,
    }


def run_benchmark(
    variant: str = "standard",
    n_text: int = DEFAULT_N_TEXT,
    n_image: int = DEFAULT_N_IMAGE,
    repeats: int = DEFAULT_REPEATS,
    discard_warm: bool = True,
    docs: list[dict[str, Any]] | None = None,
    processor_factory: Callable[[str], Any] | None = None,
    seed: int = 42,
    sleep_fake: bool = False,
    is_fake: bool | None = None,
) -> dict[str, Any]:
    """Run one benchmark variant and return structured results.

    Args:
        variant: one of VALID_VARIANTS
        n_text: number of synthetic text grievances
        n_image: number of synthetic image documents
        repeats: total repeats per document (first discarded if discard_warm)
        discard_warm: whether to discard the first repeat (warm)
        docs: optional pre-built doc list (overrides n_text/n_image)
        processor_factory: optional (variant -> processor) for real timing
        seed: RNG seed for deterministic fixtures
        sleep_fake: if True, sleep a tiny amount per fake call so wall-clock
            is non-zero for manual checks; default False for fast tests.
        is_fake: whether this result is synthetic fake timing (non-publishable).
            If None, inferred from processor_factory is None.

    Returns dict with keys variant, n_docs, repeats, warm_discarded,
    git_sha, timestamp, stages (per-stage stats), is_fake_timing, etc.
    """
    if variant not in VALID_VARIANTS:
        raise ValueError(
            f"unknown variant {variant!r}; valid variants: {sorted(VALID_VARIANTS)}"
        )
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    if n_text < 0 or n_image < 0:
        raise ValueError("n_text and n_image must be >= 0")

    if docs is None:
        docs = synthesize_documents(
            n_text=n_text, n_image=n_image, seed=seed
        )
    n_docs = len(docs)
    if n_docs == 0:
        raise ValueError("no documents to benchmark (n_text + n_image == 0)")

    raw = _measure_with_processor(
        variant=variant,
        docs=docs,
        repeats=repeats,
        discard_warm=discard_warm,
        processor_factory=processor_factory,
        sleep_fake=sleep_fake,
    )
    per_stage_times: dict[str, list[float]] = raw["times"]  # type: ignore[assignment]
    per_stage_tickets: dict[str, list[str]] = raw["tickets"]  # type: ignore[assignment]
    per_stage_kinds: dict[str, list[str]] = raw["kinds"]  # type: ignore[assignment]

    stages_stats: dict[str, dict[str, Any]] = {}
    for key in per_stage_times:
        times = per_stage_times.get(key, [])
        tickets = per_stage_tickets.get(key, [])
        stages_stats[key] = compute_stage_stats(times, tickets)

    timestamp = datetime.now(UTC).isoformat()
    git_sha = _get_git_sha()
    # is_fake is explicit: True when using synthetic timings, False for real processor
    if is_fake is None:
        is_fake = processor_factory is None

    result: dict[str, Any] = {
        "variant": variant,
        "n_docs": n_docs,
        "n_text": n_text,
        "n_image": n_image,
        "repeats": repeats,
        "warm_discarded": discard_warm,
        "n_measured": len(per_stage_times.get(E2E_KEY, [])),
        "attempts": raw["attempts"],
        "completed_attempts": raw["completed_attempts"],
        "failed_attempts": raw["failed_attempts"],
        "failures_by_error": raw["failures_by_error"],
        "timestamp": timestamp,
        "git_sha": git_sha,
        "stages": stages_stats,
        "input_paths": _stats_by_input(per_stage_times, per_stage_tickets, per_stage_kinds),
        "temperature_e2e": {
            temperature: compute_stage_stats(
                raw["temperature_times"][temperature],
                raw["temperature_tickets"][temperature],
            )
            for temperature in ("cold", "warm")
        },
        "temperature_definition": {
            "cold": "first successful request after processor construction",
            "warm": "all subsequent successful requests in the same process",
        },
        "is_fake_timing": bool(is_fake),
        "environment": {
            "python": platform.python_version(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor() or None,
            "logical_cpu_count": os.cpu_count(),
        },
    }
    # Keep raw times under a separate key for callers that want to inspect
    # or re-aggregate differently; not part of the committed latency.json
    # unless the caller explicitly persists it.
    result["_raw"] = {"times": per_stage_times, "tickets": per_stage_tickets}
    return result


def latency_json_payload(
    results: dict[str, dict[str, Any]] | dict[str, Any],
) -> dict[str, Any]:
    """Build the serialisable latency.json payload from one or many results.

    Accepts either a single result dict (from run_benchmark) or a mapping
    variant -> result. Strips the ``_raw`` key and adds a top-level
    ``variants`` wrapper when multiple variants are supplied.

    The output is stable for ``outputs/benchmark/latency.json``.
    """
    # Normalize to variant -> result mapping
    if isinstance(results, dict) and "variant" in results and "stages" in results:
        mapping: dict[str, dict[str, Any]] = {results["variant"]: results}
    else:
        mapping = results  # type: ignore[assignment]

    variants_payload: dict[str, Any] = {}
    for variant, result in mapping.items():
        # Strip _raw
        clean = {k: v for k, v in result.items() if k != "_raw"}
        variants_payload[variant] = clean

    def nonblank(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    def has_cold_and_warm(result: dict[str, Any]) -> bool:
        temperatures = result.get("temperature_e2e")
        return isinstance(temperatures, dict) and all(
            isinstance(temperatures.get(key), dict)
            and temperatures[key].get("n", 0) > 0
            for key in ("cold", "warm")
        )

    def has_clean_full_path_coverage(result: dict[str, Any]) -> bool:
        # This used to hardcode requiring BOTH "text" and "document" paths
        # to have clean e2e coverage, which matched the synthetic default
        # (always mixes text grievances and image documents) but made a
        # --documents-dir run structurally unpublishable: real staged
        # scans are all "document" kind, so there is legitimately no
        # "text" path, and the gate could never pass regardless of how
        # clean the run was. The gate should require clean coverage for
        # whatever input kinds this run actually exercised — every one of
        # them, not a fixed pair. A mixed run (synthetic default) still has
        # to show clean "text" and "document" coverage; a document-only run
        # only has to show it for "document".
        #
        # "unspecified" never counts as coverage, whether or not other
        # kinds are also present: it means at least one measurement's input
        # kind (see _document_kind) could not be determined, which is
        # exactly the provenance gap this gate exists to catch.
        if result.get("failed_attempts") != 0:
            return False
        input_paths = result.get("input_paths")
        if not isinstance(input_paths, dict) or not input_paths:
            return False
        if "unspecified" in input_paths:
            return False
        return all(
            isinstance(stats, dict)
            and isinstance(stats.get("e2e"), dict)
            and stats["e2e"].get("n", 0) > 0
            for stats in input_paths.values()
        )

    publication_ready = bool(variants_payload) and all(
        not result.get("is_fake_timing", True)
        and result.get("n_measured", 0) > 0
        and has_cold_and_warm(result)
        and has_clean_full_path_coverage(result)
        and result.get("attempts")
        == result.get("completed_attempts", 0) + result.get("failed_attempts", 0)
        and isinstance(result.get("benchmark_context"), dict)
        and nonblank(result["benchmark_context"].get("host_label"))
        and nonblank(result["benchmark_context"].get("model_release_id"))
        and nonblank(result.get("git_sha"))
        for result in variants_payload.values()
    )

    # If single variant, also expose top-level stages for backwards compat
    if len(variants_payload) == 1:
        only_variant = next(iter(variants_payload))
        only_result = variants_payload[only_variant]
        # Top-level is that variant's payload plus variants wrapper
        payload: dict[str, Any] = {
            **only_result,
            "schema_version": LATENCY_SCHEMA_VERSION,
            "publication_ready": publication_ready,
            "variants": variants_payload,
        }
    else:
        # Multi-variant file: top-level is variants plus summary
        payload = {
            "schema_version": LATENCY_SCHEMA_VERSION,
            "publication_ready": publication_ready,
            "variants": variants_payload,
            "n_variants": len(variants_payload),
            "timestamp": datetime.now(UTC).isoformat(),
            "git_sha": _get_git_sha(),
        }
    return payload


def write_latency_json(
    results: dict[str, dict[str, Any]] | dict[str, Any],
    output_path: Path | str = DEFAULT_OUTPUT,
) -> Path:
    """Write latency.json to ``output_path`` and return the path."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = latency_json_payload(results)
    # JSON with sorted keys and indent for diffability
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--variant",
        choices=sorted(VALID_VARIANTS),
        default="standard",
        help="Pipeline variant to benchmark (default: standard).",
    )
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=sorted(VALID_VARIANTS),
        default=None,
        help=(
            "Run multiple variants in sequence and write a combined "
            "latency.json with a 'variants' map. When set, --variant is "
            "ignored."
        ),
    )
    parser.add_argument(
        "--n-docs",
        type=int,
        default=None,
        help=(
            "Number of synthetic text grievances (default: 20 when "
            "--documents-dir is not given). Mutually exclusive with "
            "--documents-dir, which determines its own document count."
        ),
    )
    parser.add_argument(
        "--n-image-docs",
        type=int,
        default=None,
        help=(
            "Number of synthetic image documents (default: 10 when "
            "--documents-dir is not given). Mutually exclusive with "
            "--documents-dir, which determines its own document count."
        ),
    )
    parser.add_argument(
        "--documents-dir",
        type=Path,
        default=None,
        help=(
            "Directory of a real staged document sample to benchmark "
            "instead of the synthetic fixture (see load_staged_documents). "
            "Mutually exclusive with --n-docs/--n-image-docs. If the "
            "directory has a sample_manifest.json (written by "
            "janasunani-build-sarvam-sample), it supplies district/slice "
            "and a document-count cross-check."
        ),
    )
    parser.add_argument(
        "--slice",
        default=None,
        help=(
            "Label for the staged sample's slice (e.g. 'Sambalpur/2024'), "
            "recorded in benchmark_context. Only used with --documents-dir; "
            "defaults to the sample_manifest.json 'slice' field, or "
            "'unspecified' if there is no manifest."
        ),
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=DEFAULT_REPEATS,
        help="Repeats per document; first is discarded as warm (default: 3).",
    )
    parser.add_argument(
        "--no-warm-discard",
        action="store_true",
        help="Do not discard the first repeat (measure cold start too).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output path for latency.json (default: outputs/benchmark/latency.json).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="RNG seed for deterministic synthetic fixtures.",
    )
    parser.add_argument(
        "--sleep-fake",
        action="store_true",
        help="Sleep a tiny amount per fake call so wall-clock is non-zero (manual).",
    )
    parser.add_argument(
        "--fake",
        action="store_true",
        help="Use deterministic fake timings (synthetic, not publishable). "
        "By default, real processor is attempted; requires --fake explicitly for CI.",
    )
    parser.add_argument(
        "--host-label",
        default=None,
        help="Stable label for the benchmark host.",
    )
    parser.add_argument(
        "--model-release-id",
        default=None,
        help="Approved release ID, or an explicit development artifact identifier.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    variants = args.variants or [args.variant]
    # Validate repeats
    if args.repeats < 1:
        parser.error("--repeats must be >= 1")

    # --documents-dir (real staged sample) and --n-docs/--n-image-docs
    # (synthetic fixture counts) configure two different document sources;
    # letting both through would silently pick one and ignore the other.
    # --n-docs/--n-image-docs default to None (not DEFAULT_N_TEXT/
    # DEFAULT_N_IMAGE) precisely so this can tell "not passed" from
    # "explicitly passed the default value".
    loaded_docs: list[dict[str, Any]] | None = None
    sample_manifest: dict[str, Any] | None = None
    sample_slice: str | None = None
    sample_digest: str | None = None
    if args.documents_dir is not None:
        if args.n_docs is not None or args.n_image_docs is not None:
            parser.error(
                "--documents-dir is mutually exclusive with --n-docs/"
                "--n-image-docs: the staged sample determines its own "
                "document count, so passing both is contradictory."
            )
        manifest_path = args.documents_dir / "sample_manifest.json"
        if manifest_path.is_file():
            sample_manifest = json.loads(manifest_path.read_text())
        try:
            loaded_docs = load_staged_documents(
                args.documents_dir, manifest=sample_manifest
            )
        except (FileNotFoundError, ValueError) as exc:
            parser.error(str(exc))
        sample_slice = (
            args.slice
            or (sample_manifest or {}).get("slice")
            or "unspecified"
        )
        sample_digest = _document_sample_digest(args.documents_dir, sample_manifest)
    else:
        n_docs = DEFAULT_N_TEXT if args.n_docs is None else args.n_docs
        n_image_docs = DEFAULT_N_IMAGE if args.n_image_docs is None else args.n_image_docs
        if n_docs < 0 or n_image_docs < 0:
            parser.error("--n-docs and --n-image-docs must be >= 0")
        if n_docs + n_image_docs == 0:
            parser.error("at least one of --n-docs or --n-image-docs must be > 0")
        args.n_docs, args.n_image_docs = n_docs, n_image_docs

    discard_warm = not args.no_warm_discard
    if args.repeats == 1 and discard_warm:
        # With repeats=1 and warmup discard on, every observation is the
        # discarded warm one — the harness would silently write an
        # all-zero latency report (mean 0, se 0, n=0) with exit 0.
        parser.error(
            "--repeats must be >= 2 when discarding warmup, "
            "or pass --no-warm-discard"
        )

    # Fake mode is explicit and non-publishable; real mode wires processor.
    # For backward compat with existing tests that call main() without --fake,
    # we fall back to fake when real processor is unavailable, but still mark
    # the result as is_fake_timing=true (non-publishable). Real wiring is
    # tracked as post-demo work (provider registry + live Sarvam path).
    is_fake = args.fake
    if not is_fake and any(variant != "standard" for variant in variants):
        parser.error(
            "real Sarvam variants are not wired into this timing harness; "
            "run only --variant standard or use --fake for harness tests"
        )
    processor_factory: Any = None
    startup: dict[str, float] = {}
    if not is_fake:
        # Real wiring. This used to force is_fake=True unconditionally, which
        # meant every number this harness ever printed came from the constant
        # table in _FAKE_STAGE_MEANS, with or without --fake. A run that cannot
        # build a processor now FAILS rather than silently fabricating: a
        # fabricated latency that looks real is worse than no latency at all.
        try:
            from janasunani.inference.service import build_processor
        except Exception as exc:  # pragma: no cover - env-dependent
            parser.error(
                f"--fake was not passed but the real processor cannot be imported: {exc}. "
                "Pass --fake explicitly to get synthetic timings."
            )

        warmed: dict[str, Any] = {}

        def processor_factory(variant: str) -> Any:  # noqa: ARG001 - one warm processor serves every variant
            if "processor" not in warmed:
                started = time.perf_counter()
                warmed["processor"] = build_processor(timing_sink=lambda _timings: None)
                startup["seconds"] = time.perf_counter() - started
            return warmed["processor"]

    results: dict[str, dict[str, Any]] = {}
    for variant in variants:
        result = run_benchmark(
            variant=variant,
            n_text=0 if loaded_docs is not None else args.n_docs,
            n_image=0 if loaded_docs is not None else args.n_image_docs,
            docs=loaded_docs,
            repeats=args.repeats,
            discard_warm=discard_warm,
            seed=args.seed,
            sleep_fake=args.sleep_fake,
            processor_factory=processor_factory,
            is_fake=is_fake,
        )
        result["processor_startup_seconds"] = startup.get("seconds")
        if loaded_docs is not None:
            # Real-document path: this is the actual defect being fixed —
            # an artifact that says what it ran on. Additive keys only; the
            # synthetic-path dict below (host_label, model_release_id,
            # fixture, execution) is unchanged so existing consumers of
            # those keys and of the exact "fixture" string do not break.
            result["benchmark_context"] = {
                "host_label": args.host_label,
                "model_release_id": args.model_release_id,
                "fixture": (
                    f"real staged document sample "
                    f"({len(loaded_docs)} documents, slice {sample_slice})"
                ),
                "execution": "sequential single-process execution",
                "sample_slice": sample_slice,
                "sample_document_count": len(loaded_docs),
                "sample_digest": sample_digest,
            }
        else:
            result["benchmark_context"] = {
                "host_label": args.host_label,
                "model_release_id": args.model_release_id,
                "fixture": "deterministic synthetic grievances without citizen data",
                "execution": "sequential single-process execution",
            }
        results[variant] = result
        e2e = result["stages"][E2E_KEY]
        print(
            f"[{variant}] n_docs={result['n_docs']} repeats={args.repeats} "
            f"warm_discarded={discard_warm} -> "
            f"e2e mean={e2e['mean_seconds']:.3f}s "
            f"se={e2e['se_seconds']:.3f}s "
            f"p50={e2e['p50']:.3f}s p90={e2e['p90']:.3f}s "
            f"p95={e2e['p95']:.3f}s "
            f"n={e2e['n']} clusters={e2e['n_clusters']}"
        )

    # Write output
    if len(results) == 1:
        output_result: dict[str, Any] | dict[str, dict[str, Any]] = next(
            iter(results.values())
        )
    else:
        output_result = results

    out_path = write_latency_json(output_result, args.output)
    print(f"Wrote {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
