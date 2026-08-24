# Value-add report — Janasunani 2.0

This directory contains three versions of the same evidence story. Choose the
document for the reader; do not use the short briefs as substitutes for the
definitions and caveats in the long report.

> **Status: working drafts populated from development bundle
> `72076ba23e5ba0ec0db0dc6718378a0150abc3c97c5bf9cbf73429b6b95dbee5`;
> publication_ready=false.** The bundle now contains reproducible development
> timing and available accuracy/safety runs, but 0/1 required release-speed,
> 0/5 required release-accuracy and 0/2 required impact artifacts. The files
> report those numbers and blockers; they are not a final release or impact
> account.

## Final publication gate

The final report is complete only when a clean, approved release has one
machine-readable benchmark bundle containing all of the following. Missing
values must fail the publication check rather than silently become narrative
proxies.

- **Speed:** cold and warm end-to-end timing plus every supported stage and
  input path; release and hardware IDs; attempts, successes and failures;
  throughput, p50, p90 and p95; and clustered uncertainty where repeated pages
  belong to one grievance. The timing artifact defines cold as the first
  successful request after processor construction and warm as subsequent
  requests in that process; processor construction time is reported separately.
- **Accuracy and safety:** every supported task on a frozen, independently
  reviewed test set; numerator, denominator, coverage/abstention, confidence
  intervals, per-class and supported-language results, subgroup support,
  failure counts and immutable data/model hashes. Historical routing agreement
  remains separate from correct-authority adjudication.
- **Operational and citizen impact:** assigned arm, actual model exposure,
  officer accept/edit/override, transfers, first meaningful action, 30/90-day
  resolution or restricted mean unresolved time, repeat contact, satisfaction
  invitations and responses, missingness/censoring, effect estimates and
  uncertainty under the locked pilot design.

The speed harness and several offline scorecards exist, but this gate is not
yet satisfied. Summary now has a bounded single-frontier-judge development
baseline, but release summary quality and OCR accuracy still need human reference judgments;
correct-authority routing needs officer adjudication; and causal impact needs
the exposure/event instrumentation and follow-up specified in
[`../IMPACT_METRICS.md`](../IMPACT_METRICS.md). Until those inputs exist, the
documents must continue to say **working draft** and must not claim measured
time saved, faster resolution or improved citizen satisfaction.

## Choose the right report

| Artifact | Primary audience | Use it for |
|---|---|---|
| `Janasunani_2.0_Value_Add_Report_August_2026.docx` | Technical reviewers, programme teams and review committees | The full evidence record: definitions, denominators, baselines, model limitations, safeguards, impact framework and reproduction notes. Its executive summary can stand alone, but the annex is the source for qualified claims. |
| `Janasunani_2.0_IAS_Officer_Brief_August_2026.docx` | A Secretary, Collector or senior IAS officer with little technical time | A short decision brief: what officers gain, which numbers are already measured, what is not yet proven and which governed next step would turn technical capability into measurable service value. |
| `Janasunani_2.0_Public_Systems_Capability_Brief_August_2026.docx` | Prospective government and public-system partners | A portable account of the approach beyond Janasunani: local-first case understanding, actionability, routing, duplicate/campaign analysis, safe serving and a measured pilot path. Janasunani figures are proof points, not promised performance on a new system. |

The `figures/` directory contains charts embedded in the long report. They can
also be used as standalone presentation material, provided their captions and
evidence qualifications travel with them.

## Verification status — 2026-08-11

The report's headline figures were re-checked against the code on 10 August
2026. The following values reproduced exactly unless qualified in the table:

| Claim in the report | Re-measured | How |
|---|---|---|
| PII overlap recall 77.9%, exact 55.0%, coverage 78.3% | ✅ 0.7792 / 0.5500 / 0.7833 | `python -m janasunani.evaluation.pii_scorecard --gold …` |
| PII per entity: PHONE 82.8 / AADHAAR 85.7 / EMAIL 75.0 / NAME 77.7 | ✅ exact match | same run |
| Officer-confirmed duplicates 37,299 (21,117 + 16,182) | ✅ | `outputs/findings/confirmed_duplicates.csv`, `discard_reason_families.csv` |
| Crosswalk argmax 60.9 / 67.5 / 72.8% | ⚠️ reproduced, but in-sample only | Historical resubstitution; not a quality gate |
| Crosswalk table sizes 34 / 257 / 971 / 5,084 | ✅ | `routing/reference/routing_crosswalk.json` |
| Closure ladder 776,922 / 472,782 / 60.85% / 39.10% | ✅ | `outputs/findings/closure_finding_summary.csv` |
| Dedup 55,544 → 10,963 problems / 8,560 citizens | ✅ | PERFORMANCE.md §4 (production run, CPU box) |
| Canonical actionability test: 94.74% accuracy; 13/13 review recall; 3/44 actionable sent to review | ✅ | `dvc repro --single-item actionability-local-candidate-benchmark` |
| Chronological category agreement, viewed 2024 test n=3,160: top-1 46.55%; top-3 90.89%; macro-F1 36.49% | ✅ developmental only | `dvc repro --single-item categorization-historical-benchmark` |
| Local BART summary baseline, enriched n=30: 65.48% critical-fact recall; 8/26 usable without edit; 4/26 residual-PII cases | ✅ single-judge development only | `dvc repro --single-item summary-development-benchmark` |
| CPU development timing: 90/90 attempts, 0 failures; warm text mean 0.109 s (n=40), PDF mean 13.244 s (n=20); overall p50/p90/p95 0.139/14.348/14.883 s | ✅ | `dvc pull outputs/benchmark/latency.json.dvc`; bundle ID above |
| Full benchmark publication gate | ❌ speed 0/1, accuracy 0/5, impact 0/2 required artifacts | `dvc repro --single-item full-benchmark-bundle` |

