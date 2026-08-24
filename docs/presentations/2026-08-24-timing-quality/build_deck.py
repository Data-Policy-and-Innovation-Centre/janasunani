"""
Janasunani 2.0 — Timing and Quality.

Builds a 13-slide technical briefing by CLONING slide archetypes out of the
GAPG 18 August 2026 reference deck, so type, colour, geometry and the footer
lockup are the reference file's own shapes rather than a reconstruction.

Archetypes reused (by reference slide number):
  S1  title
  S2  three numbered items + right-hand note + bottom line
  S3  two grouped bullet lists + bottom maroon line
  S6  three label/description rows with a maroon tick
  S7  left prose + two big-number cards on the right
  S9  three label + proportional bar + number rows
  S13 closing

Reference slides 4, 8 (portal screenshots) and 10 (native chart) are NOT used:
the screenshots carry citizen PII and the chart has no equivalent here.

Every figure traces to an artifact; sources live in the speaker notes.
"""

import copy
from pptx import Presentation
from pptx.enum.text import MSO_ANCHOR
from pptx.text.text import _Paragraph
from pptx.util import Inches

REF = "reference.pptx"
OUT = "Janasunani_2.0_Timing_and_Quality.pptx"

# Reference archetype indices (0-based into prs.slides)
A_TITLE, A_NUM3, A_TWOGROUP, A_ROWS3, A_BIG2, A_BARS3, A_CLOSING = 0, 1, 2, 5, 6, 8, 12


# ── cloning ───────────────────────────────────────────────────────────────────
# The notes master carries no body placeholder, so a freshly created notes slide
# has nowhere to put speaker notes. Borrow the reference deck's own notes
# placeholder and graft it onto every cloned slide.
NOTES_PLACEHOLDER = None


def clone(prs, src_idx):
    """Deep-copy every shape of a source slide onto a new slide at the end."""
    src = prs.slides[src_idx]
    new = prs.slides.add_slide(src.slide_layout)
    for shp in list(new.shapes):
        shp._element.getparent().remove(shp._element)
    for shp in src.shapes:
        new.shapes._spTree.append(copy.deepcopy(shp._element))
    if new.notes_slide.notes_text_frame is None and NOTES_PLACEHOLDER is not None:
        new.notes_slide.shapes._spTree.append(copy.deepcopy(NOTES_PLACEHOLDER))
    return new


def set_text(shape, text):
    """Replace a shape's text, keeping the first run's formatting.

    Multi-line text is split on newlines into paragraphs, each cloned from the
    first paragraph so bullet/indent/spacing properties survive.
    """
    tf = shape.text_frame
    p0 = tf.paragraphs[0]
    if not p0.runs:
        tf.text = text
        return
    r0 = p0.runs[0]
    lines = text.split("\n")
    r0.text = lines[0]
    for extra in p0.runs[1:]:
        extra._r.getparent().remove(extra._r)
    for p in tf.paragraphs[1:]:
        p._p.getparent().remove(p._p)
    for line in lines[1:]:
        newp = copy.deepcopy(p0._p)
        p0._p.getparent().append(newp)
        para = _Paragraph(newp, tf)
        para.runs[0].text = line
        for extra in para.runs[1:]:
            extra._r.getparent().remove(extra._r)


def S(slide):
    """Shapes as a list, in document order."""
    return list(slide.shapes)


def top_anchor(shape, height_in):
    """Pin a text box to the top of a fixed band.

    The reference deck centre-anchors its left-hand prose box, which is fine for
    the two short lines it originally held but pushes longer copy downward into
    the box below it. Anchoring to the top and capping the height keeps the
    growth direction predictable.
    """
    shape.text_frame.vertical_anchor = MSO_ANCHOR.TOP
    shape.height = Inches(height_in)


# ── deck ──────────────────────────────────────────────────────────────────────
prs = Presentation(REF)
n_original = len(prs.slides._sldIdLst)

