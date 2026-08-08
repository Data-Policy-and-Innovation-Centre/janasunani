"""DSI clinic reference baselines — frozen from Full Technical Report DPIC.pdf.

Source of truth is the PDF transcribed in :doc:`ROADMAP` §Model provenance &
DSI baselines (hard rule). Values are **English-centric** and measured on
the DSI team's own splits — they are historical reference, not thresholds.
Our measurements sit alongside, never as pass/fail gates (per
DELIVERY.md Table 2 caveats: different samples, English-only DSI,
name-heavy PII gold).

Every consumer must treat these as ``reference_only=True``. The report
renderer (:mod:`janasunani.evaluation.benchmark_report`) labels the DSI
column accordingly and never colours it as a target.

Do not parse the PDF at runtime — this module is the codified copy.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Global flag — every row in this module is reference-only
# ---------------------------------------------------------------------------
REFERENCE_ONLY: bool = True

# ---------------------------------------------------------------------------
# Stage baselines — Table in docs/plans/2026-08-08-demo-integration-rehearsal.md
# Part 5 and ROADMAP §Model provenance.
# ---------------------------------------------------------------------------

# Format classifier — XGBoost + OpenCV + SMOTE, 1_000 hand-labelled pages
FORMAT_CLASSIFIER_AVG_ACCURACY: float = 0.7571
"""Average accuracy across classifiers (frozen DSI value, reference only)."""
FORMAT_CLASSIFIER_BEST_ACCURACY: float = 0.8197
FORMAT_CLASSIFIER_MACRO_PRECISION: float = 0.7293

# OCR — DeepSeek-OCR, English only, 96_469 English pages, heuristic gates
OCR_WORD_COUNT_GE20_RATE: float = 0.8552
OCR_ALPHA_GE05_RATE: float = 0.8404
OCR_TRIGRAM_LE025_RATE: float = 0.9114
OCR_ALL_THREE_PASS_RATE: float = 0.7789
"""Share of pages passing all three heuristic gates (word_count>=20,
alpha>=0.5, trigram<=0.25). The Table 2 headline for OCR."""

# PII tagger — RoBERTa BIO, 106-sentence / 2_126-token val split
PII_ANY_OVERLAP_RECALL: float = 0.8056
"""Coverage any-overlap (typed-untyped) — the DSI-comparable figure."""
PII_EXACT_SPAN_RECALL: float = 0.50
"""Exact-span recall."""
# Aliases that match names used elsewhere in the codebase
PII_ANY_OVERLAP: float = PII_ANY_OVERLAP_RECALL
PII_EXACT_SPAN: float = PII_EXACT_SPAN_RECALL

# Page-type classifier — ViT, 1_500 pages 70/30
PAGE_TYPE_VIT_ACCURACY: float = 0.67
PAGE_TYPE_VIT_MACRO_F1: float = 0.62

# Categorizer — MuRIL fine-tuned on 6_598, tested on 65_999 grievances
CATEGORIZER_MURIL_ACCURACY: float = 0.7104
CATEGORIZER_MURIL_MACRO_F1: float = 0.6853
CATEGORIZER_MURIL_WEIGHTED_F1: float = 0.6947

# Summarizer — BART, 500-page qualitative 0–3 usefulness
SUMMARIZER_BART_USEFULNESS: float = 1.9
"""Text-only usefulness (0–3). Other page-type scores kept for context."""
SUMMARIZER_BART_USEFULNESS_TEXT_ONLY: float = 1.9
SUMMARIZER_BART_USEFULNESS_LETTER: float = 1.3
SUMMARIZER_BART_USEFULNESS_FORMS: float = 0.85
SUMMARIZER_BART_USEFULNESS_IDENTIFICATION: float = 0.45
SUMMARIZER_BART_USEFULNESS_BILLS: float = 0.40

# ---------------------------------------------------------------------------
# Efficiency — clinic, per 500 tokens, A100 (ROADMAP)
# ---------------------------------------------------------------------------
EFFICIENCY_SECONDS_PER_500_TOKENS_A100: dict[str, float] = {
    "format": 4.53,
    "ocr": 18.86,
    "pii": 4.67,
    "page_type": 2.49,
    "summarizer": 1.84,
    "categorizer": 2.82,
}

# Backward-compatible alias for the minimal baselines file that used a
# shorter name. Both names resolve to the same dict object.
DSI_EFFICIENCY_SECONDS_PER_500_TOKENS: dict[str, float] = EFFICIENCY_SECONDS_PER_500_TOKENS_A100

# ---------------------------------------------------------------------------
# Structured aggregation for report consumers
# ---------------------------------------------------------------------------
# Minimal Table 2 entries (kept for backward compatibility with the
# 92-line version that only exposed DSI_BASELINES + DSI_EFFICIENCY...)
DSI_BASELINES: dict[str, dict[str, object]] = {
    "format_classifier": {
        "stage": "Format classifier",
        "model": "XGBoost + OpenCV + SMOTE",
        "sample": "1,000 hand-labelled pages",
        "metric": "avg accuracy",
        "value": FORMAT_CLASSIFIER_AVG_ACCURACY,
        "value_display": "75.71%",
        "note": "best-model acc 81.97% / macro-precision 72.93%",
        "reference_only": REFERENCE_ONLY,
    },
    "ocr_english_heuristic": {
        "stage": "OCR (English, heuristic gates)",
        "model": "DeepSeek-OCR",
        "sample": "96,469 English pages, heuristic quality gates (no transcription ground truth)",
        "metric": "all-three pass rate",
        "value": OCR_ALL_THREE_PASS_RATE,
        "value_display": "77.89%",
        "note": "word-count≥20 85.52% / alpha≥0.5 84.04% / trigram≤0.25 91.14%",
        "reference_only": REFERENCE_ONLY,
    },
    # Alias for consumers that look up "ocr"
    "ocr": {
        "stage": "OCR (English, heuristic gates)",
        "model": "DeepSeek-OCR, English only",
        "sample": "96,469 English pages, heuristic gates",
        "metric": "all-three pass rate",
        "value": OCR_ALL_THREE_PASS_RATE,
        "value_display": "77.89%",
        "components": {
            "word_count_ge20": OCR_WORD_COUNT_GE20_RATE,
            "alpha_ge05": OCR_ALPHA_GE05_RATE,
            "trigram_le025": OCR_TRIGRAM_LE025_RATE,
            "all_three": OCR_ALL_THREE_PASS_RATE,
        },
        "reference_only": REFERENCE_ONLY,
    },
    "pii_any_overlap": {
        "stage": "PII",
        "model": "RoBERTa BIO",
        "sample": "106-sentence / 2,126-token val split",
        "metric": "coverage any-overlap",
        "value": PII_ANY_OVERLAP_RECALL,
        "value_display": "80.56%",
        "note": "exact-span 50.00%; B-PII F1 0.929, I-PII F1 0.730",
        "reference_only": REFERENCE_ONLY,
    },
    "pii_exact_span": {
        "stage": "PII",
        "model": "RoBERTa BIO",
        "sample": "106-sentence / 2,126-token val split",
        "metric": "coverage exact-span",
        "value": PII_EXACT_SPAN_RECALL,
        "value_display": "50.00%",
        "reference_only": REFERENCE_ONLY,
    },
    # Combined pii entry for consumers that expect a single key
    "pii": {
        "stage": "PII",
        "model": "RoBERTa BIO",
        "sample": "106-sentence / 2,126-token val split",
        "metric": "any-overlap / exact-span recall",
        "value": PII_ANY_OVERLAP_RECALL,
        "value_exact": PII_EXACT_SPAN_RECALL,
        "value_display": "80.56% / 50.00%",
        "reference_only": REFERENCE_ONLY,
    },
    "page_type_vit": {
        "stage": "Page-type ViT",
        "model": "ViT",
        "sample": "1,500 pages, 70/30",
        "metric": "accuracy",
        "value": PAGE_TYPE_VIT_ACCURACY,
        "value_display": "0.67",
        "note": "macro-F1 0.62; per-class Letter 0.79 … Misc 0.44",
        "reference_only": REFERENCE_ONLY,
    },
    "page_type": {
        "stage": "Page-type classifier",
        "model": "ViT",
        "sample": "1,500 pages, 70/30",
        "metric": "accuracy",
        "value": PAGE_TYPE_VIT_ACCURACY,
        "value_display": "0.67",
        "reference_only": REFERENCE_ONLY,
    },
    "categorizer_muril": {
        "stage": "Categorizer",
        "model": "MuRIL fine-tuned on 6,598",
        "sample": "65,999-grievance test",
        "metric": "accuracy",
        "value": CATEGORIZER_MURIL_ACCURACY,
        "value_display": "0.7104",
        "note": "macro-F1 0.6853 / weighted-F1 0.6947; per-class Police 0.85 … Social Welfare 0.51",
        "reference_only": REFERENCE_ONLY,
    },
    "categorizer": {
        "stage": "Categorizer",
        "model": "MuRIL",
        "sample": "65,999-grievance test (trained on 6,598)",
        "metric": "accuracy (typed subjects)",
        "value": CATEGORIZER_MURIL_ACCURACY,
        "value_display": "0.7104",
        "reference_only": REFERENCE_ONLY,
    },
    "summarizer_bart_usefulness": {
        "stage": "Summarizer",
        "model": "BART",
        "sample": "500-page qualitative, 0–3 usefulness",
        "metric": "usefulness (text-only)",
        "value": SUMMARIZER_BART_USEFULNESS,
        "value_display": "1.9",
        "note": "Letter 1.3, Forms 0.85, ID 0.45, Bills 0.40; ROUGE uninformative",
        "reference_only": REFERENCE_ONLY,
    },
    "summarizer": {
        "stage": "Summarizer",
        "model": "BART",
        "sample": "500-page qualitative, single reviewer",
        "metric": "usefulness (0–3)",
        "value": SUMMARIZER_BART_USEFULNESS,
        "value_display": "1.9",
        "reference_only": REFERENCE_ONLY,
    },
    "efficiency": {
        "stage": "Efficiency (clinic)",
        "model": "A100 per 500 tokens",
        "sample": "clinic measurement",
        "metric": "seconds per 500 tokens, A100",
        "value": EFFICIENCY_SECONDS_PER_500_TOKENS_A100,
        "value_display": "format 4.53s / OCR 18.86s / PII 4.67s / page-type 2.49s / summarizer 1.84s / categorizer 2.82s",
        "components": EFFICIENCY_SECONDS_PER_500_TOKENS_A100,
        "reference_only": REFERENCE_ONLY,
    },
}

# Whether each baseline is reference-only — the flag the report must surface.
REFERENCE_ONLY_BY_STAGE: dict[str, bool] = {k: REFERENCE_ONLY for k in DSI_BASELINES}

__all__ = [
    "REFERENCE_ONLY",
    "FORMAT_CLASSIFIER_AVG_ACCURACY",
    "FORMAT_CLASSIFIER_BEST_ACCURACY",
    "FORMAT_CLASSIFIER_MACRO_PRECISION",
    "OCR_WORD_COUNT_GE20_RATE",
    "OCR_ALPHA_GE05_RATE",
    "OCR_TRIGRAM_LE025_RATE",
    "OCR_ALL_THREE_PASS_RATE",
    "PII_ANY_OVERLAP_RECALL",
    "PII_EXACT_SPAN_RECALL",
    "PII_ANY_OVERLAP",
    "PII_EXACT_SPAN",
    "PAGE_TYPE_VIT_ACCURACY",
    "PAGE_TYPE_VIT_MACRO_F1",
    "CATEGORIZER_MURIL_ACCURACY",
    "CATEGORIZER_MURIL_MACRO_F1",
    "CATEGORIZER_MURIL_WEIGHTED_F1",
    "SUMMARIZER_BART_USEFULNESS",
    "SUMMARIZER_BART_USEFULNESS_TEXT_ONLY",
    "SUMMARIZER_BART_USEFULNESS_LETTER",
    "SUMMARIZER_BART_USEFULNESS_FORMS",
    "SUMMARIZER_BART_USEFULNESS_IDENTIFICATION",
    "SUMMARIZER_BART_USEFULNESS_BILLS",
    "EFFICIENCY_SECONDS_PER_500_TOKENS_A100",
    "DSI_EFFICIENCY_SECONDS_PER_500_TOKENS",
    "DSI_BASELINES",
    "REFERENCE_ONLY_BY_STAGE",
]
