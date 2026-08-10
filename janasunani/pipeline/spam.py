"""Bounded spam-signal scorer over redacted text (Phase 14, Unit 2b).

Inputs (bounded, import-light):
- redacted_text (str) — Presidio output, never raw grievance column
- is_repetition_collapsed (bool, optional) — from :func:`ocr_quality.is_repetition_collapsed`
- len(redacted_text) — derived
- lookup against the 8 discard-reason families from ``action_taken_remark``
  (ROADMAP §5.2) — used only for *evaluation* (scorecard), never at
  scoring time.  Only two families are spam-like.

Guard:
- Never reads raw grievance column — callers pass ``grievance_redacted``
  only.
- Never scores duplicate families (case already taken up / duplicate copy)
  or not-within-purview / incomplete families as spam — those are dedup or
  routing failures, not low-signal, and the evaluation harness keeps them
  distinct.  The scorer itself only flags low-signal repetition/length
  patterns; legitimate duplicate or routing-failure prose scores ``clean``.
- Never mutates status, advisory only — the score is a review signal,
  not a gate.

Emits ``spam_score`` in [0,1] + ``spam_reason`` in the 5-value set with
evidence, via :func:`score_spam`.  The 5 reason codes map to the two
spam-like discard families plus technical guards:

- ``repetition_collapse`` — OCR loop
- ``length_too_short`` — too few chars/words to be actionable
- ``low_signal_details_inadequate`` — short/vague, closest to "details inadequate" (39,943)
- ``low_signal_no_grievance`` — generic/test-like, closest to "no specific grievance" (16,340)
- ``clean`` — no low-signal flag

The remaining discard families are *never* scored as spam:
  duplicate copy / case already taken up (dedup),
  not within purview / documents not attached / needs policy decision /
  address not given (incomplete/routing).

This module is import-light (stdlib + ocr_quality only) so it can run in
the ``pipeline-core`` extra and in CI.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, Protocol

from janasunani.pipeline.ocr_quality import is_repetition_collapsed

SPAM_VERSION = "spam-v1-bounded"


def _check_collapsed(text: str) -> bool:
    return is_repetition_collapsed(text)


def is_repetition_collapsed_text(text: str) -> bool:
    return is_repetition_collapsed(text)

SpamReason = Literal[
    "low_signal_details_inadequate",
    "low_signal_no_grievance",
    "repetition_collapse",
    "length_too_short",
    "clean",
]

SPAM_REASONS: tuple[SpamReason, ...] = (
    "low_signal_details_inadequate",
    "low_signal_no_grievance",
    "repetition_collapse",
    "length_too_short",
    "clean",
)

# --- thresholds (bounded, deterministic) ---

# Below this many non-space chars the filing cannot be actionable.
MIN_CHARS_FOR_CONTENT = 20
# Below this many words the filing cannot be actionable.
MIN_WORDS_FOR_CONTENT = 4
# Below this many words/40 chars the filing is vague -> details inadequate.
DETAILS_INADEQUATE_WORDS = 8
DETAILS_INADEQUATE_CHARS = 40

# --- generic/test-like patterns that map to "no specific grievance" ---

_NO_GRIEVANCE_RE = re.compile(
    r"^\W*(test|demo|asdf+|hello|hi|no\s*specific\s*grievance|no\s*grievance|"
    r"blank|empty|nothing|na|n/a|nil|none)\W*$",
    re.IGNORECASE,
)

# After placeholder stripping, a text that is only placeholders/brackets
# and whitespace carries no grievance.
_PLACEHOLDER_ONLY_RE = re.compile(r"^[\s\[\]<>A-Z_]*$")


@dataclass(frozen=True)
class SpamEvidence:
    """Auditable observation that contributed to the spam score."""

    kind: str
    observed: bool | int | float | str


@dataclass(frozen=True)
class SpamScore:
    """Bounded spam signal over one redacted text."""

    spam_score: float
    spam_reason: SpamReason
    evidence: tuple[SpamEvidence, ...]
    method: str = SPAM_VERSION

    def to_review_fields(self) -> dict:
        """Map to :class:`SpamReview` kwargs."""
        from janasunani.serving.schemas import OcrQualityEvidence

        # Collapse evidence to the OcrQualityEvidence shape for the wire
        # contract; extra length/pattern observations are kept in the
        # score's own evidence but only repetition is a validated OCR guard.
        rep = next(
            (e for e in self.evidence if e.kind == "repetition_collapse"),
            None,
        )
        ocr_evidence = (
            OcrQualityEvidence(
                kind="repetition_collapse",
                observed=bool(rep.observed) if rep else False,
            ),
        )
        # Advisory decision: review when score >= 0.5, else abstained.
        # This never blocks submission — it is an officer-review flag.
        decision = "review" if self.spam_score >= 0.5 else "abstained"
        # reason_code mirrors spam_reason for the bounded scorer; the
        # legacy validator in SpamReview now allows these codes.
        return {
            "decision": decision,
            "reason_code": self.spam_reason,
            "spam_score": self.spam_score,
            "spam_reason": self.spam_reason,
            "evidence": ocr_evidence,
            "method": self.method,
        }


def _is_no_grievance_like(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    # Placeholder-only (e.g., "[NAME] [PHONE]") carries no grievance
    if _PLACEHOLDER_ONLY_RE.fullmatch(stripped) and stripped:
        # If after removing placeholders nothing remains, it's empty
        no_brackets = re.sub(r"\[.*?\]", "", stripped).strip()
        if not no_brackets:
            return True
    low = stripped.lower()
    # Exact generic tokens
    if _NO_GRIEVANCE_RE.match(stripped):
        return True
    if low in {"test", "demo", "asdf", "blank"}:
        return True
    return False


class SpamScorer(Protocol):
    """Anything that can score one redacted text for low signal.

    The return type is the load-bearing part. A scorer returns a
    :class:`SpamScore`, never a ``SpamReview``, so it reaches the wire only
    through :meth:`SpamScore.to_review_fields`. That adapter is what keeps
    ``reason_code`` inside the closed set the serving contract admits: a
    trained model cannot invent a new reason and have it silently published.

    A learned scorer must also keep the two properties that make this signal
    safe to show an officer: it is advisory, and it abstains rather than
    guessing. Never auto-reject. A false positive is a citizen's grievance
    discarded, which is a different class of harm from a mis-categorisation.
    """

    def score(
        self,
        redacted_text: str,
        *,
        is_repetition_collapsed: Optional[bool] = None,
    ) -> SpamScore: ...


class BoundedSpamScorer:
    """Adapter presenting the shipped heuristic under the scorer Protocol."""

    version = SPAM_VERSION

    def score(
        self,
        redacted_text: str,
        *,
        is_repetition_collapsed: Optional[bool] = None,
    ) -> SpamScore:
        return score_spam(redacted_text, is_repetition_collapsed=is_repetition_collapsed)


def score_spam(
    redacted_text: str,
    *,
    is_repetition_collapsed: Optional[bool] = None,
) -> SpamScore:
    """Score one redacted text; never touches raw grievance.

    Returns a bounded ``SpamScore`` with ``spam_score`` in [0,1] and a
    5-value ``spam_reason``.  Deterministic, no model, no I/O.

    The 8 discard-reason families are *not* consulted here — they are the
    evaluation harness's reference (see :mod:`evaluation.spam_scorecard`).
    Scoring uses only redacted text, repetition flag, and length.  Duplicate
    and not-within-purview families are never scored as spam because their
    prose is legitimate and will fall through to ``clean`` here.
    """
    if redacted_text is None:
        redacted_text = ""
    text = str(redacted_text)
    stripped = text.strip()
    char_len = len(stripped)
    word_count = len(stripped.split()) if stripped else 0

    collapsed = (
        bool(is_repetition_collapsed)
        if is_repetition_collapsed is not None
        else _check_collapsed(text)
    )

    evidence: list[SpamEvidence] = [
        SpamEvidence(kind="repetition_collapse", observed=collapsed),
        SpamEvidence(kind="char_len", observed=char_len),
        SpamEvidence(kind="word_count", observed=word_count),
    ]

    # 1. Repetition collapse — highest signal
    if collapsed:
        return SpamScore(
            spam_score=0.92,
            spam_reason="repetition_collapse",
            evidence=tuple(evidence),
        )

    # 2. Too short to be actionable (very short filings)
    if char_len < MIN_CHARS_FOR_CONTENT:
        return SpamScore(
            spam_score=0.85,
            spam_reason="length_too_short",
            evidence=tuple(evidence),
        )

    # 3. Generic / test-like -> "no specific grievance"
    if _is_no_grievance_like(stripped):
        evidence.append(SpamEvidence(kind="pattern", observed="no_grievance"))
        return SpamScore(
            spam_score=0.78,
            spam_reason="low_signal_no_grievance",
            evidence=tuple(evidence),
        )

    # 4. Short/vague -> "details inadequate"
    if word_count < DETAILS_INADEQUATE_WORDS or char_len < DETAILS_INADEQUATE_CHARS:
        return SpamScore(
            spam_score=0.68,
            spam_reason="low_signal_details_inadequate",
            evidence=tuple(evidence),
        )

    # 5. Clean — no low-signal flag
    return SpamScore(
        spam_score=0.07,
        spam_reason="clean",
        evidence=tuple(evidence),
    )


# ---------------------------------------------------------------------------
# Lightweight helpers for evaluation — kept here so scorecard can reuse
# the same bounded logic without touching raw grievance.
# ---------------------------------------------------------------------------


def is_spam_flagged(score: SpamScore, threshold: float = 0.5) -> bool:
    return score.spam_score >= threshold


# ---------------------------------------------------------------------------
# CLI — corpus prevalence + optional scoring over the lake slice
# ---------------------------------------------------------------------------


def _parse_slice(value: str) -> tuple[str, int]:
    if "/" not in value:
        raise argparse.ArgumentTypeError("--slice must be DISTRICT/YEAR, e.g. Sambalpur/2024")
    district, year_s = value.split("/", 1)
    district = district.strip()
    if not district:
        raise argparse.ArgumentTypeError("district part of --slice is empty")
    try:
        year = int(year_s.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"year part of --slice must be int, got {year_s!r}") from exc
    return district, year


def _score_corpus(
    lake_dir: Path | None,
    district: str,
    year: int,
    threshold: float = 0.5,
) -> dict:
    """Score the slice's grievance_redacted rows; return counts + PPV."""

    # Imported lazily so `import janasunani.pipeline.spam` stays light.
    import polars as pl

    from janasunani.olap import lake as lake_mod

    # --- load slice over lake (reads grievance_redacted only) ---
    # Join complaints (for district/year/category/mode) with
    # grievance_redactions (for redacted text). Never selects raw grievance.
    district_lit = "'" + district.replace("'", "''") + "'"
    sql_inlined = f"""
        SELECT
            c.ticket_no,
            c.district,
            c.category,
            c.mode,
            c.created_year,
            g.grievance_redacted AS redacted_text
        FROM complaints c
        JOIN grievance_redactions g USING (ticket_no)
        WHERE lower(c.district) = lower({district_lit})
          AND c.created_year = {year}
          AND g.grievance_redacted IS NOT NULL
    """

    try:
        frame = lake_mod.query(sql_inlined, lake_dir, tables=("complaints", "grievance_redactions"))
    except FileNotFoundError:
        return {
            "slice": f"{district}/{year}",
            "total": 0,
            "flagged": 0,
            "prevalence": 0.0,
            "by_district": [],
            "by_category": [],
            "by_mode": [],
            "by_year": [],
            "ppv": None,
            "false_positive_rate": None,
            "note": "lake tables missing — run materialize first",
        }

    if frame.height == 0:
        return {
            "slice": f"{district}/{year}",
            "total": 0,
            "flagged": 0,
            "prevalence": 0.0,
            "by_district": [],
            "by_category": [],
            "by_mode": [],
            "by_year": [],
            "ppv": None,
            "false_positive_rate": None,
            "note": "no rows in slice",
        }

    # Score each row
    scores = []
    for row in frame.iter_rows(named=True):
        s = score_spam(row["redacted_text"] or "")
        scores.append((s.spam_score, s.spam_reason))
    frame = frame.with_columns(
        [
            pl.Series("spam_score", [s for s, _ in scores]),
            pl.Series("spam_reason", [r for _, r in scores]),
            pl.Series("flagged", [1 if s >= threshold else 0 for s, _ in scores]),
        ]
    )

    total = frame.height
    flagged = int(frame["flagged"].sum())
    prevalence = flagged / total if total else 0.0

    def _group(by: str) -> list[dict]:
        if by not in frame.columns:
            return []
        g = (
            frame.group_by(by)
            .agg([pl.len().alias("total"), pl.col("flagged").sum().alias("flagged")])
            .with_columns((pl.col("flagged") / pl.col("total")).alias("prevalence"))
            .sort(by)
        )
        return g.to_dicts()

    # --- PPV vs officer-confirmed spam-like discards (held-out) ---
    # Officer labels are the two spam-like families only; duplicate and
    # not-within-purview families are explicitly excluded from the positive
    # set (guard: never score them as spam).
    ppv = None
    fpr = None
    try:
        # Normalize action_taken_remark the same way discards.py does
        # (lowercase, trim, collapse whitespace, strip trailing period)
        # and join to the slice's ticket_nos.
        family_sql = """
            WITH discard_template(family, template) AS (
                VALUES
                    ('details_inadequate', 'complaint details inadequate'),
                    ('details_inadequate', 'complaint details inadequate. may file a detail grievance'),
                    ('no_specific_grievance', 'no specific grievance'),
                    ('no_specific_grievance', 'no specific grievance. may file a detail grievance')
            ),
            normalized_action AS (
                SELECT ticket_no,
                       regexp_replace(regexp_replace(lower(trim(action_taken_remark)), '\\s+', ' ', 'g'), '\\.$', '') AS norm
                FROM action_history
                WHERE action_taken_remark IS NOT NULL
            ),
            matched AS (
                SELECT DISTINCT ticket_no FROM normalized_action
                INNER JOIN discard_template d ON d.template = norm
                WHERE d.family IN ('details_inadequate', 'no_specific_grievance')
            )
            SELECT ticket_no FROM matched
        """
        officer_frame = lake_mod.query(family_sql, lake_dir, tables=("action_history",))
        officer_positive = set(officer_frame["ticket_no"].to_list()) if officer_frame.height else set()

        # Held-out split: deterministic hash of ticket_no -> 30% holdout
        def is_holdout(t: str) -> bool:
            return (int(hashlib.sha256(t.encode()).hexdigest(), 16) % 10) < 3

        holdout = frame.filter(pl.col("ticket_no").map_elements(is_holdout, return_dtype=pl.Boolean))
        if holdout.height > 0 and officer_positive:
            tp = holdout.filter(
                (pl.col("flagged") == 1) & pl.col("ticket_no").is_in(list(officer_positive))
            ).height
            fp = holdout.filter(
                (pl.col("flagged") == 1) & (~pl.col("ticket_no").is_in(list(officer_positive)))
            ).height
            tn = holdout.filter(
                (pl.col("flagged") == 0) & (~pl.col("ticket_no").is_in(list(officer_positive)))
            ).height
            ppv = tp / (tp + fp) if (tp + fp) > 0 else None
            fpr = fp / (fp + tn) if (fp + tn) > 0 else None
        elif holdout.height == 0:
            ppv = None
            fpr = None
    except Exception:
        ppv = None
        fpr = None

    return {
        "slice": f"{district}/{year}",
        "total": total,
        "flagged": flagged,
        "prevalence": prevalence,
        "threshold": threshold,
        "by_district": _group("district"),
        "by_category": _group("category"),
        "by_mode": _group("mode"),
        "by_year": _group("created_year"),
        "ppv_holdout": ppv,
        "false_positive_rate_holdout": fpr,
        "note": "PPV reported, not gated; duplicate/not-within-purview never counted as spam",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score spam signal over a lake slice (redacted text only).")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--slice", type=str, help="District/year slice, e.g. Sambalpur/2024")
    g.add_argument("--district", type=str, help="District (requires --year)")
    parser.add_argument("--year", type=int, default=None, help="Year (requires --district)")
    parser.add_argument("--lake-dir", type=Path, default=None, help="Lake dir (default: data/interim)")
    parser.add_argument("--threshold", type=float, default=0.5, help="Flag threshold on spam_score")
    parser.add_argument("--out-dir", type=Path, default=None, help="Write prevalence CSV/Markdown under this dir")
    args = parser.parse_args()

    if args.slice:
        district, year = _parse_slice(args.slice)
    else:
        if args.year is None:
            parser.error("--year is required with --district")
        district, year = args.district, args.year

    result = _score_corpus(args.lake_dir, district, year, threshold=args.threshold)

    # Human-readable summary (stdout is the contract for the demo)
    print(f"Slice: {result['slice']}")
    print(f"Total with redacted text: {result['total']}")
    print(f"Flagged (score >= {result['threshold']}): {result['flagged']} ({result['prevalence']:.2%})")
    if result.get("ppv_holdout") is not None:
        print(f"PPV (holdout vs officer spam-like): {result['ppv_holdout']:.3f}")
        print(f"False-positive rate (holdout): {result['false_positive_rate_holdout']:.3f}")
    else:
        print("PPV: not computed (no holdout or no officer labels in slice)")
    if result.get("by_category"):
        print("\nBy category:")
        for row in result["by_category"]:
            print(f"  {row['category']}: {row['flagged']}/{row['total']} ({row['prevalence']:.2%})")

    if args.out_dir:
        import json

        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "spam_prevalence.json").write_text(json.dumps(result, indent=2, default=str))
        # Also delegate to scorecard harness if available
        try:
            from janasunani.evaluation.spam_scorecard import write_outputs

            write_outputs(result, out)
        except Exception:
            pass
        print(f"\nWrote {out / 'spam_prevalence.json'}")


if __name__ == "__main__":
    main()