# Capture the reference deck's notes body placeholder before anything is removed.
for _src in prs.slides:
    _tf = _src.notes_slide.notes_text_frame
    if _tf is not None:
        NOTES_PLACEHOLDER = copy.deepcopy(_tf._txBody.getparent())
        break

# ══ 1 · Title ═════════════════════════════════════════════════════════════════
s = clone(prs, A_TITLE)
sh = S(s)
set_text(sh[0], "TECHNICAL BRIEFING")
set_text(sh[1], "Janasunani 2.0")
set_text(sh[2], "What the system does, measured on two axes: timing and quality")
set_text(sh[4], "Data, Policy and Innovation Centre")
set_text(sh[5], "24 August 2026")

# ══ 2 · The record ════════════════════════════════════════════════════════════
s = clone(prs, A_BARS3)
sh = S(s)
set_text(sh[0], "THE RECORD")
set_text(sh[1], "1.37 million grievances, one question asked of them")
set_text(sh[2], "A continuous, dated, geocoded record of what is going wrong across thirty districts.")
rows = [
    (sh[3], sh[4], sh[5], "Complaints filed", "1,371,288", 1.0),
    (sh[6], sh[7], sh[8], "Closed with a remark", "1,209,144", 1_209_144 / 1_371_288),
    (sh[9], sh[10], sh[11], "No closing remark", "162,144", 162_144 / 1_371_288),
]
for label_sh, bar_sh, num_sh, label, num, frac in rows:
    set_text(label_sh, label)
    set_text(num_sh, num)
    bar_sh.width = Inches(max(1.35, 8.0 * frac))
    num_sh.width = Inches(max(1.05, 8.0 * frac) - 0.3)
set_text(sh[15], "6,556,171 action events · 30 districts · 427 blocks · 2021 to 2025")
set_text(sh[12], "The portal reports how many cases are open and how many are closed. It has never read what the citizen wrote.")
s.notes_slide.notes_text_frame.text = (
    "Sources: docs/ARCHITECTURE.md, docs/ROADMAP.md. Canonical counts verified on both local "
    "SQLite and cloud Postgres and must match after any migration change.\n\n"
    "Caveat: the Parquet lake reads 6,548,820 action rows against the canonical 6,556,171, a "
    "0.11% shortfall tracked as issue #241. Use the canonical figure.\n\n"
    "DERIVED FIGURE: 162,144 is arithmetic, 1,371,288 minus the 1,209,144 that carry a closing "
    "remark. It is not separately measured. Those cases are either still open or were closed "
    "without a remark, and the record cannot distinguish the two. If pressed, say that.\n\n"
    "Two structural facts behind everything that follows: the portal has never read the "
    "grievance text (median 19 words, 61% unique), and there is no citizen key, so every row is "
    "an island."
)

# ══ 3 · How we measure ════════════════════════════════════════════════════════
s = clone(prs, A_ROWS3)
sh = S(s)
set_text(sh[0], "HOW WE MEASURE")
set_text(sh[1], "Two axes, and both halves of each")
set_text(sh[3], "Measured")
set_text(sh[4], "From the records, or a timed run on real code paths. Reported with n, and an interval wherever it is a rate.")
set_text(sh[6], "Estimated")
set_text(sh[7], "Model output with an interval and a stated estimand. Never promoted to a fact.")
set_text(sh[9], "Open")
set_text(sh[10], "The number does not exist. We say so, rather than substitute a proxy or a plausible round figure.")
s.notes_slide.notes_text_frame.text = (
    "The three-label scheme is the evidence-chip primitive carried over from the 17 August deck.\n\n"
    "Governing rule, from janasunani/evaluation/__init__.py: pipeline.pii_eval is a GATE and can "
    "fail a release; everything in evaluation/ REPORTS, and a bad result is an answer, not a "
    "failure.\n\n"
    "Two slides in this deck do nothing but list the open items: slide 7 for timing, slide 11 for "
    "quality. Do not cut them for time. A timing and quality briefing showing only the good half "
    "of each axis is exactly what this room is trained to distrust.\n\n"
    "If asked how the intervals are computed: an audit of evaluation/stats.py (PR #237) found a "
    "fixed z=1.96 used for every confidence level and a missing small-cluster correction. Both "
    "were fixed, and every clustered interval in this deck is 4 to 10% wider as a result."
)