The report was **ahead of** `DELIVERY.md`, `DEMO_SCRIPT.md` and `PERFORMANCE.md` on the PII
figure: those three still carried 49.6%, which was measured six hours before the ALL-CAPS
name recogniser and sixteen before the surname gazetteer. They were corrected on 10 August
to match this report, not the other way round.

### Known gaps these reports do not close

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
- **Development timing is not a release-host benchmark.** The tracked run used
  30 deterministic synthetic grievances on an Apple arm64 laptop, three passes
  each with the first discarded. It completed 90/90 attempts with no failures
  and retained 60 warm observations. Text mean/p50/p90 was
  0.109/0.133/0.145 s (n=40); PDF mean/p50/p90 was
  13.244/13.661/15.079 s (n=20). Overall mean was 4.487 s (clustered SE 1.158),
  p50 0.139, p90 14.348 and p95 14.883 s. This used DVC model bytes plus an
  identified local BART cache snapshot, not an activated approved release; the
  format classifier also emitted a scikit-learn 1.8-to-1.9 compatibility
  warning. OCR includes page/format work internally, so those subcomponents do
  not have separately instrumented live timings.
- **Actionability is not yet a production five-class score.** Administrative
  dispositions are weak training labels, not independent truth. Even a
  privacy-screened, frontier-model-adjudicated development set does not replace
  officer-adjudicated validation, establish that every class has enough support
  or justify a permanent external production call. The two judges and resolver
  were separate Codex agent contexts, not independent model families. The exact
  serving model/version, hidden prompts, sampling settings and provider-retention
  evidence were unavailable; the tracked aggregate records that limitation.
  The checksummed binary artifact is now compatible with the advisory serving
  objective, but it cannot assign five-class reasons and is not release-eligible.
- **Categorization measures historical labels, not policy correctness.** The
  2024 redacted-text benchmark keeps exact normalized-text groups in one
  chronological split and reports top-1 46.55%, top-3 90.89% and macro-F1
  36.49% on the later n=3,160 test. That test was viewed during development,
  language is unadjudicated, and no serving artifact was promoted. A newly
  frozen, officer-confirmed release set is still required.
- **Sarvam evidence is coverage and divergence, not OCR accuracy.** Cached paid
  runs show completed pages and text differences, but there is no hand
  transcription against which to score either engine. Credit exhaustion and
  failed or excluded pages remain part of the denominator. The aggregate has no
  reportable latency distribution or actual billing record, and its source
  source snapshots are now privately DVC-tracked and hashed. The original sample
  manifest and derivation command were not recovered, so the larger aggregate
  still cannot be independently rebuilt from those snapshots alone.
- **Summary has a small development baseline, not officer validation.** On an
  enriched 30-case redacted typed-text set, local BART retained 55/84 critical
  facts, had no unsupported or contradictory cases in 26 generated outputs,
  produced 8/26 drafts usable without edit, and repeated residual identifying
  detail in 4/26. It summarized all six cases the single frontier judge marked
  for skipping and missed all four coherent Odia cases. The test was viewed,
  edit seconds were judge estimates, and scan/language coverage is incomplete;
  the required paired officer release scorecard remains missing.

### New quality evidence on the pipeline-quality trunk

