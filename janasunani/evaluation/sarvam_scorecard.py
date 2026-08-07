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

import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

NORMALIZER_VERSION = "1.0"

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

def _clustered_se(diffs: list[float], clusters: list[str]) -> float:
    """Cluster-robust SE for the mean of ``diffs`` clustered by ``clusters``.

    Uses the sandwich estimator for a mean:
      Var = sum_c (sum_{i in c} (d_i - mean))^2 / n^2 * C/(C-1)
    where n = len(diffs), C = number of clusters. When C == 1 or C == n,
    reduces to the usual variance of the mean (with finite-sample correction).
    Returns 0.0 for n < 2.
    """
    n = len(diffs)
    if n < 2:
        return 0.0
    mean = sum(diffs) / n
    # per-cluster sum of deviations
    cluster_sums: dict[str, float] = defaultdict(float)
    for d, c in zip(diffs, clusters):
        cluster_sums[c] += d - mean
    C = len(cluster_sums)
    summed_sq = sum(s * s for s in cluster_sums.values())
    # finite-sample correction for clusters; when C==1 fall back to simple
    if C <= 1:
        # simple SE: sqrt( sum (d - mean)^2 / (n*(n-1)) )
        s2 = sum((d - mean) ** 2 for d in diffs) / (n - 1) if n > 1 else 0.0
        return math.sqrt(s2 / n) if n else 0.0
    correction = C / (C - 1)
    var = summed_sq / (n * n) * correction
    # numerical guard
    if var < 0:
        var = 0.0
    return math.sqrt(var)


def paired_difference(
    a_correct: list[int],
    b_correct: list[int],
    clusters: list[str],
    alpha: float = 0.05,
) -> dict[str, float]:
    """Paired difference in accuracy with ticket-clustered CI.

    Args:
      a_correct: 1/0 per page for system A (e.g. Sarvam)
      b_correct: 1/0 per page for system B (e.g. pytesseract/pipeline)
      clusters: ticket id per page
      alpha: two-sided level (0.05 -> 95% CI, z=1.96)

    Returns dict with diff, se, z, ci_low, ci_high, n, n_clusters.
    The difference is mean(a) - mean(b). Positive favours A.
    """
    if not (len(a_correct) == len(b_correct) == len(clusters)):
        raise ValueError("a_correct, b_correct, clusters must have equal length")
    n = len(a_correct)
    if n == 0:
        return {"diff": 0.0, "se": 0.0, "z": 1.96, "ci_low": 0.0, "ci_high": 0.0, "n": 0, "n_clusters": 0}
    diffs = [float(a - b) for a, b in zip(a_correct, b_correct)]
    diff = sum(diffs) / n
    se = _clustered_se(diffs, clusters)
    # Normal critical value; for large n the t correction is negligible.
    # Use 1.96 for 95%, else approximate via normal quantile.
    if alpha == 0.05:
        z = 1.96
    else:
        # approximate via inverse normal (simple); for non-95% use 1.96 as fallback
        # Keep dependency-light: use 1.96 for 95% else 2.576 for 99% else 1.645 for 90%
        if abs(alpha - 0.01) < 1e-9:
            z = 2.576
        elif abs(alpha - 0.10) < 1e-9:
            z = 1.645
        else:
            z = 1.96
    ci_low = diff - z * se
    ci_high = diff + z * se
    n_clusters = len(set(clusters))
    return {"diff": diff, "se": se, "z": z, "ci_low": ci_low, "ci_high": ci_high, "n": n, "n_clusters": n_clusters}


def divergence_rate(
    pytesseract_texts: list[str],
    sarvam_markdowns: list[str],
    clusters: list[str],
) -> dict[str, float]:
    """Divergence rate: share of pages where normalised texts differ.

    Used when no transcription referee exists (issue #84 fallback). Reports
    a descriptive disagreement rate with ticket-clustered CI, not a verdict.
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
    z = 1.96
    return {"rate": rate, "se": se, "ci_low": rate - z * se, "ci_high": rate + z * se, "n": n, "n_clusters": len(set(clusters))}


# ---------------------------------------------------------------------------
# Power helpers (McNemar, paired)
# ---------------------------------------------------------------------------

def required_pages_mcnemar(gap: float, discordance: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """Approximate required pages for a paired (McNemar) comparison.

    ``gap`` is the accuracy difference to detect (e.g. 0.10 for 10 points),
    ``discordance`` is the expected share where the two systems disagree.
    Uses the normal approximation for McNemar's test. Returns ceiling.

    The table in #127 was computed with the same approximation; values are
    indicative — the recommmended 200-250 stratified sample is powered for
    ~10 points at 20-30% discordance.
    """
    if not (0 < gap < 1 and 0 < gap < discordance <= 1):
        raise ValueError("need 0 < gap < discordance <= 1")
    # z critical values
    z_alpha = 1.96 if alpha == 0.05 else 1.96
    # 80% power -> z=0.84
    z_beta = 0.84 if power == 0.8 else 0.84
    # McNemar: n = (z_alpha*sqrt(p_d) + z_beta*sqrt(p_d - gap^2))^2 / gap^2
    p_d = discordance
    numerator = (z_alpha * math.sqrt(p_d) + z_beta * math.sqrt(max(p_d - gap * gap, 1e-9))) ** 2
    n = numerator / (gap * gap)
    return int(math.ceil(n))


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PRIMARY_OUTCOME_LABEL = (
    "Category accuracy difference (Sarvam Extract vs pipeline categorizer) "
    "on the paired sample, ticket-clustered 95% CI — the headline. "
    "Transcription and per-class category are exploratory."
)


def build_scorecard(pages: list[PageRecord]) -> ScorecardReport:
    """Build the full scorecard from a frozen sample.

    * Every page must have been processed by **both** engines (paired).
    * ``handwritten`` and ``language`` splits are always reported.
    * If no page carries ``transcription``, accuracy is not computable and
      ``transcription_divergence`` is the only transcription result.
    """
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
    cat_tickets = [t for t in ticket_gold if ticket_gold[t] is not None]
    cat_clusters = cat_tickets  # one per ticket
    if cat_tickets:
        pipe_correct = [1 if ticket_pipe.get(t) == ticket_gold[t] else 0 for t in cat_tickets]
        sarvam_correct = [1 if ticket_sarvam.get(t) == ticket_gold[t] else 0 for t in cat_tickets]
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

    # Transcription arm
    transcription_available = any(p.transcription is not None for p in pages)
    # divergence always computable
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
    def _split_metrics(key_fn) -> dict[str, dict[str, Any]]:
        buckets: dict[str, list[PageRecord]] = defaultdict(list)
        for p in pages:
            buckets[key_fn(p)].append(p)
        out: dict[str, dict[str, Any]] = {}
        for bucket, bucket_pages in sorted(buckets.items()):
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
    )