# ══ 4 · Timing, end to end ════════════════════════════════════════════════════
s = clone(prs, A_BIG2)
sh = S(s)
set_text(sh[1], "TIMING · END TO END")
set_text(sh[2], "Seconds, not minutes")
top_anchor(sh[3], 1.85)
set_text(sh[3],
         "A typed grievance completes in a tenth of a second. A scanned document, including "
         "reading the text off the page, takes under fourteen seconds. Ninety of ninety timed "
         "attempts completed with no failures.")
set_text(sh[4], "Today the same first pass is self-reported at 10 to 15 minutes, and has never been timed.")
set_text(sh[6], "0.13 s")
set_text(sh[7], "median, typed grievance (n = 40)")
set_text(sh[10], "13.7 s")
set_text(sh[11], "median, scanned document (n = 20)")
s.notes_slide.notes_text_frame.text = (
    "Source: outputs/benchmark/latency.json, run 2026-08-10T23:14:58Z at git sha 24ab193, with "
    "is_fake_timing false. 30 synthetic grievances (20 typed, 10 PDF) x 3 repeats, first "
    "discarded. Host is an arm64 laptop, 10 logical cores, Python 3.13.\n\n"
    "Means: 0.109 s typed (clustered SE 0.0115), 13.244 s PDF (SE 0.425). p95: 0.150 s and "
    "15.26 s. Processor startup 6.47 s, one-off.\n\n"
    "Live API from docs/PERFORMANCE.md section 1, baseline 2026-08-07 at ca58f31: warm POST "
    "median 4.44 s over n=8, first call after boot 9.5 s, cold start to health 19.4 s.\n\n"
    "DO NOT cite the latency section of outputs/benchmark/table2.md. It was generated 3h38m "
    "before latency.json and wrongly says no harness output exists."
)

# ══ 5 · Where the time goes ═══════════════════════════════════════════════════
s = clone(prs, A_BARS3)
sh = S(s)
set_text(sh[0], "TIMING · PER STAGE")
set_text(sh[1], "Two stages are 94% of the wall clock")
set_text(sh[2], "Mean seconds per scanned document, by pipeline stage.")
stages = [
    (sh[3], sh[4], sh[5], "Summarise", "6.55 s", 6.550 / 6.550),
    (sh[6], sh[7], sh[8], "Extract text", "5.83 s", 5.833 / 6.550),
    (sh[9], sh[10], sh[11], "Everything else", "0.86 s", 0.861 / 6.550),
]
for label_sh, bar_sh, num_sh, label, num, frac in stages:
    set_text(label_sh, label)
    set_text(num_sh, num)
    bar_sh.width = Inches(max(1.35, 8.0 * frac))
    num_sh.width = Inches(max(1.05, 8.0 * frac) - 0.3)
set_text(sh[15], "Categorise, redact, detect, route and triage combined")
set_text(sh[12], "Everything after text extraction is effectively free. Routing and triage cost under a thousandth of a second.")
s.notes_slide.notes_text_frame.text = (
    "Source: outputs/benchmark/latency.json, document path, n=20 over 10 clusters.\n\n"
    "Full per-stage means: summarise 6.550, OCR 5.833, categorise 0.778, redact 0.055, detect "
    "PII 0.021, detect language 0.006, route 0.00048, triage 0.00026. Summarise plus OCR is "
    "12.383 s of the 13.244 s mean run, 93.5%.\n\n"
    "'Everything else' is 0.861 s, the sum of the remaining six instrumented stages.\n\n"
    "Four stages (format classifier, page type, pii, spam) were never separately instrumented and "
    "carry n=0. They are omitted rather than shown as zero. On the typed path summarise is a "
    "no-op at 5e-06 s.\n\n"
    "Implication if asked: optimisation effort belongs in the summariser and the OCR engine. "
    "Nowhere else on this list is worth touching."
)

