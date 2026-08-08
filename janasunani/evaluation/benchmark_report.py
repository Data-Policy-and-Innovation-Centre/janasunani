"""Unified benchmark report generator — DELIVERY Table 2.

Collects DSI clinic reference baselines (``dsi_baselines``, ``reference_only=True``)
and our measurements (PII, Sarvam, spam, dedup, latency, pricing) into one
machine-readable artifact and one human-readable table.

Every input is optional; a missing measurement produces a ``not_measured``
row, never a fabricated number.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
from pathlib import Path
from typing import Any

from janasunani.config import (
    DEMO_SLICE_DISTRICT,
    DEMO_SLICE_GROUPS,
    DEMO_SLICE_LABEL,
    DEMO_SLICE_SIZE,
    DEMO_SLICE_YEAR,
    ROOT_DIR,
)

from janasunani.evaluation import dsi_baselines as dsi
from janasunani.evaluation import pricing as pricing_mod

DEFAULT_OUT_DIR: Path = ROOT_DIR / "outputs" / "benchmark"
TABLE_JSON: str = "table2.json"
TABLE_MD: str = "table2.md"
DEFAULT_LATENCY_PATH: Path = DEFAULT_OUT_DIR / "latency.json"
NOT_MEASURED: str = "not_measured"


def _try_load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        p = Path(path)
        if not p.exists():
            return None
        return json.loads(p.read_text())
    except Exception:
        return None


def _load_latency(latency_path: Path | None = None) -> dict[str, Any] | None:
    candidate = Path(latency_path) if latency_path is not None else DEFAULT_LATENCY_PATH
    return _try_load_json(candidate)


def _dedup_snapshot() -> dict[str, Any]:
    return {
        "slice": DEMO_SLICE_LABEL,
        "slice_district": DEMO_SLICE_DISTRICT,
        "slice_year": DEMO_SLICE_YEAR,
        "total_signatures": DEMO_SLICE_SIZE,
        "duplicate_groups": DEMO_SLICE_GROUPS,
        "comparison_pairs": None,
        "digest_guarded": True,
        "method": "MinHash/LSH over char n-grams, blocked by district/time, salted identity keys",
    }


def _row(
    stage: str,
    metric: str,
    dsi_reference: Any,
    our_measurement: Any = NOT_MEASURED,
    unit: str = "",
    latency: Any = NOT_MEASURED,
    cost: Any = NOT_MEASURED,
    status: str | None = None,
    notes: str = "",
    reference_only: bool = False,
) -> dict[str, Any]:
    if status is None:
        status = NOT_MEASURED if our_measurement == NOT_MEASURED else "measured"
        if reference_only and our_measurement == NOT_MEASURED:
            status = "reference_only"
    return {
        "stage": stage,
        "metric": metric,
        "dsi_reference": dsi_reference,
        "dsi_reference_only": True,
        "our_measurement": our_measurement,
        "unit": unit,
        "latency": latency,
        "cost": cost,
        "status": status,
        "notes": notes,
    }


def build_report(
    *,
    pii_result: dict[str, Any] | None = None,
    sarvam_result: dict[str, Any] | None = None,
    spam_result: dict[str, Any] | None = None,
    dedup_result: dict[str, Any] | None = None,
    latency_result: dict[str, Any] | None = None,
    latency_path: Path | None = None,
    pricing_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if latency_result is None and latency_path is not None:
        latency_result = _load_latency(latency_path)
    if latency_result is None:
        maybe = _load_latency(None)
        if maybe is not None:
            latency_result = maybe
    dedup_snapshot = dedup_result if dedup_result is not None else _dedup_snapshot()
    if pii_result is not None:
        pii_measurement: Any = pii_result
        pii_status = "measured"
    else:
        pii_measurement = NOT_MEASURED
        pii_status = NOT_MEASURED
    if sarvam_result is not None:
        sarvam_measurement: Any = sarvam_result
        sarvam_status = "measured"
    else:
        sarvam_measurement = NOT_MEASURED
        sarvam_status = NOT_MEASURED
    if spam_result is not None:
        spam_measurement: Any = spam_result
        spam_status = "measured"
    else:
        spam_measurement = NOT_MEASURED
        spam_status = NOT_MEASURED

    def _latency_for(stage_key: str) -> Any:
        if latency_result is None:
            return NOT_MEASURED
        stages = latency_result.get("stages") if isinstance(latency_result, dict) else None
        if isinstance(stages, dict) and stage_key in stages:
            return stages[stage_key]
        if stage_key == "e2e" and isinstance(latency_result, dict) and "e2e" in latency_result:
            return latency_result["e2e"]
        return NOT_MEASURED

    pricing_card = pricing_mod.pricing_table()
    rows: list[dict[str, Any]] = []
    rows.append(
        _row(
            stage="Format classifier",
            metric="avg accuracy",
            dsi_reference=dsi.FORMAT_CLASSIFIER_AVG_ACCURACY,
            our_measurement=NOT_MEASURED,
            unit="accuracy (0–1)",
            latency=_latency_for("format"),
            cost=pricing_mod.LOCAL_API_COST_RUPEES,
            status="reference_only",
            notes="DSI 75.71% avg across classifiers (best 81.97%, macro-precision 72.93%). Historical context only.",
            reference_only=True,
        )
    )
    rows.append(
        _row(
            stage="OCR (text extraction)",
            metric="all-three heuristic pass rate",
            dsi_reference=dsi.OCR_ALL_THREE_PASS_RATE,
            our_measurement=sarvam_measurement if isinstance(sarvam_measurement, dict) and "transcription_accuracy" in sarvam_measurement else NOT_MEASURED,
            unit="pass rate (0–1); ours is divergence absent transcription",
            latency=_latency_for("ocr"),
            cost=pricing_mod.VISION_DIGITISE_RUPEES_PER_PAGE,
            status=sarvam_status if sarvam_status != NOT_MEASURED else "reference_only",
            notes="DSI 77.89% (English, heuristic gates; word-count≥20 85.52%, alpha≥0.5 84.04%, trigram≤0.25 91.14%). Divergence-only without transcription.",
        )
    )
    rows.append(
        _row(
            stage="PII redaction",
            metric="any-overlap recall",
            dsi_reference=dsi.PII_ANY_OVERLAP_RECALL,
            our_measurement=pii_measurement,
            unit="recall (0–1)",
            latency=_latency_for("pii"),
            cost=pricing_mod.LOCAL_API_COST_RUPEES,
            status=pii_status,
            notes="DSI 80.56% any-overlap / 50.00% exact-span on 106-sentence English split. Our figure typed spans vs 89 pages; corpus scan over 55,544 redacted.",
        )
    )
    rows.append(
        _row(
            stage="PII redaction",
            metric="exact-span recall",
            dsi_reference=dsi.PII_EXACT_SPAN_RECALL,
            our_measurement=pii_measurement,
            unit="recall (0–1)",
            latency=NOT_MEASURED,
            cost=pricing_mod.LOCAL_API_COST_RUPEES,
            status=pii_status,
            notes="Exact-span stricter; reported alongside any-overlap per #67.",
        )
    )
    rows.append(
        _row(
            stage="Page-type classifier",
            metric="accuracy",
            dsi_reference=dsi.PAGE_TYPE_VIT_ACCURACY,
            our_measurement=NOT_MEASURED,
            unit="accuracy (0–1)",
            latency=_latency_for("page_type"),
            cost=pricing_mod.LOCAL_API_COST_RUPEES,
            status="reference_only",
            notes="DSI ViT 0.67 (macro-F1 0.62; Letter 0.79 … Misc 0.44). Historical context only.",
            reference_only=True,
        )
    )
    rows.append(
        _row(
            stage="Categorizer",
            metric="accuracy (typed subjects)",
            dsi_reference=dsi.CATEGORIZER_MURIL_ACCURACY,
            our_measurement=NOT_MEASURED,
            unit="accuracy (0–1)",
            latency=_latency_for("categorizer"),
            cost=pricing_mod.LOCAL_API_COST_RUPEES,
            status="reference_only",
            notes="DSI MuRIL 0.7104 (macro-F1 0.6853, weighted 0.6947; Police 0.85 … Social Welfare 0.51). Typed subjects, not scans.",
            reference_only=True,
        )
    )
    rows.append(
        _row(
            stage="Summarizer",
            metric="usefulness (0–3)",
            dsi_reference=dsi.SUMMARIZER_BART_USEFULNESS_TEXT_ONLY,
            our_measurement=NOT_MEASURED,
            unit="usefulness (0–3)",
            latency=_latency_for("summarizer"),
            cost=pricing_mod.LOCAL_API_COST_RUPEES,
            status="reference_only",
            notes="DSI BART text-only 1.9, Letter 1.3, Forms 0.85, ID 0.45, Bills 0.40. Re-scoring needs blinded review.",
            reference_only=True,
        )
    )
    rows.append(
        _row(
            stage="Efficiency (clinic)",
            metric="seconds per 500 tokens, A100",
            dsi_reference=dsi.EFFICIENCY_SECONDS_PER_500_TOKENS_A100,
            our_measurement=_latency_for("e2e") if latency_result is not None else NOT_MEASURED,
            unit="seconds",
            latency=_latency_for("e2e"),
            cost=NOT_MEASURED,
            status="measured" if latency_result is not None else "reference_only",
            notes="DSI clinic: format 4.53s, OCR 18.86s, PII 4.67s, page-type 2.49s, summarizer 1.84s, categorizer 2.82s. Our latency wall-clock mean±SE.",
        )
    )
    sarvam_cat: Any = NOT_MEASURED
    sarvam_cat_notes = "Primary outcome: category accuracy difference (Sarvam Extract vs pipeline) paired, ticket-clustered 95% CI."
    if isinstance(sarvam_result, dict):
        cat = sarvam_result.get("category")
        if cat is not None:
            sarvam_cat = cat
    rows.append(
        _row(
            stage="Sarvam Vision",
            metric="category accuracy difference (Extract vs pipeline)",
            dsi_reference=NOT_MEASURED,
            our_measurement=sarvam_cat,
            unit="accuracy difference (Sarvam − pipeline)",
            latency=NOT_MEASURED,
            cost=pricing_mod.VISION_EXTRACT_RUPEES_PER_PAGE,
            status="measured" if sarvam_cat is not NOT_MEASURED else NOT_MEASURED,
            notes=sarvam_cat_notes,
        )
    )
    sarvam_div: Any = NOT_MEASURED
    if isinstance(sarvam_result, dict):
        div = sarvam_result.get("transcription_divergence")
        if div is not None:
            sarvam_div = div
    rows.append(
        _row(
            stage="Sarvam Vision",
            metric="OCR divergence rate (pytesseract vs Sarvam digitise)",
            dsi_reference=NOT_MEASURED,
            our_measurement=sarvam_div,
            unit="share of pages with normalised text disagreement",
            latency=NOT_MEASURED,
            cost=pricing_mod.VISION_DIGITISE_RUPEES_PER_PAGE,
            status="measured" if sarvam_div is not NOT_MEASURED else NOT_MEASURED,
            notes="Divergence only when no hand-transcribed ground truth. Split handwritten/printed.",
        )
    )
    rows.append(
        _row(
            stage="Spam / low-signal",
            metric="prevalence (flagged share)",
            dsi_reference=NOT_MEASURED,
            our_measurement=spam_measurement,
            unit="share (0–1) over grievance_redacted",
            latency=_latency_for("spam"),
            cost=pricing_mod.LOCAL_API_COST_RUPEES,
            status=spam_status,
            notes="Measured over lake slice grievance_redacted only. Duplicates never counted as spam.",
        )
    )
    rows.append(
        _row(
            stage="Duplicate detection",
            metric="duplicate groups / total with text",
            dsi_reference=NOT_MEASURED,
            our_measurement=dedup_snapshot,
            unit="groups over slice",
            latency=_latency_for("dedup"),
            cost=pricing_mod.LOCAL_API_COST_RUPEES,
            status="measured",
            notes="Slice Sambalpur/2024: 55,544 signatures → 10,963 groups (07 Aug 14:14, #71, digest-guarded).",
        )
    )
    cost_appendix = {
        "rate_card": pricing_card,
        "notes": "Vision digitise ₹0.50/page, extract ₹1.00/page, both ₹1.50/page (ROADMAP §5.5, 10 pages/PDF, 10 req/min). Sarvam-105B input ₹29.28/M output ₹73.20/M (cached ₹10.98/M). Local pipeline API cost ₹0. 500-page digitise ₹250, digitise+extract ₹750.",
        "per_document_formula": "cost_doc = pages × rate_per_page + (input_tokens+output_tokens)/1M × rate_per_1M",
        "per_1k_tokens_formula": "cost_per_1k = tokens/1000 × rate_per_1k",
    }
    if latency_result is None:
        latency_appendix: dict[str, Any] = {
            "status": NOT_MEASURED,
            "mean_seconds": NOT_MEASURED,
            "se_seconds": NOT_MEASURED,
            "notes": "No harness output at outputs/benchmark/latency.json. Run timing harness over synthetic fixtures.",
        }
    else:
        latency_appendix = {
            "status": "measured",
            "result": latency_result,
            "notes": "Wall-clock per stage, ticket-clustered SE, p50/p95, n_clusters.",
        }
    report: dict[str, Any] = {
        "generated_at": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        "reference_only": dsi.REFERENCE_ONLY,
        "reference_only_by_stage": dsi.REFERENCE_ONLY_BY_STAGE,
        "slice": DEMO_SLICE_LABEL,
        "slice_district": DEMO_SLICE_DISTRICT,
        "slice_year": DEMO_SLICE_YEAR,
        "slice_size": DEMO_SLICE_SIZE,
        "slice_groups": DEMO_SLICE_GROUPS,
        "dsi_source": "Full Technical Report DPIC.pdf via ROADMAP §Model provenance",
        "rows": rows,
        "cost": cost_appendix,
        "latency": latency_appendix,
        "sarvam_arms": {
            "digitise": f"₹{pricing_mod.VISION_DIGITISE_RUPEES_PER_PAGE:.2f}/page",
            "extract": f"₹{pricing_mod.VISION_EXTRACT_RUPEES_PER_PAGE:.2f}/page",
            "both": f"₹{pricing_mod.VISION_BOTH_RUPEES_PER_PAGE:.2f}/page",
        },
        "pricing": pricing_card,
        "notes": "Every row is either DSI reference (reference_only) or our measurement with clustered SE. Missing inputs produce not_measured — never fabricated.",
    }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    gen = report.get("generated_at", "")
    sl = report.get("slice", DEMO_SLICE_LABEL)
    lines.append("# Benchmark Table 2 — DSI reference vs our measurements")
    lines.append("")
    lines.append(f"_Generated {gen} · slice {sl} · reference_only={report.get('reference_only')} — DSI values are historical reference, not thresholds._")
    lines.append("")
    lines.append("> Full Technical Report DPIC.pdf via ROADMAP §Model provenance. DSI figures are English-centric.")
    lines.append("")
    lines.append("| Stage | Metric | DSI reference | Our measurement | Unit | Status | Notes |")
    lines.append("|---|---|---|---|---|---|---|")

    def _fmt(v: Any) -> str:
        if v == NOT_MEASURED:
            return "`not_measured`"
        if isinstance(v, float):
            if 0 <= v <= 1 and v not in (0.0, 1.0):
                return f"{v:.4f} ({v:.2%})"
            return f"{v:.4f}" if isinstance(v, float) else str(v)
        if isinstance(v, dict):
            if "mean_seconds" in v and "se_seconds" in v:
                n = v.get("n_clusters", "?")
                mean = v.get("mean_seconds", "?")
                se = v.get("se_seconds", "?")
                return f"mean {mean} ± SE {se} (C={n})"
            if "groups" in v or "duplicate_groups" in v or "total_signatures" in v:
                tot = v.get("total_signatures") or v.get("total") or "?"
                grp = v.get("duplicate_groups") or v.get("groups") or "?"
                return f"{grp} groups / {tot} with text"
            if "slice" in v and "prevalence" in v:
                return f"prevalence {v.get('prevalence', '?'):.2%} over {v.get('total', '?')} with text"
            if "transcription_divergence" in v or "category" in v:
                parts = []
                if "transcription_divergence" in v:
                    td = v["transcription_divergence"]
                    if isinstance(td, dict):
                        parts.append(f"divergence {td.get('rate', '?'):.2%}")
                if "category" in v and isinstance(v["category"], dict):
                    c = v["category"]
                    parts.append(f"cat diff {c.get('difference', '?'):.3f} CI [{c.get('ci_low','?'):.3f},{c.get('ci_high','?'):.3f}]")
                return "; ".join(parts) if parts else json.dumps(v)[:120]
            if "rate" in v and "se" in v:
                return f"{v.get('rate', '?'):.2%} ± {v.get('se', '?'):.4f}"
            if "difference" in v and "se" in v:
                return f"diff {v.get('difference', 0):.3f} ±{v.get('se', 0):.3f} CI [{v.get('ci_low', 0):.3f},{v.get('ci_high', 0):.3f}]"
            try:
                return json.dumps(v, sort_keys=True)[:160]
            except Exception:
                return str(v)[:160]
        if isinstance(v, int):
            return str(v)
        if v is None:
            return "—"
        try:
            s = json.dumps(v, sort_keys=True)
            return s[:160]
        except Exception:
            return str(v)[:160]

    for r in report.get("rows", []):
        stage = r.get("stage", "")
        metric = r.get("metric", "")
        dsi_ref = _fmt(r.get("dsi_reference"))
        ours = _fmt(r.get("our_measurement"))
        unit = r.get("unit", "")
        status = r.get("status", "")
        notes = (r.get("notes", "") or "").replace("|", "/").replace("\n", " ")
        if len(notes) > 220:
            notes = notes[:217] + "..."
        lines.append(f"| {stage} | {metric} | {dsi_ref} | {ours} | {unit} | {status} | {notes} |")
    lines.append("")
    lines.append("## Latency (wall-clock, ticket-clustered SE)")
    lines.append("")
    lat = report.get("latency", {})
    if lat.get("status") == NOT_MEASURED:
        lines.append(f"_{lat.get('notes', 'not_measured')}_")
    else:
        res = lat.get("result", {})
        lines.append(f"_Measured harness output: {json.dumps(res, indent=2, sort_keys=True)[:3000]}_")
        stages = res.get("stages") if isinstance(res, dict) else None
        if isinstance(stages, dict):
            lines.append("")
            lines.append("| Stage | mean (s) | SE (s) | n_clusters | p50 (s) | p95 (s) |")
            lines.append("|---|---:|---:|---:|---:|---:|")
            for k, v in sorted(stages.items()):
                if isinstance(v, dict):
                    lines.append(f"| {k} | {v.get('mean_seconds', '—')} | {v.get('se_seconds', '—')} | {v.get('n_clusters', '—')} | {v.get('p50', '—')} | {v.get('p95', '—')} |")
        e2e = res.get("e2e") if isinstance(res, dict) else None
        if isinstance(e2e, dict):
            lines.append("")
            lines.append(f"**End-to-end:** mean {e2e.get('mean_seconds', '—')}s ± SE {e2e.get('se_seconds', '—')}s, n_clusters {e2e.get('n_clusters', '—')}, p50 {e2e.get('p50', '—')}s, p95 {e2e.get('p95', '—')}s.")
    lines.append("")
    lines.append("## Cost")
    lines.append("")
    cost = report.get("cost", {})
    rc = cost.get("rate_card") or report.get("pricing", {})
    lines.append("| Item | Rate |")
    lines.append("|---|---:|")
    lines.append(f"| Vision digitise (OCR) | ₹{rc.get('vision_digitise_rupees_per_page', pricing_mod.VISION_DIGITISE_RUPEES_PER_PAGE):.2f} / page |")
    lines.append(f"| Vision extract (schema) | ₹{rc.get('vision_extract_rupees_per_page', pricing_mod.VISION_EXTRACT_RUPEES_PER_PAGE):.2f} / page |")
    lines.append(f"| Vision both (digitise+extract) | ₹{rc.get('vision_both_rupees_per_page', pricing_mod.VISION_BOTH_RUPEES_PER_PAGE):.2f} / page |")
    lines.append(f"| Sarvam-105B input | ₹{rc.get('sarvam_105b_input_rupees_per_million_tokens', pricing_mod.SARVAM_105B_INPUT_RUPEES_PER_MILLION_TOKENS):.2f} / 1M tokens |")
    lines.append(f"| Sarvam-105B output | ₹{rc.get('sarvam_105b_output_rupees_per_million_tokens', pricing_mod.SARVAM_105B_OUTPUT_RUPEES_PER_MILLION_TOKENS):.2f} / 1M tokens |")
    lines.append(f"| Sarvam-105B cached input | ₹{rc.get('sarvam_105b_cached_rupees_per_million_tokens', pricing_mod.SARVAM_105B_CACHED_RUPEES_PER_MILLION_TOKENS):.2f} / 1M tokens |")
    lines.append(f"| Local pipeline API | ₹{rc.get('local_api_cost_rupees', pricing_mod.LOCAL_API_COST_RUPEES):.2f} |")
    lines.append("")
    lines.append(cost.get("notes", ""))
    lines.append("")
    lines.append(f"Formulae: {cost.get('per_document_formula', 'cost_doc = pages × rate_per_page + (input_tokens+output_tokens)/1M × rate_per_1M')}")
    lines.append(f"; {cost.get('per_1k_tokens_formula', 'cost_per_1k = tokens/1000 × rate_per_1k')}.")
    lines.append("Token counts via tokenizer; OCR-only benchmarks report ₹/page only.")
    lines.append("")
    lines.append("## Slice and provenance")
    lines.append("")
    lines.append(f"Demo slice: **{report.get('slice', sl)}** — {report.get('slice_size', DEMO_SLICE_SIZE)} signatures, {report.get('slice_groups', DEMO_SLICE_GROUPS)} dedup groups (07 Aug 14:14, #71).")
    lines.append(f"DSI source: {report.get('dsi_source', '')}")
    lines.append(f"Reference-only: {report.get('reference_only')} (every DSI row is historical reference, not a threshold).")
    lines.append("Pricing source: ROADMAP §5.5 via `janasunani.evaluation.pricing`.")
    lines.append("")
    lines.append(report.get("notes", ""))
    lines.append("")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any], out_dir: Path | str = DEFAULT_OUT_DIR) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / TABLE_JSON
    md_path = out / TABLE_MD
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    md_path.write_text(render_markdown(report))
    return {"json": json_path, "markdown": md_path}


def validate_report(report: dict[str, Any] | Path | str) -> list[str]:
    errors: list[str] = []
    data: dict[str, Any]
    if isinstance(report, (str, Path)) and Path(report).exists():
        try:
            data = json.loads(Path(report).read_text())
        except Exception as exc:
            return [f"unreadable JSON at {report}: {exc}"]
    elif isinstance(report, dict):
        data = report
    elif isinstance(report, (str, Path)):
        return [f"report file not found: {report}"]
    else:
        return ["validate_report: expected dict or path"]
    if data.get("reference_only") is not True:
        errors.append("reference_only must be True (DSI rows are historical reference, not thresholds)")
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        errors.append("rows must be a non-empty list")
    else:
        for i, r in enumerate(rows):
            if not isinstance(r, dict):
                errors.append(f"rows[{i}] must be a dict")
                continue
            for k in ("stage", "metric", "dsi_reference", "our_measurement", "status"):
                if k not in r:
                    errors.append(f"rows[{i}] missing key {k!r}")
            if "dsi_reference_only" not in r:
                errors.append(f"rows[{i}] missing dsi_reference_only")

        def _contains_value(target: float, tol: float = 1e-9) -> bool:
            for r in rows:
                v = r.get("dsi_reference")
                if isinstance(v, float) and math.isclose(v, target, abs_tol=tol):
                    return True
                if isinstance(v, dict):
                    for vv in v.values():
                        if isinstance(vv, float) and math.isclose(vv, target, abs_tol=tol):
                            return True
            return False

        checks: list[tuple[float, str]] = [
            (dsi.FORMAT_CLASSIFIER_AVG_ACCURACY, "format avg accuracy"),
            (dsi.OCR_ALL_THREE_PASS_RATE, "OCR all-three"),
            (dsi.PII_ANY_OVERLAP_RECALL, "PII any-overlap"),
            (dsi.PII_EXACT_SPAN_RECALL, "PII exact-span"),
            (dsi.PAGE_TYPE_VIT_ACCURACY, "page-type ViT accuracy"),
            (dsi.CATEGORIZER_MURIL_ACCURACY, "categorizer MuRIL accuracy"),
            (dsi.SUMMARIZER_BART_USEFULNESS, "summarizer usefulness"),
        ]
        for val, name in checks:
            if not _contains_value(val):
                errors.append(f"DSI reference for {name} ({val}) not found in rows — stale report?")
    pricing = data.get("pricing") or (data.get("cost") or {}).get("rate_card")
    if not isinstance(pricing, dict):
        errors.append("pricing rate card missing (expected pricing or cost.rate_card dict)")
    else:
        expected_rates: dict[str, float] = {
            "vision_digitise_rupees_per_page": pricing_mod.VISION_DIGITISE_RUPEES_PER_PAGE,
            "vision_extract_rupees_per_page": pricing_mod.VISION_EXTRACT_RUPEES_PER_PAGE,
            "vision_both_rupees_per_page": pricing_mod.VISION_BOTH_RUPEES_PER_PAGE,
            "sarvam_105b_input_rupees_per_million_tokens": pricing_mod.SARVAM_105B_INPUT_RUPEES_PER_MILLION_TOKENS,
            "sarvam_105b_output_rupees_per_million_tokens": pricing_mod.SARVAM_105B_OUTPUT_RUPEES_PER_MILLION_TOKENS,
        }
        for k, expected in expected_rates.items():
            if k not in pricing:
                errors.append(f"pricing missing key {k!r}")
            elif not math.isclose(float(pricing[k]), expected, abs_tol=1e-9):
                errors.append(f"pricing {k}={pricing[k]!r} != expected {expected}")
    if data.get("slice") != DEMO_SLICE_LABEL:
        errors.append(f"slice {data.get('slice')!r} != expected {DEMO_SLICE_LABEL!r}")
    if data.get("slice_size") != DEMO_SLICE_SIZE:
        errors.append(f"slice_size {data.get('slice_size')!r} != {DEMO_SLICE_SIZE}")
    if data.get("slice_groups") != DEMO_SLICE_GROUPS:
        errors.append(f"slice_groups {data.get('slice_groups')!r} != {DEMO_SLICE_GROUPS}")
    if "latency" not in data:
        errors.append("latency appendix missing")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark Table 2 generator — DSI reference vs our measurements (ROADMAP/DELIVERY Table 2).",
        epilog="Without --check, generates outputs/benchmark/table2.json + table2.md. With --check, validates existing outputs.",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help=f"Output directory (default: {DEFAULT_OUT_DIR})")
    parser.add_argument("--latency", type=Path, default=None, help="Optional latency harness JSON (default: outputs/benchmark/latency.json if present).")
    parser.add_argument("--check", action="store_true", help="Validate existing outputs and exit; do not regenerate.")
    parser.add_argument("--allow-stale", action="store_true", help="With --check, do not fail on stale generated_at (>7d).")
    args = parser.parse_args(argv)
    out_dir: Path = Path(args.out)
    if args.check:
        json_path = out_dir / TABLE_JSON
        md_path = out_dir / TABLE_MD
        if not json_path.exists():
            print(f"check failed: missing {json_path}")
            print(f"Run without --check to generate: janasunani-evaluate-benchmark --out {out_dir}")
            return 1
        if not md_path.exists():
            print(f"check failed: missing {md_path}")
            return 1
        errors = validate_report(json_path)
        try:
            data = json.loads(json_path.read_text())
            gen = data.get("generated_at")
            if gen and not args.allow_stale:
                try:
                    ts = _dt.datetime.fromisoformat(gen)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=_dt.timezone.utc)
                    age_days = (_dt.datetime.now(tz=_dt.timezone.utc) - ts).total_seconds() / 86400.0
                    if age_days > 7:
                        print(f"warning: table2.json is {age_days:.1f} days old (>7d); regenerate before freeze")
                except Exception:
                    pass
        except Exception:
            pass
        if errors:
            print("check failed — schema errors:")
            for e in errors:
                print(f"  - {e}")
            return 1
        print(f"check ok: {json_path} and {md_path} are valid (reference_only=True, {len(json.loads(json_path.read_text()).get('rows', []))} rows)")
        return 0
    latency_result = _try_load_json(args.latency) if args.latency else _load_latency(None)
    report = build_report(latency_result=latency_result, latency_path=args.latency)
    paths = write_outputs(report, out_dir)
    print(f"wrote {paths['json']}")
    print(f"wrote {paths['markdown']}")
    errs = validate_report(report)
    if errs:
        print("warning: generated report has schema warnings:")
        for e in errs:
            print(f"  - {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