The governed register is [`../QUALITY_BENCHMARKS.md`](../QUALITY_BENCHMARKS.md).
It adds a chronological routing benchmark over structured weighted cells
(full-corpus campaign-group isolation is unavailable); a five-class
actionability taxonomy and weak-label confounding audit; the exact
`error_in_summary_and_spam.png` regression; cached Sarvam coverage/divergence;
and a bounded single-judge local-BART summary baseline. The category harness now has a 2024
chronological, exact-text-group-disjoint historical-label benchmark: its viewed
development test (n=3,160) reached 46.55% top-1, 90.89% top-3 and 36.49%
macro-F1. This supports testing a ranked shortlist, not automatic assignment;
it is neither policy correctness nor a release result. A 180-case adjudication
sample produced 174 canonical development cases after excluding six resolver
judgments marked uncertain. In the resulting 57-case test, the
validation-selected TF-IDF candidate caught all 13 review cases while flagging
3 of 44 actionable cases. A frozen local MuRIL probe did not beat it. The
original 180-row TF-IDF/MuRIL/MiniLM benchmark is retained as historical
evidence but is not canonical. The set has no `out_of_scope` support,
wide intervals and a viewed test, so it is not a five-class score or release
gate. Its checksummed binary artifact is serving-compatible only for advisory
`actionable_vs_officer_review`; it does not produce five-class reasons. The
2025 routing cohort was likewise viewed during harness development and requires
a newly frozen future slice. No administrative weak label is reported as
adjudicated classifier accuracy.

Officer and citizen outcomes are defined separately in
[`../IMPACT_METRICS.md`](../IMPACT_METRICS.md). The Word report must distinguish
technical latency, offline model quality, officer behavior, workflow outcomes
and citizen outcomes; only a locked pilot can support causal value-add.
The current bundle has no exposure/decision or satisfaction artifact, so impact
is reported as **not measured**, not as a zero effect.

## Regenerate and verify

[`../../scripts/update_value_add_report.py`](../../scripts/update_value_add_report.py)
applies the pipeline-quality corrections idempotently to the tracked Word
report. It intentionally preserves the established layout and embedded charts;
it is not a from-scratch chart generator. The two short briefs are generated
from Python sources so that their text and layout can be reproduced.

Generate all three tracked documents from the repository root. DVC treats the
long report's `.source.docx` as an immutable input and the three published DOCX
files as outputs, so regeneration cannot silently patch its own previous output:

```bash
dvc pull dvc.yaml:full-benchmark-bundle
dvc repro value-add-report-documents
```

All three generators fail closed if the bundle lacks the tracked real timing,
selected actionability test, weak-label audit, PII scorecard, or both routing
scorecards. They also refuse fake timing. Accuracy and speed claims therefore
come from one bundle; impact remains an explicit missing section until its two
required artifacts exist. Required release artifacts must match their declared
schema, and every configured evidence field must satisfy its declared JSON type
and cardinality; a self-declared `publication_ready=true` stub cannot open the
publication gate. Cross-field invariants also fail closed: confidence intervals
must contain their estimates, and citizen-response counts and rates must
reconcile. A configured artifact participates only when its
`tracked_input` flag is true and its path is a dependency of the
`full-benchmark-bundle` DVC stage; future release files remain explicit blockers
even if an ambient file appears at the configured path. Each generator also
canonicalizes DOCX archive metadata, so identical inputs produce byte-identical
DVC outputs.

Render each output with the `documents` skill's `render_docx.py`. Set
`DOCX_RENDERER` to the absolute path of that installed script, then run:

```bash
DOCX_RENDERER="/absolute/path/to/documents-skill/render_docx.py"
mkdir -p /tmp/janasunani-value-add-render/long
mkdir -p /tmp/janasunani-value-add-render/ias
mkdir -p /tmp/janasunani-value-add-render/capability
.venv/bin/python "$DOCX_RENDERER" \
  docs/value-add-report/Janasunani_2.0_Value_Add_Report_August_2026.docx \
  --output_dir /tmp/janasunani-value-add-render/long --emit_pdf
.venv/bin/python "$DOCX_RENDERER" \
  docs/value-add-report/Janasunani_2.0_IAS_Officer_Brief_August_2026.docx \
  --output_dir /tmp/janasunani-value-add-render/ias --emit_pdf
.venv/bin/python "$DOCX_RENDERER" \
  docs/value-add-report/Janasunani_2.0_Public_Systems_Capability_Brief_August_2026.docx \
  --output_dir /tmp/janasunani-value-add-render/capability --emit_pdf
```

Open every rendered page and check clipping, table breaks, repeated headers,
font substitution and blank pages. A successful conversion alone is not visual
verification. Before publication, also reconcile every headline with
[`../QUALITY_BENCHMARKS.md`](../QUALITY_BENCHMARKS.md) and every officer or
citizen outcome with [`../IMPACT_METRICS.md`](../IMPACT_METRICS.md).

## Sources

Primary: `docs/ROADMAP.md`, `docs/DELIVERY.md`, `docs/PERFORMANCE.md`, `docs/FINDINGS.md`,
`janasunani/evaluation/dsi_baselines.py` (frozen DSI reference, `reference_only=True`),
`janasunani/evaluation/pii_scorecard.py`, `janasunani/pipeline/*`, `janasunani/analytics/marts/*.sql` and `findings/*.py`.

External (read-only, not in repo): Box `Outputs/DSI Progress Report/dsi_progress_report.md` and `Outputs/CA&GR Analytics Note/grievance_analytics.md`.