# ══ 6 · At corpus scale ═══════════════════════════════════════════════════════
s = clone(prs, A_ROWS3)
sh = S(s)
set_text(sh[0], "TIMING · AT SCALE")
set_text(sh[1], "Batch work is minutes, not days")
set_text(sh[3], "Deduplication")
set_text(sh[4], "55,544 filings indexed in about 57 minutes on two virtual cores, across 16.1 million comparison pairs.")
set_text(sh[6], "Materialisation")
set_text(sh[7], "The full corpus rebuilt for analysis in about 26 seconds: 1.37 million complaints, 6.5 million action rows.")
set_text(sh[9], "History query")
set_text(sh[10], "A median 0.13 seconds to page twenty records out of 1,371,288.")
s.notes_slide.notes_text_frame.text = (
    "Sources: docs/PERFORMANCE.md sections 1 and 4. Measured against production Postgres on the "
    "deployment box, not the laptop used for slide 4.\n\n"
    "Dedup ran over the frozen demo slice, Sambalpur 2024. Provenance complete: 55,544 of 55,544 "
    "on both the group and signature tables. 310 large buckets.\n\n"
    "Materialisation via janasunani/olap/materialize.py using DuckDB. History reads the Parquet "
    "lake, never the transactional store; a live submission therefore appears in history only "
    "after the next re-materialisation, and that freshness gap is by design.\n\n"
    "Cost, if asked: marginal cost per call is zero. Open weights on hardware we control. A "
    "commercial vision API is Rs 0.50 to 1.50 per page; roughly Rs 8,050 to push 1.37M subjects "
    "through a 105B model. Source janasunani/evaluation/pricing.py, verified 2026-08-07."
)

# ══ 7 · What we cannot claim on timing ════════════════════════════════════════
s = clone(prs, A_NUM3)
sh = S(s)
set_text(sh[0], "TIMING · THE GAPS")
set_text(sh[1], "What we cannot claim about time")
set_text(sh[3], "13.7 seconds is machine time, not officer time.")
set_text(sh[5], "Measured on a laptop, not the deployment box, and not a measure of officer effort.")
set_text(sh[8], "The 10 to 15 minute baseline is self-reported.")
set_text(sh[10], "No timed officer study exists. Every speed-up ratio inherits that uncertainty.")
set_text(sh[13], "No release timing harness has been run.")
set_text(sh[15], "The publication gate reads speed 0 of 1. All of the above is development evidence.")
set_text(sh[16], "201,000 to 302,000 officer-hours sits in the resolved record. That is a denominator, not a saving.")
s.notes_slide.notes_text_frame.text = (
    "Do not cut this slide for time.\n\n"
    "The officer-hours range covers the registration time embedded in 1,209,144 resolved cases. "
    "It is the size of the prize, not anything realised. It must never be presented as a benefit.\n\n"
    "A fourth gap, if asked: routing step timing. The figure that previously sat on this slide in "
    "the 18 August deck, 11 to 23 days lost between routing steps, was WITHDRAWN on 23 August "
    "(commits 879c24c, 365e3b4). See slide 12. A descriptive replacement measuring elapsed time "
    "between recorded handling steps is under recomputation and is deliberately not previewed "
    "here. Do not reintroduce any day-saving number."
)

# ══ 8 · Redaction ═════════════════════════════════════════════════════════════
s = clone(prs, A_BIG2)
sh = S(s)
set_text(sh[1], "QUALITY · REDACTION")
set_text(sh[2], "Nothing shaped like an identifier survives")
top_anchor(sh[3], 1.85)
set_text(sh[3],
         "Against 480 hand-marked spans across 89 pages, redaction finds 78% of personal "
         "details, 86% of Aadhaar numbers and 83% of phone numbers. The population scan on the "
         "right covers every record in the slice, not a sample.")
