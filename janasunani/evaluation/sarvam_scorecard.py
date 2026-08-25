"""Sarvam Vision vs pytesseract scorecard — paired design per #127, fallback per #84.

Design decisions (frozen before submission, see #127):

* Paired: both systems process the same pages. Page difficulty is the dominant
  variance component and pairing removes it rather than averaging over it.
* Primary outcome (pre-registered): difference in **category accuracy** on the
  paired sample, because it has a referee (recorded category on the ticket)
  and the transcription arm has none. Everything else is exploratory and
  labelled as such. Per-class results are exploratory; the headline is the
  overall difference.
* Report the confidence interval on the **difference**, not two marginal
  intervals. Two overlapping CIs do not imply no difference for paired data.
* Cluster standard errors by **ticket (document)**, not by page. Pages from
  one complaint share handwriting, scan quality and subject matter.
  Treating 200 pages from 40 documents as n=200 understates SE by ~sqrt(5).
* Handwritten vs printed reported **separately** (issue #84). Sarvam documents
  Vision as strong on printed text/tables/layout; handwriting is claimed but
  has no published handwriting-specific number. A blended number would hide the
  exact question the corpus turns on. Split on the ``handwritten`` column
  (``yes`` = handwritten, ``no`` = printed, ``partial``/NULL = separate bucket).
* Language split: Odia and English reported separately (issue #84). The corpus
  is Odia-heavy; a blended figure hides the Odia question entirely.
* Transcription arm: if no hand-transcribed ground truth is available (the
  current state per DELIVERY.md — no owner was named), report **divergence only**
  — how the two systems differ from each other — with no verdict on which is
  right, and drop the accuracy row from DELIVERY.md Table 2. This module flags
  that condition explicitly (``transcription_available`` / ``needs_transcription_sample``).
* Markdown-to-text normaliser is frozen **before any output is read** (#84,
  #127). Both engines are compared after the same normalisation so markdown
  headedness does not move the headline. Version is recorded in the report.

Verification: recorded responses only. Tests supply synthetic page records and
recorded Sarvam markdown fixtures; no live network call is made.
"""

from __future__ import annotations

import html
import json
import random
import re
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from janasunani.config import DEMO_SLICE_DISTRICT, DEMO_SLICE_LABEL, DEMO_SLICE_YEAR
from janasunani.evaluation.stats import (
    clustered_se as _clustered_se,
    critical_value,
    paired_difference as paired_difference,
    required_pages_mcnemar as required_pages_mcnemar,
)

NORMALIZER_VERSION = "1.0"

# Benchmark sampling — few hundred pages from Sambalpur/2024 slice,
# stratified handwritten/printed per #127/#84. Cost at 50p/page digitise
# is few-hundred rupees; size is powered for ~10-point category gaps.
BENCHMARK_SAMPLE_SIZE = 300
BENCHMARK_MIN_SAMPLE = 200
BENCHMARK_MAX_SAMPLE = 400
# Deterministic seed so the same Sambalpur/2024 slice sample is reproducible
# across runs; the stratification, not the randomness, is the control.
BENCHMARK_SAMPLE_SEED = 42

# ---------------------------------------------------------------------------
# Normaliser — frozen before output is read
# ---------------------------------------------------------------------------

_MD_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_MD_BOLD_RE = re.compile(r"\*\*([^\*]+)\*\*")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*([^\*]+)\*(?!\*)")
_MD_CODE_RE = re.compile(r"`([^`]+)`")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^\)]+\)")
_MD_HR_RE = re.compile(r"^\s*(?:---|\*\*\*|___)\s*$", re.MULTILINE)


def normalize_text(text: str | None) -> str:
    """Normalise markdown or plain text to a comparable plain form.

    Frozen at ``NORMALIZER_VERSION``. Steps:
      1. Unicode NFC, strip zero-width chars.
      2. Remove markdown headings, bold, italic, code, links, images, rules,
         and table pipes.
      3. Replace pipes and table separators with spaces.
      4. Collapse whitespace, strip.
    The aggressiveness of this step moves the headline number, so it is
    versioned and must not be changed after outputs are read.
    """
    if not text:
        return ""
    # NFC + remove zero-width
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")

    # images -> alt text
    text = _MD_IMAGE_RE.sub(r"\1", text)
    # links -> link text
    text = _MD_LINK_RE.sub(r"\1", text)
    # code, bold, italic
    text = _MD_CODE_RE.sub(r"\1", text)
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_ITALIC_RE.sub(r"\1", text)
    # headings: remove leading #'s but keep text
    lines = []
    for line in text.splitlines():
        line = _MD_HEADING_RE.sub("", line)
        # table rows: pipes -> spaces
        if "|" in line:
            line = line.replace("|", " ")
        # horizontal rules -> blank
        if _MD_HR_RE.match(line):
            line = ""
        lines.append(line)
    text = "\n".join(lines)
    # collapse whitespace (including newlines to single space for comparison)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def unescape_label(label: str | None) -> str | None:
    """Return *label* in its readable form, undoing HTML entity escaping.

    Some ``complaints.category`` values reached the lake double-escaped:
    ``Scheme & Benefits`` is stored as ``Scheme &amp;amp; Benefits``. Unescaping
    is applied repeatedly until it reaches a fixed point so both single and
    double escaping resolve, with a bounded loop so a pathological value cannot
    spin. Returns ``None`` for a missing or blank label.
    """
    if label is None:
        return None
    text = unicodedata.normalize("NFC", str(label))
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def category_key(label: str | None) -> str | None:
    """Comparison key for a category label, or ``None`` if there is no label.

    Category accuracy compares a stored gold label against a model's answer.
    The schema enum offers the readable form (``Scheme & Benefits``), so an
    exact-equality comparison against the escaped stored form would score every
    correct prediction for that category as wrong. Comparisons must go through
    this function rather than ``==`` on the raw strings.

    ``None`` never equals ``None`` for scoring purposes: callers filter on a
    present gold label first, so a missing prediction cannot match.
    """
    cleaned = unescape_label(label)
    return cleaned.casefold() if cleaned else None


