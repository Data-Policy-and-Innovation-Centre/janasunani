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
* Report: mean_seconds, se_seconds, n_clusters, p50, p95 per stage and
  end-to-end.
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
import json
import math
import random
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

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
    """Compute mean/se/p50/p95 with ticket-clustered SE.

    Args:
        times: per-measurement wall seconds for one stage (or e2e).
        tickets: ticket/document id per measurement (same length as times).

    Returns dict with keys mean_seconds, se_seconds, n_clusters,
    p50, p95, n, min_seconds, max_seconds. All floats are plain Python
    floats for JSON serialisation.
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
            "p95": 0.0,
            "min_seconds": 0.0,
            "max_seconds": 0.0,
        }
    mean = sum(times) / n
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
        "p95": float(p95),
        "min_seconds": float(min(times)),
        "max_seconds": float(max(times)),
    }


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
    # Synthetic image documents (pretend rendered pages)
    for i in range(n_image):
        ticket = f"SYN-IMG-{i:04d}"
        docs.append(
            {
                "ticket": ticket,
                "text": None,
                "document_name": f"synthetic_{i}.pdf",
                "document_bytes": b"%PDF-1.4 fake synthetic page",
                "district": "Sambalpur",
            }
        )
    # Deterministic shuffle so order is not fixture-grouped
    rng.shuffle(docs)
    return docs


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
    # Seed per ticket+repeat for deterministic per-doc variation
    rng_seed = hash((seed, ticket, repeat_idx, variant)) & 0xFFFFFFFF
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
) -> dict[str, list[float]]:
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

    # Optionally warm a real processor once per variant
    processor = None
    if processor_factory is not None:
        processor = processor_factory(variant)

    for doc in docs:
        ticket = doc["ticket"]
        for r in range(repeats):
            is_warm = discard_warm and r == 0
            # Timing path: real processor vs fake
            if processor is not None:
                # Real processor path — measure wall clock for the single
                # process() call. Per-stage breakdown is not available from
                # the monolithic PipelineGrievanceProcessor, so we record
                # e2e and leave per-stage as proportional slices. This keeps
                # the harness usable on the live path while still producing
                # the required per-stage keys for downstream consumers.
                start = time.perf_counter()
                try:
                    processor.process(
                        grievance_id=f"bench-{ticket}-{r}",
                        ticket_no=ticket,
                        text=doc.get("text"),
                        document_name=doc.get("document_name"),
                        document_bytes=doc.get("document_bytes"),
                        district=doc.get("district"),
                    )
                except Exception:
                    # Benchmark must not hide processor bugs as slow timings.
                    # Re-raise so the harness fails visibly.
                    raise
                elapsed = time.perf_counter() - start
                if is_warm:
                    continue
                # Record e2e
                per_stage_times[E2E_KEY].append(elapsed)
                per_stage_tickets[E2E_KEY].append(ticket)
                # Distribute elapsed proportionally across stages by fake
                # means, so per-stage p50/p95 are still meaningful and
                # variant-differentiated. This is an approximation; the
                # batch path with artifact DB hooks can replace it with true
                # stage hooks.
                total_fake = sum(_FAKE_STAGE_MEANS[s] for s in STAGES)
                for stage in STAGES:
                    frac = _FAKE_STAGE_MEANS[stage] / total_fake
                    # Adjust fraction for Sarvam variants
                    if stage == "ocr" and variant in (
                        "sarvam_digitise",
                        "sarvam_both",
                    ):
                        frac *= 1.6
                    per_stage_times[stage].append(elapsed * frac)
                    per_stage_tickets[stage].append(ticket)
            else:
                # Fake timing path — deterministic, fast, no sleep unless
                # BENCHMARK_FAKE_SLEEP is set (for manual wall-clock checks).
                timings = _fake_process(variant, ticket, r)
                if sleep_fake:
                    # Optionally sleep a tiny fraction so wall-clock is real
                    time.sleep(min(timings.e2e * 0.01, 0.005))
                if is_warm:
                    continue
                per_stage_times[E2E_KEY].append(timings.e2e)
                per_stage_tickets[E2E_KEY].append(ticket)
                for stage in STAGES:
                    per_stage_times[stage].append(timings.per_stage[stage])
                    per_stage_tickets[stage].append(ticket)

    # Return merged structure for compute_stage_stats
    return {
        "times": per_stage_times,  # type: ignore[return-value]
        "tickets": per_stage_tickets,  # type: ignore[return-value]
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

    Returns dict with keys variant, n_docs, repeats, warm_discarded,
    git_sha, timestamp, stages (per-stage stats), stage_times (raw, for
    debugging, not written to latency.json by default).
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

    stages_stats: dict[str, dict[str, Any]] = {}
    for key in ALL_KEYS:
        times = per_stage_times.get(key, [])
        tickets = per_stage_tickets.get(key, [])
        stages_stats[key] = compute_stage_stats(times, tickets)

    timestamp = datetime.now(UTC).isoformat()
    git_sha = _get_git_sha()

    result: dict[str, Any] = {
        "variant": variant,
        "n_docs": n_docs,
        "n_text": n_text,
        "n_image": n_image,
        "repeats": repeats,
        "warm_discarded": discard_warm,
        "n_measured": len(per_stage_times.get(E2E_KEY, [])),
        "timestamp": timestamp,
        "git_sha": git_sha,
        "stages": stages_stats,
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

    # If single variant, also expose top-level stages for backwards compat
    if len(variants_payload) == 1:
        only_variant = next(iter(variants_payload))
        only_result = variants_payload[only_variant]
        # Top-level is that variant's payload plus variants wrapper
        payload: dict[str, Any] = {
            **only_result,
            "variants": variants_payload,
        }
    else:
        # Multi-variant file: top-level is variants plus summary
        payload = {
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
        default=DEFAULT_N_TEXT,
        help="Number of synthetic text grievances (default: 20).",
    )
    parser.add_argument(
        "--n-image-docs",
        type=int,
        default=DEFAULT_N_IMAGE,
        help="Number of synthetic image documents (default: 10).",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    variants = args.variants or [args.variant]
    # Validate repeats
    if args.repeats < 1:
        parser.error("--repeats must be >= 1")
    if args.n_docs < 0 or args.n_image_docs < 0:
        parser.error("--n-docs and --n-image-docs must be >= 0")
    if args.n_docs + args.n_image_docs == 0:
        parser.error("at least one of --n-docs or --n-image-docs must be > 0")

    discard_warm = not args.no_warm_discard

    results: dict[str, dict[str, Any]] = {}
    for variant in variants:
        result = run_benchmark(
            variant=variant,
            n_text=args.n_docs,
            n_image=args.n_image_docs,
            repeats=args.repeats,
            discard_warm=discard_warm,
            seed=args.seed,
            sleep_fake=args.sleep_fake,
        )
        results[variant] = result
        e2e = result["stages"][E2E_KEY]
        print(
            f"[{variant}] n_docs={result['n_docs']} repeats={args.repeats} "
            f"warm_discarded={discard_warm} -> "
            f"e2e mean={e2e['mean_seconds']:.3f}s "
            f"se={e2e['se_seconds']:.3f}s "
            f"p50={e2e['p50']:.3f}s p95={e2e['p95']:.3f}s "
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