set_text(sh[4], "It over-fires: 824 predicted spans against 480 marked. There is no precision figure.")
set_text(sh[6], "0 of 55,544")
set_text(sh[7], "records retaining a shaped identifier after redaction")
set_text(sh[10], "0.78")
set_text(sh[11], "recall on the hand-marked gold set (n = 480)")
s.notes_slide.notes_text_frame.text = (
    "Sources: outputs/evaluation/pii_release.json (re-measured 2026-08-10) and docs/FINDINGS.md.\n\n"
    "Overall typed overlap recall 0.7792, coverage 0.7833, exact 0.5500. Per entity, overlap: "
    "Aadhaar 0.857 (n=7), phone 0.828 (n=29), name 0.777 (n=404), email 0.750 (n=40). Names are "
    "404 of 480 spans so they set the headline; name exact recall is only 0.507.\n\n"
    "Gold set: 529 hand-corrected spans, 480 scored after excluding 49 government email addresses "
    "by policy. 50 documents, 89 pages.\n\n"
    "Stack: Presidio in-process, custom Indian recognisers (mobile/Aadhaar/PAN), spaCy NER, an "
    "Indian-surname gazetteer and an ALL-CAPS recogniser.\n\n"
    "WITHDRAWN, do not use: the 49.6% coverage figure and the 0.44 name figure. Both predate the "
    "ALL-CAPS and gazetteer fixes.\n\n"
    "The gate currently FAILS: janasunani-evaluate-pii exits 1 because 0.7833 < 0.8056. That "
    "0.8056 is the DSI reference constant wired as a threshold by mistake, issue #239. It was "
    "filed rather than relaxed on the eve of a demo."
)

# ══ 9 · Shortlists ════════════════════════════════════════════════════════════
s = clone(prs, A_BARS3)
sh = S(s)
set_text(sh[0], "QUALITY · RANKING")
set_text(sh[1], "Shortlists, not decisions")
set_text(sh[2], "Share of cases where the correct answer is in the model's top three.")
ranked = [
    (sh[3], sh[4], sh[5], "Category", "90.9%", 0.909),
    (sh[6], sh[7], sh[8], "Department, clear cases", "79.7%", 0.797),
    (sh[9], sh[10], sh[11], "Department, all cases", "69.0%", 0.690),
]
for label_sh, bar_sh, num_sh, label, num, frac in ranked:
    set_text(label_sh, label)
    set_text(num_sh, num)
    bar_sh.width = Inches(max(1.35, 8.0 * frac))
    num_sh.width = Inches(max(1.05, 8.0 * frac) - 0.3)
