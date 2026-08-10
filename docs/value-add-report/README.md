# Value-add report — Janasunani 2.0

**For:** non-technical stakeholders (Secretaries, Collectors, programme officers, review committees).
**What:** baseline vs system improvement on document processing (time, accuracy/precision/recall per stage), spam & dedup, routing optimisation, and the intelligence layer (what & who people complain about, hotspots, spikes, real-time officer help).

## Files

- `Janasunani_2.0_Value_Add_Report_August_2026.docx` — the Word report (≈18 min read; executive summary self-contained). Open in Word / LibreOffice / Google Docs.
- `figures/` — the generated charts embedded in the report (also usable as standalone slides).

## Verification status — 2026-08-10

The report's headline figures were re-checked against the code on 10 August, before this
directory was committed. Verified reproducing exactly:

| Claim in the report | Re-measured | How |
|---|---|---|
| PII overlap recall 77.9%, exact 55.0%, coverage 78.3% | ✅ 0.7792 / 0.5500 / 0.7833 | `python -m janasunani.evaluation.pii_scorecard --gold …` |
| PII per entity: PHONE 82.8 / AADHAAR 85.7 / EMAIL 75.0 / NAME 77.7 | ✅ exact match | same run |
| Officer-confirmed duplicates 37,299 (21,117 + 16,182) | ✅ | `outputs/findings/confirmed_duplicates.csv`, `discard_reason_families.csv` |
| Crosswalk argmax 60.9 / 67.5 / 72.8% | ✅ consistent across ROADMAP, DELIVERY, `crosswalk.py` | — |
| Crosswalk table sizes 34 / 257 / 971 / 5,084 | ✅ | `routing/reference/routing_crosswalk.json` |
| Closure ladder 776,922 / 472,782 / 60.85% / 39.10% | ✅ | `outputs/findings/closure_finding_summary.csv` |
| Dedup 55,544 → 10,963 problems / 8,560 citizens | ✅ | PERFORMANCE.md §4 (production run, CPU box) |

The report was **ahead of** `DELIVERY.md`, `DEMO_SCRIPT.md` and `PERFORMANCE.md` on the PII
figure: those three still carried 49.6%, which was measured six hours before the ALL-CAPS
name recogniser and sixteen before the surname gazetteer. They were corrected on 10 August
to match this report, not the other way round.

### Known gaps this report does not close

- **No precision figure for PII.** The recogniser predicts 824 spans against 480 in the
  gold (730 NAME against 404). The gold cannot separate a name the labeller missed from an
  over-redaction. The report's recall numbers should never be quoted without this.
- **No by-language PII split** (issue #240). The gold carries no language field; every
  record scores as `unknown`. "English only" describes how the set was assembled, not
  something the scorecard verifies.
- **The PII gate does not pass** (issue #239). It thresholds against the DSI reference
  constant, which every other document calls not-a-target.
- **Crosswalk accuracy is in-sample.** 60.9 / 67.5 / 72.8% are resubstitution figures over
  the same history the crosswalk is fitted on — no holdout, no standard error. They are an
  upper bound on out-of-sample agreement, and the report's "learned where history sent it,
  not where it resolved best" caveat is necessary but not sufficient.
- **No per-stage latency.** `outputs/benchmark/latency.json` has never been produced.
  End-to-end warm text (4.44 s median, n=8) is the only measured timing.

## Regenerate

The generator script was written to `/tmp` in the session that produced this report and did
not survive. The `.docx` and `figures/` here are the artefact of record. Regenerating needs
the script rewritten; charts require `python-docx` and `matplotlib`.

## Sources

Primary: `docs/ROADMAP.md`, `docs/DELIVERY.md`, `docs/PERFORMANCE.md`, `docs/FINDINGS.md`,
`janasunani/evaluation/dsi_baselines.py` (frozen DSI reference, `reference_only=True`),
`janasunani/evaluation/pii_scorecard.py`, `janasunani/pipeline/*`, `janasunani/analytics/marts/*.sql` and `findings/*.py`.

External (read-only, not in repo): Box `Outputs/DSI Progress Report/dsi_progress_report.md` and `Outputs/CA&GR Analytics Note/grievance_analytics.md`.