def _labels_match(gold: str | None, predicted: str | None) -> bool:
    """True when *predicted* is the same category as *gold*, ignoring escaping."""
    gold_key = category_key(gold)
    return gold_key is not None and gold_key == category_key(predicted)


# ---------------------------------------------------------------------------
# Sample model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PageRecord:
    """One page that both engines processed.

    ``handwritten`` follows the pipeline ``pages.handwritten`` column:
    ``yes`` / ``no`` / ``partial`` / None. ``no`` is treated as printed.
    ``language`` is a canonical value like ``English`` / ``Odia`` / None.
    ``transcription`` is the hand-transcribed ground truth if available;
    ``None`` means no referee (divergence-only mode per #84).
    ``pipeline_category`` / ``sarvam_category`` / ``gold_category`` are for
    the category arm (issue #127) at ticket level; they may be repeated per
    page for convenience and are de-duplicated by ticket when scored.
    ``sarvam_summary`` / ``pipeline_summary`` are the exploratory summary arm
    (Extract ``summary`` field vs BART output); no gold referee, divergence
    only, clustered by ticket like the OCR arm.
    """

    ticket: str
    page_id: str
    handwritten: str | None = None
    language: str | None = None
    pytesseract_text: str = ""
    sarvam_markdown: str = ""
    transcription: str | None = None
    pipeline_category: str | None = None
    sarvam_category: str | None = None
    gold_category: str | None = None
    sarvam_summary: str | None = None
    pipeline_summary: str | None = None

    def handwritten_bucket(self) -> str:
        if self.handwritten == "yes":
            return "handwritten"
        if self.handwritten == "no":
            return "printed"
        if self.handwritten == "partial":
            return "mixed"
        return "unknown"

    def language_bucket(self) -> str:
        if not self.language:
            return "unknown"
        low = self.language.strip().lower()
        if "odia" in low or "oriya" in low or "od-" in low:
            return "Odia"
        if "english" in low or low == "en":
            return "English"
        return self.language.strip()


# ---------------------------------------------------------------------------
# Paired estimator with ticket-clustered SE
# ---------------------------------------------------------------------------
#
# ``_clustered_se``, ``paired_difference`` and ``required_pages_mcnemar`` now
# live in ``janasunani.evaluation.stats`` (see that module's docstring for the
# two defects fixed on the move: a hardcoded z-lookup that silently returned
# 1.96 for any alpha outside {0.05, 0.01, 0.10}, and no small-cluster t
# correction). ``_clustered_se`` is re-exported here under its original
# private name — via the ``import ... as`` above — so this module's own call
# sites and ``paired_difference``/``required_pages_mcnemar`` are re-exported
# by their public names so ``tests/test_sarvam_scorecard.py`` does not churn.


def divergence_rate(
    pytesseract_texts: list[str],
    sarvam_markdowns: list[str],
    clusters: list[str],
    alpha: float = 0.05,
) -> dict[str, float]:
    """Divergence rate: share of pages where normalised texts differ.

    Used when no transcription referee exists (issue #84 fallback). Reports
    a descriptive disagreement rate with ticket-clustered CI, not a verdict.

    Critical value comes from ``stats.critical_value`` at
    ``df = n_clusters - 1`` (falling back to the normal quantile for 0 or 1
    clusters) instead of the previously hardcoded ``z=1.96`` — the same
    small-cluster t correction applied everywhere else a clustered SE backs
    a CI in this module; see ``stats.py``. This widens the reported interval
    versus previously published numbers, which is intended.
    """
    if not (len(pytesseract_texts) == len(sarvam_markdowns) == len(clusters)):
        raise ValueError("inputs must have equal length")
    n = len(pytesseract_texts)
    if n == 0:
        return {"rate": 0.0, "se": 0.0, "ci_low": 0.0, "ci_high": 0.0, "n": 0, "n_clusters": 0}
    disagreements = []
    for a, b in zip(pytesseract_texts, sarvam_markdowns):
        disagreements.append(0 if normalize_text(a) == normalize_text(b) else 1)
    rate = sum(disagreements) / n
    # SE for a proportion with clustering: treat disagreements as 1/0 and
    # apply clustered SE for the mean.
    se = _clustered_se([float(x) for x in disagreements], clusters)
    n_clusters = len(set(clusters))
    z = critical_value(alpha, df=(n_clusters - 1 if n_clusters > 1 else None))
    return {"rate": rate, "se": se, "ci_low": rate - z * se, "ci_high": rate + z * se, "n": n, "n_clusters": n_clusters}