set_text(sh[15], "Held-out test cases: 3,160 for category, 142,181 and 208,267 for department, all from an untouched 2025")
set_text(sh[12], "Forty departments to three. A separate claim, that routing saved 11 to 23 days, was withdrawn.")
s.notes_slide.notes_text_frame.text = (
    "Sources: outputs/evaluation/categorization_historical_v1.json and "
    "outputs/evaluation/routing_historical_{informative,all}.json.\n\n"
    "TOP-1, if asked, and say it unprompted if the room is quantitative: category 46.6%, "
    "department 55.0% clear / 45.1% all. Neither model can name the single right answer and "
    "neither is asked to. Macro-F1 is weak: 36.5% category, 25.2% / 19.8% department, with about "
    "a dozen departments at F1 zero.\n\n"
    "Category split: chronological 2024, exact-text-group-disjoint, 18 categories, ECE 26.4%, "
    "release_eligible false.\n\n"
    "Department split: train 2021-23, validate 2024, final refit on train+validation, test on an "
    "untouched 2025. alpha=100, one-year history window. Intervals suppressed as not "
    "cluster-robust for weighted route cells.\n\n"
    "CRITICAL CAVEAT: this measures agreement with where cases were historically sent, NOT "
    "jurisdictional correctness. A correct-authority adjudication does not exist and is one of "
    "the eight publication blockers.\n\n"
    "WITHDRAWN, do not use: the in-sample crosswalk figures 60.9 / 67.5 / 72.8%. They are "
    "resubstitution and are not comparable held-out results.\n\n"
    "Subcategory would give 86.5% top-3, but it is not reliably supplied at live intake, so it is "
    "an upper bound and not a live number.\n\n"
    "THE WITHDRAWN ROUTING-OUTCOME CLAIM, if asked. The claim held on validation 2024 (augmented +26.77 days, SE "
    "4.04) and collapsed on the untouched 2025 test year (-2.35, SE 3.50). Three separable "
    "causes:\n"
    "(a) The population was selected on a post-treatment variable. The old 'correct' label was "
    "read off the closing remark, which routing can itself affect. Robustness rung R0 gives a "
    "delta RMSE of +0.0305 (SE 0.0138, t=2.21); rung R1, changing nothing but the population, "
    "gives +0.0002 (SE 0.0059, t=0.04). A 125x collapse. The old restriction discarded 63% of "
    "cases and kept the ones selected on outcome.\n"
    "(b) Overlap collapsed on the test year: match rate 0.536 to 0.156, median propensity 0.412 "
    "to 0.108. For 84% of test cases the recommended route essentially never occurred in that "
    "cell. The direct estimator extrapolates anyway and returns its largest figure ever, 30.53 "
    "days; the doubly-robust estimator returns -2.35. A 33-day gap between estimators is the "
    "diagnosis.\n"
    "(c) Censoring tripled: 0.031 train, 0.092 validation, 0.344 test. A third of 2025 cases were "
    "still open at the snapshot.\n\n"
    "Also worth saying: '11 to 23' was never a confidence interval. It was two estimators "
    "disagreeing by a factor of two on identical data, which was the warning sign from the start.\n\n"
    "ITEM 4: an audit of evaluation/stats.py found a fixed z=1.96 used for every confidence level "
    "and a missing small-cluster t correction. Both fixed; every clustered interval in the repo "
    "widened 4 to 10%.\n\n"
    "Sources: outputs/experiments/routing_outcome/{robustness,ope_val_ridge,ope_test_ridge,"
    "outputs/experiments/routing_outcome/{robustness,ope_val_ridge,ope_test_ridge,censoring}.json; "
    "commits 879c24c and 365e3b4; docs/experiments/superseded/README.md."
)

# ══ 10 · Triage and drafting ══════════════════════════════════════════════════
s = clone(prs, A_TWOGROUP)
sh = S(s)
set_text(sh[0], "QUALITY · THE TWO ENDS")
set_text(sh[1], "Triage works. Drafting does not.")
set_text(sh[2], "Catching what cannot be acted on — our strongest result")
set_text(sh[4], "All 13 cases needing officer review were caught, out of 57 held-out cases.")
set_text(sh[6], "Only 3 of 44 ordinary complaints were wrongly sent for review.")
set_text(sh[12], "A plain word-counting method beat a large multilingual model, 13 of 13 against 9 of 13.")
set_text(sh[7], "Drafting a summary — our weakest")
set_text(sh[14], "8 of 26 summaries were usable without an edit.")
set_text(sh[16], "15% carried personal details back into the summary text.")
set_text(sh[8], "Both were measured the same way. That is why we can tell them apart.")
s.notes_slide.notes_text_frame.text = (
    "Sources: models/actionability/benchmark.json and outputs/evaluation/summary_development_v1.json.\n\n"
    "TRIAGE, n=57 held-out: accuracy 94.74% (95% Wilson 85.63-98.19), review recall 13/13 = 100% "
    "(77.19-100), actionable-review rate 3/44 = 6.82% (2.35-18.23), F1 89.66%, ROC-AUC 99.13%. "
    "Selected model is TF-IDF word+char at threshold 0.435. The frozen MuRIL probe got 9/13 "
    "recall and 85.96% accuracy. The cheap method won, and that is the procurement point.\n\n"
    "Gold set: 180 PII-redacted cases labelled in two independent contexts plus a resolver, 174 "
    "canonical after excluding 6 uncertain judgments. Raw agreement 99.44%, Cohen's kappa 0.985.\n\n"
    "Fifty-seven cases is a small test. Say so. The intervals are wide and they are on the slide "
    "in the notes for a reason.\n\n"
    "DRAFTING, n=26 scored: critical-fact recall 65.48% (54.83-74.76) over 84 facts, usable "
    "unedited 8/26 = 30.77% (16.50-49.99), residual PII in output 4/26 = 15.38% (6.15-33.53). "
    "Zero unsupported claims and zero contradictions, but the upper bound on each is 12.87%. "
    "Mean usefulness 1.5 against the DSI reference of 1.9. One judge, not an officer.\n\n"
    "The English-only gate skipped all 4 coherent Odia cases, a 100% miss on that slice. The "
    "summary_release artifact is missing and is one of the eight publication blockers.\n\n"
    "Local BART, bart-large-cnn rev 37f520fa."
)

# ══ 11 · What we cannot claim on quality ══════════════════════════════════════
s = clone(prs, A_NUM3)
sh = S(s)
set_text(sh[0], "QUALITY · THE GAPS")
set_text(sh[1], "What we cannot claim about quality")
set_text(sh[3], "There is no precision figure for redaction.")
set_text(sh[5], "824 predicted spans against 480 marked. We cannot separate a miss from an over-redaction.")
set_text(sh[8], "There is no accuracy figure for text extraction.")
set_text(sh[10], "No hand-transcribed ground truth was ever produced, so no system can be scored.")
set_text(sh[13], "We claim no gain from duplicate detection.")
set_text(sh[15], "The index collapses 55,544 filings to 10,963 problems. Nobody has checked whether those merges are right.")
set_text(sh[16], "Nothing here is release-eligible. Every figure in this deck is development evidence.")
s.notes_slide.notes_text_frame.text = (
    "Sources: outputs/evaluation/pii_release.json, docs/PERFORMANCE.md section 6, "
    "outputs/findings/confirmed_duplicates.md, outputs/benchmark/full_benchmark.json.\n\n"
    "37,299 = 21,117 'already taken up' plus 16,182 'duplicate copy', re-run 8 August. NOTE: "
    "outputs/findings/duplicate_recall.md still holds a buggy 18,432 from a template-matching "
    "defect, and 34,671 and 39,937 are older superseded totals. Do not read those files.\n\n"
    "On text extraction: a commercial vision model returns 1.3345x as many characters as ours on "
    "56 paired pages. Neither fact says which is right. The accuracy row was dropped from the "
    "delivery table because no owner was ever named for a hand-transcription sample, issue #53.\n\n"
    "The eight publication blockers: pipeline_latency_release, pii_officer_release, "
    "actionability_officer_release, categorization_release, summary_release, "
    "routing_correct_authority_release, pilot_operational_effects, pilot_citizen_outcomes.\n\n"
    "If pushed on why so little is claimed: because the alternative is claiming things we cannot "
    "defend, and this deck has to survive the room checking it."
)

# ══ 12 · Closing ══════════════════════════════════════════════════════════════
s = clone(prs, A_CLOSING)
s.notes_slide.notes_text_frame.text = (
    "Close on the limits, not a summary.\n\n"
    "What we are not claiming: no officer minutes saved, no faster resolution, no satisfaction "
    "improvement, no accuracy figure confirmed by an officer, no redaction precision, no "
    "deduplication increment, no routing gain.\n\n"
    "Nearly every effect worth proving is blocked on a log line or a timer, not on a model."
)

# ── drop the original reference slides, keep the built ones in order ──────────
sldIdLst = prs.slides._sldIdLst
for sldId in list(sldIdLst)[:n_original]:
    rId = sldId.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
    prs.part.drop_rel(rId)
    sldIdLst.remove(sldId)

prs.core_properties.title = "Janasunani 2.0 — Timing and Quality"
prs.core_properties.author = "Data, Policy and Innovation Centre"
prs.save(OUT)
print(f"Wrote {OUT} with {len(prs.slides._sldIdLst)} slides")