# ``summary_divergence`` was byte-identical logic to ``divergence_rate`` with
# a different docstring (comparing Sarvam Extract ``summary`` vs pipeline
# BART ``summary`` instead of OCR text) — folded into one implementation.
# ``None`` still becomes "" via ``normalize_text``'s own None handling, so a
# missing summary on one side still counts as divergence, matching the old
# behaviour exactly.
summary_divergence = divergence_rate


# ---------------------------------------------------------------------------
# Sampling — stratified few-hundred-page sample from Sambalpur/2024 (few hundred)
# ---------------------------------------------------------------------------

def stratified_sample(
    pages: list[PageRecord],
    n: int = BENCHMARK_SAMPLE_SIZE,
    seed: int = BENCHMARK_SAMPLE_SEED,
) -> list[PageRecord]:
    """Stratified sample of *n* pages, balanced handwritten vs printed.

    The Sambalpur/2024 slice is the demo source; its handwritten share is
    not 50/50 in the wild, but a braided sample ensures both surfaces are
    reportable separately (per #84). The implementation is deterministic
    (seeded) so the same slice produces the same audit-cost sample across
    runs.

    Stratification key is PageRecord.handwritten_bucket(). Buckets with no
    members are omitted; allocation is proportional to bucket size, with at
    least one per present bucket when n permits. Within each bucket the
    selection is a seeded shuffle. If n >= len(pages), returns a shuffled
    copy.

    Few-hundred-page invariant: callers should use 200–400; the function
    accepts any positive n but the report flags compliance separately.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if not pages:
        return []
    if n >= len(pages):
        rng = random.Random(seed)
        shuffled = list(pages)
        rng.shuffle(shuffled)
        return shuffled
    # bucket by handwritten status
    buckets: dict[str, list[PageRecord]] = defaultdict(list)
    for p in pages:
        buckets[p.handwritten_bucket()].append(p)
    # proportional allocation
    total = len(pages)
    # initial proportional floor
    allocation: dict[str, int] = {}
    remaining = n
    # sort buckets by size descending for stable allocation of remainders
    sorted_buckets = sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True)
    for bucket_name, members in sorted_buckets:
        # at least 1 per present bucket when possible
        prop = len(members) / total
        count = max(1, int(round(prop * n))) if n >= len(buckets) else int(round(prop * n))
        # clamp to bucket size and remaining
        count = min(count, len(members), remaining - (len(sorted_buckets) - len(allocation) - 1))
        # ensure at least 1 if possible
        if count < 1 and len(members) > 0 and remaining > 0:
            count = 1
        allocation[bucket_name] = count
        remaining -= count
    # adjust for rounding drift
    # if we overshot or undershot due to rounding, fix by moving 1s
    while remaining != 0 and allocation:
        # find bucket that can give or take
        if remaining > 0:
            # give to largest bucket that still has headroom
            for bucket_name, members in sorted_buckets:
                if allocation[bucket_name] < len(members):
                    allocation[bucket_name] += 1
                    remaining -= 1
                    if remaining == 0:
                        break
            if remaining > 0:
                break  # no headroom
        else:
            # remaining < 0 : take from largest allocation
            for bucket_name, _ in sorted(sorted_buckets, key=lambda kv: allocation[kv[0]], reverse=True):
                if allocation[bucket_name] > 1:
                    allocation[bucket_name] -= 1
                    remaining += 1
                    if remaining == 0:
                        break
            if remaining < 0:
                break
    rng = random.Random(seed)
    sampled: list[PageRecord] = []
    for bucket_name, members in buckets.items():
        count = allocation.get(bucket_name, 0)
        if count <= 0:
            continue
        shuffled = list(members)
        rng.shuffle(shuffled)
        sampled.extend(shuffled[:count])
    # final shuffle so output is not bucket-grouped
    rng.shuffle(sampled)
    return sampled


def sample_compliance(n_pages: int, slice_label: str | None = None) -> dict[str, Any]:
    """Whether the sample size is the intended few hundred (200–400)."""
    sl = slice_label or DEMO_SLICE_LABEL
    # Derive district/year from slice label when possible
    if "/" in sl:
        try:
            d, y_s = sl.split("/", 1)
            sd = d.strip()
            sy = int(y_s.strip())
        except Exception:  # noqa: BLE001
            sd = DEMO_SLICE_DISTRICT
            sy = DEMO_SLICE_YEAR
    else:
        sd = DEMO_SLICE_DISTRICT
        sy = DEMO_SLICE_YEAR
    return {
        "n_pages": n_pages,
        "is_few_hundred": BENCHMARK_MIN_SAMPLE <= n_pages <= BENCHMARK_MAX_SAMPLE,
        "recommended": BENCHMARK_SAMPLE_SIZE,
        "min": BENCHMARK_MIN_SAMPLE,
        "max": BENCHMARK_MAX_SAMPLE,
        "slice": sl,
        "slice_district": sd,
        "slice_year": sy,
    }


# ---------------------------------------------------------------------------
# Per-category accuracy — the spread that headlines hide
# ---------------------------------------------------------------------------

def per_category_table(pages: list[PageRecord]) -> dict[str, dict[str, Any]]:
    """Per-category accuracy for pipeline vs Sarvam, grouped by gold label.

    One row per gold_category value (e.g. Police 0.85 vs Welfare 0.51).
    De-duplicated by ticket like the headline metric (one category per
    ticket). Tickets without a gold label are omitted.

    Returns mapping category -> {n_tickets, pipeline_accuracy,
    sarvam_accuracy, pipeline_correct, sarvam_correct, difference}.
    """
    ticket_gold: dict[str, str | None] = {}
    ticket_pipe: dict[str, str | None] = {}
    ticket_sarvam: dict[str, str | None] = {}
    for p in pages:
        for d, val in [
            (ticket_gold, p.gold_category),
            (ticket_pipe, p.pipeline_category),
            (ticket_sarvam, p.sarvam_category),
        ]:
            if p.ticket not in d:
                d[p.ticket] = val
            elif d[p.ticket] is None and val is not None:
                d[p.ticket] = val
    # Group tickets by gold category. The row label is the readable form, so
    # escaped and unescaped spellings of one category are a single row.
    groups: dict[str, list[str]] = defaultdict(list)
    for ticket, gold in ticket_gold.items():
        readable = unescape_label(gold)
        if readable is not None:
            groups[readable].append(ticket)
    out: dict[str, dict[str, Any]] = {}
    for category in sorted(groups):
        tickets = groups[category]
        pipe_correct = [1 if _labels_match(category, ticket_pipe.get(t)) else 0 for t in tickets]
        sarv_correct = [1 if _labels_match(category, ticket_sarvam.get(t)) else 0 for t in tickets]
        pipe_acc = sum(pipe_correct) / len(pipe_correct) if pipe_correct else 0.0
        sarv_acc = sum(sarv_correct) / len(sarv_correct) if sarv_correct else 0.0
        out[category] = {
            "n_tickets": len(tickets),
            "pipeline_correct": sum(pipe_correct),
            "sarvam_correct": sum(sarv_correct),
            "pipeline_accuracy": pipe_acc,
            "sarvam_accuracy": sarv_acc,
            "difference": sarv_acc - pipe_acc,
        }
    return out


# ---------------------------------------------------------------------------
# Transliteration probe — romanized Odia if present (Phase 17 → Phase 21)
# ---------------------------------------------------------------------------

_ODIA_SCRIPT_RE = re.compile(r"[\u0B00-\u0B7F]")

def is_romanized_odia_candidate(page: PageRecord) -> bool:
    """Heuristic for a page that is Odia content in Latin script.

    True when the page's language signals Odia but its text has Latin
    without Odia script — i.e. romanized Odia that transliteration would
    need to normalize before hashing/embedding. English pages are not
    candidates; only Odia-language pages rendered in Latin.
    """
    lang = (page.language or "").lower()
    is_odia_lang = "odia" in lang or "oriya" in lang or "od-in" in lang
    if not is_odia_lang:
        return False
    text = page.pytesseract_text or page.sarvam_markdown or ""
    if not text.strip():
        return False
    has_latin = bool(re.search(r"[A-Za-z]", text))
    has_odia = bool(_ODIA_SCRIPT_RE.search(text))
    # romanized = Odia language but latin script, no native script
    return has_latin and not has_odia


def transliteration_probe(pages: list[PageRecord]) -> dict[str, Any]:
    """Probe for romanized Odia presence and the action it triggers.

    If any page is a romanized-Odia candidate, the report notes that a
    Sarvam transliteration probe (od-IN, 1000 chars/chunk) would be the
    next step; otherwise it is not applicable. No network call is made
    here — the probe is a corpus-conditional recommendation, and the actual
    transliteration goes through janasunani/egress/sarvam.py with its audit
    log and kill switch.
    """
    romanized = [p for p in pages if is_romanized_odia_candidate(p)]
    return {
        "n_total": len(pages),
        "n_romanized_candidates": len(romanized),
        "probe_applicable": len(romanized) > 0,
        "romanized_ticket_ids": [p.ticket for p in romanized[:5]],
        "note": (
            "If romanized Odia present, run Sarvam transliteration "
            "(source en-IN, target od-IN, 1000 chars/chunk) via "
            "janasunani/egress/sarvam.py (authorized-external, audit-logged, "
            "kill-switch to dpic-infra IndicXlit) before hashing/embedding."
            if romanized
            else "No romanized Odia detected in this sample; transliteration probe not applicable."
        ),
    }


# ---------------------------------------------------------------------------
# Scorecard assembly
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScorecardReport:
    """Complete scorecard artifact.

    ``transcription_available`` is False when every page has ``transcription is None``,
    in which case ``transcription_accuracy`` is None and the consumer must report
    divergence only (per DELIVERY.md fallback). ``needs_transcription_sample`` flags
    that a hand-transcribed sample must be commissioned for a future accuracy
    comparison.
    """

    normalizer_version: str
    primary_outcome: str
    transcription_available: bool
    needs_transcription_sample: bool
    paired_design: bool
    n_pages: int
    n_tickets: int
    category: dict[str, Any] | None
    transcription_accuracy: dict[str, Any] | None
    transcription_divergence: dict[str, Any]
    by_handwritten: dict[str, dict[str, Any]]
    by_language: dict[str, dict[str, Any]]
    notes: str
    # Phase 17 extensions — per-category spread, transliteration probe,
    # and sample compliance (few-hundred, stratified from Sambalpur/2024).
    # Optional with defaults so existing callers stay green.
    per_category: dict[str, Any] | None = None
    transliteration_probe_result: dict[str, Any] | None = None
    sample_info: dict[str, Any] | None = None
    # Unit E — summary divergence exploratory (Sarvam Extract summary vs BART)
    summary_divergence: dict[str, Any] | None = None
    # Slice + arm for correct rendering (P2 slice label, P1 arm-aware OCR/category)
    slice_label: str = DEMO_SLICE_LABEL
    arm: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PRIMARY_OUTCOME_LABEL = (
    "Category accuracy difference (Sarvam Extract vs pipeline categorizer) "
    "on the paired sample, ticket-clustered 95% CI — the headline. "
    "Transcription and per-class category are exploratory."
)


def build_scorecard(
    pages: list[PageRecord],
    slice_label: str | None = None,
    arm: str | None = None,
) -> ScorecardReport:
    """Build the full scorecard from a frozen sample.

    * Every page must have been processed by **both** engines (paired).
    * ``handwritten`` and ``language`` splits are always reported.
    * If no page carries ``transcription``, accuracy is not computable and
      ``transcription_divergence`` is the only transcription result.
    * ``arm`` controls which headline metrics are measured: ``digitise`` hides
      category, ``extract`` hides OCR divergence, ``both`` measures both.
    * ``slice_label`` is the selected slice (e.g. Ganjam/2024) for sample_info
      and rendering; defaults to DEMO_SLICE_LABEL.
    """
    resolved_slice = slice_label or DEMO_SLICE_LABEL
    n_pages = len(pages)
    n_tickets = len({p.ticket for p in pages})

    # Category arm — paired, ticket-clustered. De-duplicate by ticket for
    # category (one category per ticket/document, not per page).
    # If a ticket appears on multiple pages, its category should be consistent;
    # we take the first non-None and require consistency (warn if not).
    ticket_gold: dict[str, str | None] = {}
    ticket_pipe: dict[str, str | None] = {}
    ticket_sarvam: dict[str, str | None] = {}
    for p in pages:
        for d, val in [
            (ticket_gold, p.gold_category),
            (ticket_pipe, p.pipeline_category),
            (ticket_sarvam, p.sarvam_category),
        ]:
            if p.ticket not in d:
                d[p.ticket] = val
            elif d[p.ticket] is None and val is not None:
                d[p.ticket] = val
            elif val is not None and d[p.ticket] is not None and d[p.ticket] != val:
                # Inconsistent label for same ticket — keep first, note later
                pass

    # Only tickets with a gold label contribute to paired accuracy
    # Arm-aware: digitise-only does not score category (gold Category requires Extract)
    if arm == "digitise":
        category_result = None
        cat_tickets = []
    else:
        cat_tickets = [t for t in ticket_gold if ticket_gold[t] is not None]
        cat_clusters = cat_tickets  # one per ticket
        if cat_tickets:
            pipe_correct = [
                1 if _labels_match(ticket_gold[t], ticket_pipe.get(t)) else 0 for t in cat_tickets
            ]
            sarvam_correct = [
                1 if _labels_match(ticket_gold[t], ticket_sarvam.get(t)) else 0 for t in cat_tickets
            ]
            pipe_rate = sum(pipe_correct) / len(pipe_correct) if pipe_correct else 0.0
            sarvam_rate = sum(sarvam_correct) / len(sarvam_correct) if sarvam_correct else 0.0
            diff = paired_difference(sarvam_correct, pipe_correct, cat_clusters)
            category_result: dict[str, Any] | None = {
                "n_tickets": len(cat_tickets),
                "pipeline_accuracy": pipe_rate,
                "sarvam_accuracy": sarvam_rate,
                "difference": diff["diff"],  # sarvam - pipeline, positive favours sarvam
                "se": diff["se"],
                "ci_low": diff["ci_low"],
                "ci_high": diff["ci_high"],
                "n_clusters": diff["n_clusters"],
                "interpretation": "difference + CI is the result; marginal rates are description (#127)",
            }
        else:
            category_result = None
            cat_tickets = []

    # Transcription arm — arm-aware: extract-only does not measure OCR
    transcription_available = any(p.transcription is not None for p in pages)
    if arm == "extract":
        div: dict[str, Any] = {
            "rate": 0.0,
            "se": 0.0,
            "ci_low": 0.0,
            "ci_high": 0.0,
            "n": 0,
            "n_clusters": 0,
            "not_measured": True,
            "reason": "Extract-only arm — Digitise not run; OCR divergence not measured (run with --arm digitise or both).",
        }
    else:
        # divergence always computable when OCR arm is active
        div = divergence_rate(
            [p.pytesseract_text for p in pages],
            [p.sarvam_markdown for p in pages],
            [p.ticket for p in pages],
        )
    # accuracy only if ground truth exists
    if transcription_available:
        # For pages without transcription, exclude from accuracy comparison
        pages_with_gt = [p for p in pages if p.transcription is not None]
        norm_gt = [normalize_text(p.transcription) for p in pages_with_gt]
        norm_pipe = [normalize_text(p.pytesseract_text) for p in pages_with_gt]
        norm_sarv = [normalize_text(p.sarvam_markdown) for p in pages_with_gt]
        pipe_correct_t = [1 if a == b else 0 for a, b in zip(norm_pipe, norm_gt)]
        sarv_correct_t = [1 if a == b else 0 for a, b in zip(norm_sarv, norm_gt)]
        pipe_acc = sum(pipe_correct_t) / len(pipe_correct_t) if pipe_correct_t else 0.0
        sarv_acc = sum(sarv_correct_t) / len(sarv_correct_t) if sarv_correct_t else 0.0
        t_diff = paired_difference(sarv_correct_t, pipe_correct_t, [p.ticket for p in pages_with_gt])
        transcription_accuracy: dict[str, Any] | None = {
            "n_pages": len(pages_with_gt),
            "pipeline_accuracy": pipe_acc,
            "sarvam_accuracy": sarv_acc,
            "difference": t_diff["diff"],
            "se": t_diff["se"],
            "ci_low": t_diff["ci_low"],
            "ci_high": t_diff["ci_high"],
            "n_clusters": t_diff["n_clusters"],
        }
    else:
        transcription_accuracy = None

    # Splits — divergence split always; accuracy split only if available
    # When arm == extract, OCR splits are not measured (Digitise not run)
    def _split_metrics(key_fn) -> dict[str, dict[str, Any]]:
        buckets: dict[str, list[PageRecord]] = defaultdict(list)
        for p in pages:
            buckets[key_fn(p)].append(p)
        out: dict[str, dict[str, Any]] = {}
        for bucket, bucket_pages in sorted(buckets.items()):
            if arm == "extract":
                b_div: dict[str, Any] = {
                    "rate": 0.0,
                    "se": 0.0,
                    "ci_low": 0.0,
                    "ci_high": 0.0,
                    "n": 0,
                    "n_clusters": 0,
                    "not_measured": True,
                    "reason": "Extract-only arm",
                }
            else:
                b_div = divergence_rate(
                    [x.pytesseract_text for x in bucket_pages],
                    [x.sarvam_markdown for x in bucket_pages],
                    [x.ticket for x in bucket_pages],
                )
            entry: dict[str, Any] = {"n_pages": len(bucket_pages), "divergence": b_div}
            if transcription_available:
                gt_pages = [x for x in bucket_pages if x.transcription is not None]
                if gt_pages:
                    ng = [normalize_text(x.transcription) for x in gt_pages]
                    np_ = [normalize_text(x.pytesseract_text) for x in gt_pages]
                    ns = [normalize_text(x.sarvam_markdown) for x in gt_pages]
                    pc = [1 if a == b else 0 for a, b in zip(np_, ng)]
                    sc_ = [1 if a == b else 0 for a, b in zip(ns, ng)]
                    d = paired_difference(sc_, pc, [x.ticket for x in gt_pages])
                    entry["accuracy_diff"] = d
                    entry["pipeline_accuracy"] = sum(pc) / len(pc) if pc else 0.0
                    entry["sarvam_accuracy"] = sum(sc_) / len(sc_) if sc_ else 0.0
            out[bucket] = entry
        return out

    by_handwritten = _split_metrics(lambda p: p.handwritten_bucket())
    by_language = _split_metrics(lambda p: p.language_bucket())

    needs_sample = not transcription_available
    notes_parts = [
        "Paired design: both engines on the same pages; "
        "CI on the difference with ticket-clustered SE (per #127).",
        f"Primary outcome: {PRIMARY_OUTCOME_LABEL}",
        "Handwritten vs printed and Odia vs English reported separately (per #84); "
        "a blended number would hide the handwriting question the corpus turns on.",
        f"Normaliser frozen at version {NORMALIZER_VERSION} before output read (#84, #127).",
    ]
    if needs_sample:
        notes_parts.append(
            "Flag: No hand-transcribed sample is available (unassigned per DELIVERY.md / #53). "
            "The transcription accuracy row is therefore DIVERGENCE ONLY — how the two "
            "systems differ, with no verdict on which is right — and the accuracy row is "
            "dropped from DELIVERY.md Table 2. Commission ~50 pages (printed + handwritten, "
            "Odia + English) hand-transcribed to make the accuracy comparison reportable. "
            "Transcription cannot be produced by an agent because it is the ground truth "
            "an agent would be measured against."
        )
    # Summary divergence — exploratory, no gold referee
    pages_with_summaries = [p for p in pages if p.sarvam_summary is not None and p.pipeline_summary is not None]
    if pages_with_summaries:
        summ_div = summary_divergence(
            [p.sarvam_summary for p in pages_with_summaries],
            [p.pipeline_summary for p in pages_with_summaries],
            [p.ticket for p in pages_with_summaries],
        )
    else:
        summ_div = None
    if summ_div is not None:
        notes_parts.append(
            f"Summary divergence (exploratory, Sarvam Extract vs BART): "
            f"{summ_div['rate']:.3f} (n={summ_div['n']}, clusters={summ_div['n_clusters']}) — "
            "no gold referee; normalised text diff, ticket-clustered CI."
        )

    # Arm-aware summary divergence: only when Extract contributed
    if arm == "digitise":
        summ_div = None
        # Add note that summary not measured for digitise-only
        notes_parts.append(
            "Summary divergence not measured — Extract arm not run (digitise-only); run with --arm extract or both."
        )
    # Phase 17 extensions
    per_cat = per_category_table(pages)
    trans_probe = transliteration_probe(pages)
    samp_info = sample_compliance(n_pages, slice_label=resolved_slice)
    # augment notes with per-category spread headline if available
    if per_cat:
        # headline spread like Police 0.85 vs Welfare 0.51 — report in notes for discoverability
        best = max(per_cat.items(), key=lambda kv: kv[1]["sarvam_accuracy"])
        worst = min(per_cat.items(), key=lambda kv: kv[1]["sarvam_accuracy"])
        notes_parts.append(
            f"Per-category spread: {best[0]} {best[1]['sarvam_accuracy']:.2f} vs "
            f"{worst[0]} {worst[1]['sarvam_accuracy']:.2f} (Sarvam) — "
            "headline accuracy hides this; see per_category table."
        )
    if trans_probe["probe_applicable"]:
        notes_parts.append(
            f"Romanized Odia probe applicable: {trans_probe['n_romanized_candidates']}/"
            f"{trans_probe['n_total']} pages are romanized Odia candidates; "
            "Sarvam transliteration (od-IN, 1000 chars/chunk) via "
            "janasunani/egress/sarvam.py is the next step before hashing/embedding."
        )
    # few-hundred compliance note — use resolved slice, not demo constant
    if not samp_info["is_few_hundred"]:
        notes_parts.append(
            f"Sample size {n_pages} is outside the intended few-hundred "
            f"({BENCHMARK_MIN_SAMPLE}–{BENCHMARK_MAX_SAMPLE}) window for the "
            f"{resolved_slice} stratified benchmark; "
            "cost and power are calibrated for 200–400 at 10 req/min Vision."
        )
    else:
        notes_parts.append(
            f"Sample size {n_pages} is within the few-hundred "
            f"({BENCHMARK_MIN_SAMPLE}–{BENCHMARK_MAX_SAMPLE}) benchmark window for "
            f"{resolved_slice} (stratified handwritten/printed); "
            "Vision divergence reported per-surface with no accuracy verdict "
            "(no transcription ground truth)."
        )
    notes = " ".join(notes_parts)

    # Arm-aware notes for P1 digitise/extract
    if arm == "digitise" and category_result is None:
        notes_parts.append(
            "Category accuracy not measured — digitise-only arm (Extract not run); run with --arm extract or both."
        )
        notes = " ".join(notes_parts)
    if arm == "extract" and div.get("not_measured"):
        notes_parts.append(
            "Transcription divergence not measured — extract-only arm (Digitise not run); run with --arm digitise or both."
        )
        notes = " ".join(notes_parts)

    return ScorecardReport(
        normalizer_version=NORMALIZER_VERSION,
        primary_outcome=PRIMARY_OUTCOME_LABEL,
        transcription_available=transcription_available,
        needs_transcription_sample=needs_sample,
        paired_design=True,
        n_pages=n_pages,
        n_tickets=n_tickets,
        category=category_result,
        transcription_accuracy=transcription_accuracy,
        transcription_divergence=div,
        by_handwritten=by_handwritten,
        by_language=by_language,
        notes=notes,
        per_category=per_cat if per_cat else None,
        transliteration_probe_result=trans_probe,
        sample_info=samp_info,
        summary_divergence=summ_div,
        slice_label=resolved_slice,
        arm=arm,
    )


# ---------------------------------------------------------------------------
# Rendering — markdown + outputs (Unit E)
# ---------------------------------------------------------------------------

def render_markdown(report: ScorecardReport) -> str:
    """Render a human-readable markdown summary of *report*.

    Mirrors the ``render_markdown`` pattern in
    ``janasunani.evaluation.spam_scorecard`` so the benchmark report can
    inline the Sarvam section verbatim. All numeric fields are formatted
    with three decimals; missing arms are labelled ``not measured`` rather
    than omitted so a stale ``DELIVERY.md`` Table 2 row is visible.
    """
    # Use report slice when available (P2), else demo constant
    slice_for_header = getattr(report, "slice_label", None) or report.sample_info.get("slice") if report.sample_info else DEMO_SLICE_LABEL
    if not slice_for_header:
        slice_for_header = DEMO_SLICE_LABEL
    lines: list[str] = [
        f"# Sarvam benchmark — {slice_for_header}",
        "",
        f"Normalizer: `{report.normalizer_version}` · paired={report.paired_design} · n_pages={report.n_pages} · n_tickets={report.n_tickets}",
        "",
        f"**Primary outcome:** {report.primary_outcome}",
        "",
    ]
    # Category headline
    if report.category is not None:
        c = report.category
        lines += [
            "## Category accuracy (headline — paired, ticket-clustered 95% CI)",
            "",
            f"- Pipeline accuracy: **{c['pipeline_accuracy']:.3f}**",
            f"- Sarvam Extract accuracy: **{c['sarvam_accuracy']:.3f}**",
            f"- Difference (Sarvam − pipeline): **{c['difference']:.3f}** 95% CI [{c['ci_low']:.3f}, {c['ci_high']:.3f}] (se={c['se']:.3f}, n_tickets={c['n_tickets']}, clusters={c['n_clusters']})",
            f"- Interpretation: {c.get('interpretation', '')}",
            "",
        ]
    else:
        lines += [
            "## Category accuracy",
            "",
            "Not measured — no gold labels (``gold_category``) in sample; run with ``--join-metadata`` from the lake slice.",
            "",
        ]
    # Transcription — arm-aware: extract-only hides OCR
    if report.transcription_available and report.transcription_accuracy is not None:
        t = report.transcription_accuracy
        lines += [
            "## Transcription accuracy (ground-truth available)",
            "",
            f"- Pipeline: {t['pipeline_accuracy']:.3f} · Sarvam: {t['sarvam_accuracy']:.3f} · diff {t['difference']:.3f} 95% CI [{t['ci_low']:.3f}, {t['ci_high']:.3f}] (n={t['n_pages']}, clusters={t['n_clusters']})",
            "",
        ]
    elif report.transcription_divergence.get("not_measured"):
        reason = report.transcription_divergence.get("reason", "OCR not measured for this arm.")
        lines += [
            "## Transcription",
            "",
            f"Not measured — {reason}",
            "",
        ]
    else:
        lines += [
            "## Transcription",
            "",
            f"Divergence only (no hand-transcribed ground truth): **{report.transcription_divergence['rate']:.3f}** 95% CI [{report.transcription_divergence['ci_low']:.3f}, {report.transcription_divergence['ci_high']:.3f}] (n={report.transcription_divergence['n']}, clusters={report.transcription_divergence['n_clusters']})",
            "",
            "No accuracy verdict — commission a hand-transcribed sample for an accuracy comparison (per DELIVERY.md fallback).",
            "",
        ]
    # Divergence splits — arm-aware
    def _div_block(title: str, data: dict[str, dict[str, Any]]) -> None:
        lines.append(f"## {title}")
        lines.append("")
        # check if arm hides OCR: if any entry has not_measured divergence, show not measured notice
        if data and any(e.get("divergence", {}).get("not_measured") for e in data.values()):
            lines.append("Not measured — Extract-only arm (Digitise not run).")
            lines.append("")
            return
        lines.append("| Bucket | n_pages | divergence rate | 95% CI |")
        lines.append("|---|---:|---:|---|")
        for bucket, entry in sorted(data.items()):
            d = entry["divergence"]
            lines.append(f"| {bucket} | {d['n']} | {d['rate']:.3f} | [{d['ci_low']:.3f}, {d['ci_high']:.3f}] |")
        lines.append("")

    _div_block("By handwritten", report.by_handwritten)
    _div_block("By language", report.by_language)

    # Summary divergence exploratory
    lines.append("## Summary divergence (exploratory — Sarvam Extract vs BART, no gold referee)")
    lines.append("")
    if report.summary_divergence is not None:
        s = report.summary_divergence
        lines.append(f"- Rate: **{s['rate']:.3f}** 95% CI [{s['ci_low']:.3f}, {s['ci_high']:.3f}] (se={s['se']:.3f}, n={s['n']}, clusters={s['n_clusters']})")
        lines.append("- Normalised text diff with ticket-clustered SE; exploratory — no accuracy verdict unless a DSI usefulness re-run is commissioned.")
    else:
        lines.append("Not measured — no paired ``sarvam_summary`` / ``pipeline_summary`` in sample.")
    lines.append("")

    # Per-category spread
    if report.per_category:
        lines.append("## Per-category accuracy spread")
        lines.append("")
        lines.append("| Category | n_tickets | pipeline | Sarvam | diff |")
        lines.append("|---|---:|---:|---:|---:|")
        for cat, row in sorted(report.per_category.items()):
            lines.append(f"| {cat} | {row['n_tickets']} | {row['pipeline_accuracy']:.3f} | {row['sarvam_accuracy']:.3f} | {row['difference']:.3f} |")
        lines.append("")

    # Transliteration probe
    if report.transliteration_probe_result is not None:
        probe = report.transliteration_probe_result
        lines.append("## Transliteration probe (romanized Odia)")
        lines.append("")
        lines.append(f"- Candidates: {probe['n_romanized_candidates']}/{probe['n_total']} — applicable={probe['probe_applicable']}")
        lines.append(f"- Note: {probe.get('note', '')}")
        lines.append("")

    # Sample compliance
    if report.sample_info is not None:
        si = report.sample_info
        lines.append("## Sample")
        lines.append("")
        lines.append(f"- n_pages={si['n_pages']} · few-hundred={si['is_few_hundred']} · slice={si['slice']} (recommended {si['recommended']}, range {si['min']}–{si['max']})")
        lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append(report.notes)
    lines.append("")
    return "\n".join(lines)


def write_outputs(report: ScorecardReport, out_dir: Path | str) -> dict[str, Path]:
    """Write ``report`` as JSON and markdown under *out_dir*.

    Returns mapping ``{"json": Path, "markdown": Path}``. The JSON is the
    machine-readable artifact that feeds ``benchmark_report`` Table 2; the
    markdown is the human-readable fragment inline in the rehearsal slide.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "sarvam_scorecard.json"
    md_path = out / "sarvam_scorecard.md"
    json_path.write_text(json.dumps(report.to_dict(), indent=2, default=str))
    md_path.write_text(render_markdown(report))
    return {"json": json_path, "markdown": md_path}
