"""Apply evidence-status corrections to the August 2026 working-draft DOCX.

The source report was produced without a durable generator.  This narrow,
idempotent patcher keeps the existing layout and makes the evidence corrections
reproducible until the report is regenerated from a complete, versioned bundle
of quality, timing, and impact benchmark results. It does not certify that the
final publication gate in docs/value-add-report/README.md has passed.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile

from docx import Document


DEFAULT_REPORT = Path(
    "docs/value-add-report/Janasunani_2.0_Value_Add_Report_August_2026.docx"
)


PARAGRAPH_REPLACEMENTS = {
    "1   How grievances work today  — the status quo in numbers\t3":
    "1   How grievances work today  — the status quo in numbers\t2",
    "2   What we built  — the same workflow, now machine-readable\t4":
    "2   What we built  — the same workflow, now machine-readable\t3",
    "3   Faster and more reliable  — document processing before vs after\t5":
    "3   Faster and more reliable  — document processing before vs after\t4",
    "4   Keeping the queue clean  — spam and duplicates\t8":
    "4   Keeping the queue clean  — spam and duplicates\t6",
    "5   What are people complaining about  — hotspots, spikes, and workload\t10":
    "5   What are people complaining about  — hotspots, spikes, and workload\t7",
    "6   Who is complaining  — geography, gender, and channels\t13":
    "6   Who is complaining  — geography, gender, and channels\t9",
    "7   What officers see day-to-day  — real-time help while a case is open\t15":
    "7   What officers see day-to-day  — real-time help while a case is open\t10",
    "8   Safeguards and honesty  — what we don’t claim yet\t16":
    "8   Safeguards and honesty  — what we don’t claim yet\t11",
    "A   Annex — metric registry, sources, and how to reproduce\t17":
    "A   Annex — metric registry, sources, and how to reproduce\t12",
    "Janasunani receives about 1.4 million grievances a year. Three in four arrive with at least one attached document — together more than a million files. Today, an officer must open each filing, read the grievance and its attachments, decide what the complaint is about, and route it to the office that can act. That first routing step — understanding the complaint well enough to send it to the right place — takes a median of 1.7 days among forwarded cases, and a mean of 20.8 days because a long tail of cases takes much longer. Email filings take 17.7 days at the median; the Collector’s office, which handles one in seven routed complaints, takes 4 days at the median and 30 days at the mean.":
    "Between July 2021 and June 2025 Janasunani received 1,371,288 complaints; 688,301 arrived in 2024–25. Three in four filings carry at least one attachment, so officers must read the grievance and its documents before deciding what it is about and where it should go. Among forwarded cases, the administrative interval from receipt to first forward is 1.7 days at the median and 20.8 days at the mean; email is 17.7 days at the median, while the Collector’s office is 4 days at the median and 30 days at the mean.",
    "1. Faster first response — from reading to reviewing. The system reads the grievance and its scanned pages, removes personal information, labels the useful pages, summarises what matters, suggests a category, and proposes where to route it — in seconds for typed text, minutes for scans — so the officer starts from a first draft, not a blank page. Officers keep full authority: they accept, edit, or reject every suggestion.":
    "1. Faster first response — from reading to reviewing. The intended workflow extracts and redacts text, filters pages, and prepares advisory category, summary and route suggestions. Warm typed requests have completed in seconds in controlled laptop tests; the full scanned browser/model path and officer accept/edit/reject events are not yet verified. Redaction has measured misses, and officers retain full authority.",
    "3. Intelligence that earns a decision — hotspots with denominators, spikes explained, and workload counted as problems not filings. Geography is shown as complaints per 1,000 residents; time is shown against last year, not last month; every spike carries three numbers (filings / distinct problems / distinct citizens) so a campaign and a wave of unrelated problems — identical in a raw count — trigger different responses. Block-level diagnostics surface the blocks that a district average would hide.":
    "3. Intelligence that earns a decision — geographic counts with explicit limits, spikes explained, and workload counted as problems not filings. The first map release will show privacy-safe filing counts, inferred problems and distinct signatories; it will not publish per-capita or per-eligible rates until a current, documented denominator is governed. Time is compared with an appropriate historical baseline, and every spike carries three numbers (filings / inferred problems / distinct signatories) so a campaign and a diffuse wave — identical in a raw count — prompt different review.",
    "We rebuilt the grievance workflow as a sequence of governed, measurable steps. A raw filing — typed text or a scanned document — is extracted, redacted, triaged, classified, summarised, and routed, with a supervisor intelligence layer reading the same redacted corpus in parallel. Data stays on DPIC-controlled infrastructure; any external call (Sarvam) is declared, audited, and revocable — with a local fallback.":
    "We rebuilt the grievance workflow as a sequence of governed, measurable steps. The intended path extracts, redacts, triages, classifies, summarises and routes a filing, with a supervisor intelligence layer over governed redacted text. Most production inference is designed to remain on DPIC-controlled infrastructure. Sarvam is declared, audited and revocable with a local fallback; separately, a privacy-screened redacted sample was sent through hosted Codex for one-time development adjudication, not production.",
    "Figure 1 — The seven functional steps. Steps 1–6 are per-grievance (live in seconds/minutes); step 7 is corpus-level (nightly aggregates over the lake). Triage is advisory — it never changes submission status.":
    "Figure 1 — The seven functional steps. Steps 1–6 are implemented per-grievance stages, but the full scanned browser/model path is not yet verified; step 7 is corpus-level and some dedup-backed views still require integration. The triage contract is advisory and must never change submission status.",
    "The DSI Clinic built the first open-source pipeline on a 100,000-complaint sample (2025) — English-only, five stages, on an A100 GPU. We refolded it into a six-stage production pipeline (plus triage) that runs on DPIC infrastructure (CPU box + GPU box + laptop), replaced the unrecoverable PII model with a Presidio rebuild, added quality guards, and wired it to the live API. The comparison below is honest about what is like-for-like and what is not: baselines are historical reference, not thresholds.":
    "The DSI Clinic built the first open-source pipeline on a 100,000-complaint sample (2025) — English-only, five stages, on an A100 GPU. We implemented six local stages plus triage, replaced the unrecoverable PII model with a Presidio rebuild, and added quality guards and typed API contracts. This is not yet a verified production deployment: one-pass integration, live interface wiring and AWS activation remain outstanding. Baselines below are historical reference, not thresholds.",
    "Figure 2 — Time from filing to an officer-ready packet. Manual median 1.7 days is among forwarded complaints (n≈30k); live text median 4.4 s is n=8 warm submits on a laptop (PERFORMANCE.md). Log scale is required because the improvement is two orders of magnitude. First document after boot is slower (~9–10 s) while models warm; the summariser (~1.6 GB BART) is fetched once on first boot.":
    "Figure 2 — Two different clocks, shown side by side. The 1.7-day median is the observed administrative interval to first forward among ≈30,000 forwarded complaints; 4.4 seconds is technical pipeline latency from eight warm laptop submissions. This demonstrates processing speed, not days saved. Officer handling time and first-forward impact require exposure logging and a controlled rollout.",
    "Spam is not a block — it is a banner. A filing flagged as low-signal is still submitted; the officer sees “low-signal: <reason> (spam_score 0.82)” and decides. Prevalence is measured over redacted grievance text only (never raw grievance) and reported by district / category / mode / year — so a high-spam district is visible, not hidden in a state average. PPV / false-positive rate is measured against the two officer-confirmed spam-like families (details inadequate 39,964 + no specific grievance 16,375) on a deterministic 30% holdout by ticket hash; duplicate families are never counted as spam positives.":
    "Low-signal review is not a block. The evaluation taxonomy separates actionable, underspecified, irrelevant, outside-purview and policy-blocked cases; only advisory review or abstention is permitted and officers decide. Administrative templates provide 106,683 non-conflicting train-only weak labels, not adjudicated truth. A separate binary development benchmark is not compatible with the five-class serving slot and produced no deployable artifact. Office variation fails the pooling gate (maximum total-variation distance 0.522), so no PPV, false-positive rate or production threshold is approved until a stratified officer-adjudicated validation/test set exists.",
    "Duplicate-adjusted workload — the same three numbers, without a spike. The portal counts filings; the intelligence layer counts problems. For Sambalpur/2024: Filings 55,544 → Distinct problems 10,963 → Distinct citizens 8,560. That is the “true workload” for the slice. Both workload and spike share the same dedup_groups digest; the serving layer refuses a mixed snapshot (#137), so a stale index cannot silently undercount a surge.":
    "Duplicate-adjusted workload — the same three numbers, without a spike. For Sambalpur/2024: 55,544 filings → 10,963 inferred problems → 8,560 distinct signatories: 5.07 filings per inferred problem, or 80.3% fewer problem-units than filings. This is a reviewable operational view, not ground-truth workload. Workload and spike share the same dedup digest, and serving refuses a mixed snapshot (#137).",
    "Hotspot monitoring — per-1,000, not raw counts":
    "Geographic monitoring — governed counts first, rates later",
    "Headline complaint counts are not complaint rates — without an eligibility denominator (eligible households for PMAY, ration-card holders for food security, etc.), a district with 200 complaints looks the same whether it has 1,000 or 10,000 eligible households (2% vs 20%). The interim control is complaints per 1,000 general population (2021 projected district population, MoHFW 2020), excluding discards, tracked monthly.":
    "Headline filing counts are not complaint rates. Until a current, documented population or programme-eligibility denominator is governed, the first release will show privacy-safe filing counts, inferred-problem counts and distinct-signatory counts without ranking districts by incidence. Comparisons over time use each geography’s own historical baseline and always state the period and denominator.",
    "Month-to-month comparison is the operational use: a district whose per-1,000 rises faster than the state average is flagged — whether or not its absolute count is the highest. The roll-up goes district → block next (see below).":
    "The operational use is change detection: flag a district or block whose filing count rises unusually relative to its own history, then inspect whether the change reflects many signatories, repeat filing or inferred problem clusters. Cross-district rate comparisons remain disabled until a governed denominator exists; district-to-block drilldown follows only after the boundary crosswalk is deterministic.",
    "1. Eligibility denominators — link Janasunani to PMAY / pension / ration-card lists by district and block (per-eligible rates replace per-1,000). Highest priority.":
    "1. Eligibility denominators — later, link Janasunani to current, governed PMAY / pension / ration-card lists by district and block before publishing any per-eligible rate. The first release uses counts only. Highest priority.",
    "The system is human-in-the-loop. AI suggests; officers decide. Every automated suggestion is logged with who saw it, what model version was used, and whether the officer accepted, edited, or rejected it — that exposure log is what makes the future A/B evaluation possible. The live triage is advisory — it never auto-discards (even a “discard” recommendation in the old system is rendered as a flag, not a state change).":
    "The system is human-in-the-loop: AI suggests and officers decide; live triage never auto-discards. The model/release manifest can now pin exact versions locally, but the append-only exposure and later officer-decision events are still to be implemented. Until they exist, the report cannot claim acceptance, edit, override, time saved, or causal impact.",
    "Timing the officer feels. Cold start to /health: 19.4 s (models on disk) — the slow part is first boot when BART (~1.6 GB) is fetched from the hub (pre-warm the night before). Warm text grievance: median 4.44 s, mean 4.77 s (n=8, laptop). First request after boot ~9.5 s. Typed-text needs no tesseract/poppler at submit time beyond preflight checks; document upload renders via Poppler (pdftoppm/pdfinfo) then Tesseract (Oriya needs ori traineddata) before the same downstream steps. Every response includes extraction source (text vs ocr_model), spans (start/end over original text), and advisory fields — all typed and validated against the frozen frontend contract (janasunani/serving/schemas.py).":
    "Technical timing. Cold start to /health was 19.4 s with models already on disk. Serving now requires locally materialized, pinned BART bytes and performs no model download at startup. A warm typed grievance took a 4.44 s median and 4.77 s mean (n=8, laptop); the first request after boot was about 9.5 s. Document uploads render via Poppler and then use local OCR before the same downstream stages. These are technical timings, not officer time saved. Responses retain extraction source, original-text spans, and advisory fields under the typed serving contract.",
    "Evaluation discipline: a harness that measures and prints (evaluation/) is separate from a gate that fails a run when the number is bad (pipeline/pii_eval.py). DSI baselines are labelled reference_only=True (dsi_baselines.py) and the report renderer never colours them as targets. The lake is not PII-free — and we do not pretend it is (ROADMAP §3.2).":
    "Evaluation discipline: a harness that measures and prints (evaluation/) is separate from a gate that fails a run when a frozen number is bad (pipeline/pii_eval.py). The impact ladder is model quality → officer behavior → workflow outcome → citizen outcome. Current data can support selected model and workflow descriptions; officer behavior needs exposure/decision logging, correct authority needs adjudication, and causal citizen benefit needs a locked pilot. The lake is not PII-free — and we do not pretend it is (ROADMAP §3.2).",
    "The intelligence layer is not only a monthly report — it is a banner the officer sees while deciding, and a supervisor panel that refreshes from the same governed marts that produce the monthly findings. Both are built for the reality that a count without a denominator, a spike without a cause, and a queue with duplicates are worse than no information.":
    "The intelligence layer currently provides governed findings and a reviewable dedup slice. The officer banner and live supervisor panel are intended delivery surfaces, but their real-data integration is not yet verified and some spike/workload marts still await the dedup-index join. A count without a denominator, a spike without a cause and a queue with duplicates remain the design problem.",
    "Figure 7 — Left: learned crosswalk accuracy rises when district is added (≈12 pp from category-only to full). Right: Route 4’s median 48 days vs Route 2’s 23 days (PMAY, 2024–25); the 9-day Step 3 (BDO → Collector return) is the piloted reform target. Upper bound, not an estimate — Route 4 may be more complex on unobserved dimensions.":
    "Figure 7 — Left: the 60.9/67.5/72.8% crosswalk bars are historical in-sample resubstitution, retained only as upper-bound context. A chronological developmental holdout gives 45.15% top-1 and 69.05% top-3 for live category+district features (n=208,267), or 54.96%/79.68% on informative categories. Right: Route 4’s 48-day vs Route 2’s 23-day medians are descriptive and may reflect unobserved complexity; a pilot must measure any time effect.",
    "Plus the two engineering slices that are built and waiting for an overnight run and a key: dedup-index join into spike/workload marts (#78) and the Sarvam live comparison (needs SARVAM_API_KEY — few-hundred rupees on the paired 300-page sample; governance is recorded, cost is not a blocker). The A/B stepped-wedge design (AB_PLAN.md) is locked before any outcome data is viewed — so the August framework does not become a post-hoc story.":
    "Next engineering work is evidence-preserving rather than new provider spend: checkpoint each Sarvam page so an interruption cannot lose paid results, import the cached 56-page aggregate into the benchmark registry, and wire the dedup-index join into spike/workload marts (#78). The stepped-wedge A/B plan remains DRAFT; its unit map, estimands, extract hash, MDE and pause rules must be locked before any arm outcome is read.",
}

# The report was patched once before the five-class serving incompatibility was
# documented. Keep that intermediate paragraph as an accepted source so the
# generator remains idempotent across both tracked states.
PARAGRAPH_ALTERNATES = {
    "Spam is not a block — it is a banner. A filing flagged as low-signal is still submitted; the officer sees “low-signal: <reason> (spam_score 0.82)” and decides. Prevalence is measured over redacted grievance text only (never raw grievance) and reported by district / category / mode / year — so a high-spam district is visible, not hidden in a state average. PPV / false-positive rate is measured against the two officer-confirmed spam-like families (details inadequate 39,964 + no specific grievance 16,375) on a deterministic 30% holdout by ticket hash; duplicate families are never counted as spam positives.": (
        "Low-signal review is not a block. The five classes are actionable, underspecified, irrelevant, outside purview, and policy-blocked; only an advisory review flag is permitted and officers decide. Administrative templates provide 106,683 non-conflicting train-only weak labels, not adjudicated truth. Office variation fails the pooling gate (maximum total-variation distance 0.522), so no PPV, false-positive rate, or production threshold is claimed until a stratified officer-adjudicated validation/test set exists.",
    ),
}


CELL_REPLACEMENTS = {
    (2, 0, 0, 1): "Median technical latency for a warm typed grievance (n=8). The 1.7-day first-forward interval is a different administrative clock; workflow time saved is not yet measured.",
    (3, 0, 0, 0): "⚠  What we are not claiming. Technical latency is not officer time saved. Sarvam evidence is divergence/coverage, not OCR accuracy. Administrative discard templates are weak labels, not spam gold; live summary quality is unmeasured. Routing’s older 60.9/67.5/72.8% is in-sample, while the new chronological 2025 result is developmental because that test was viewed. Causal officer or citizen impact requires a locked pilot.",
    (9, 5, 2, 0): "BART incumbent (facebook/bart-large-cnn family, ~1.6 GB). It receives grievance text plus redacted letter/text pages. Historical usefulness scores guided page filtering; there is no current factuality/usefulness scorecard. Serving now requires a pinned local release/DVC artifact; the public model ID is available only through an explicit development opt-in.",
    (9, 5, 3, 0): "The page gate avoids known low-value ID/bill summaries. It does not establish that the remaining summary is accurate or officer-useful; critical-fact recall, unsupported facts, PII leakage, edit burden, and correct abstention still need adjudication.",
    (9, 6, 2, 0): "MuRIL incumbent via DVC mirror. Feature = grievance text + redacted page text; current serving is English-gated. The 71.04% figure is a historical typed-subject benchmark, not a new scanned/redacted production evaluation. The new group-disjoint harness is ready, but governed gold is not yet frozen.",
    (9, 6, 3, 0): "Category suggestions can rescue uninformative intake fields, but no automatic assignment is justified. Report accepted rescue, top-k, per-class, calibration, abstention and language/source slices on the same frozen grievances before promotion.",
    (9, 7, 3, 0): "The measured 4.44 seconds is technical latency, not officer handling time or time to first action. Those outcomes need exposure/decision logging and a controlled rollout.",
    (9, 2, 3, 0): "About 77.9% of historical English pages passed three plausibility heuristics. Without hand transcription, that is not evidence that four in five pages were read correctly. Pages that fail the repetition guard are quarantined for review; OCR accuracy remains unmeasured.",
    (9, 4, 3, 0): "Historical usefulness scores motivated skipping IDs and bills before summary generation. They do not establish attachment prevalence, current summary factuality or that the retained subset is officer-useful; those claims need a governed paired review.",
    (13, 2, 2, 0): "55,544 / 10,963 = 5.07 filings per inferred problem, or 80.3% fewer problem-units than filings. These are reviewable inferred groups, not adjudicated ground truth.",
    (15, 0, 2, 0): "Why counts need context",
    (15, 1, 0, 0): "Filing counts by district and block — descriptive workload only; not incidence or need.",
    (15, 1, 1, 0): "Counts can locate workload and unusual changes within a geography, but they do not support fair rate comparisons across differently sized populations.",
    (15, 1, 2, 0): "The first map release will not rank districts by per-capita or per-eligible rates until a current, documented denominator is governed.",
    (15, 2, 0, 0): "Distinct signatories and inferred problems — reported beside filing counts.",
    (15, 3, 2, 0): "A filing count alone cannot tell you whether to investigate broad service-delivery demand or a small set of repeat filers.",
    (15, 4, 0, 0): "Rural Housing + PMAY filing volume — descriptive counts, with period and suppression status.",
    (18, 0, 0, 0): "⚠  Eligibility denominators remain the highest-priority external data to acquire. A block with 200 housing complaints among 1,000 eligible households is a different fact from 200 among 10,000. Until current beneficiary lists are linked, documented and governed, the UI and report must not publish per-capita or per-eligible rates.",
    (19, 5, 1, 0): "No hard rural/urban flag exists in the structured record. District and block fields can support privacy-safe raw aggregates after deterministic boundary reconciliation, but they do not identify rurality or incidence. Distinct-signatory and filings-per-signatory measures can describe repeat concentration without inventing a population denominator.",
    (20, 0, 0, 0): "⚠  On rural/urban and disadvantaged groups — a candour note for reviewers. The complaint record does not contain a rural/urban flag or a caste/category field. The first map release may show privacy-safe district and block counts after deterministic boundary reconciliation, but it will not show per-capita rates, rural-versus-urban shares or SC/ST shares. Those claims require current, documented census, SECC or beneficiary denominators and approved linkage. We mark them as ‘needs linkage’ rather than filling the gap with a guess.",
    (21, 1, 1, 0): "Advisory low-signal reason, never a rejection. The screenshot case now skips category/summary. On the canonical 57-case frontier-adjudicated binary development test, the local review candidate caught all 13 complaints needing extra review and sent 3 of 44 ordinary complaints to review; it is not five-class serving-compatible or release-eligible.",
    (21, 1, 2, 0): "Administrative weak labels do not establish quality. The development result has no outside-purview support, wide intervals and a viewed test; an officer-adjudicated, stratified future set is still required. Officer always decides whether to proceed or seek clarification.",
    (21, 3, 1, 0): "MuRIL category candidate with confidence. The cited 71.04% and per-class spread are historical typed-subject reference; a governed production-domain scorecard is pending.",
    (21, 3, 2, 0): "Does not auto-assign. Promotion requires group-disjoint gold, per-class/top-k/calibration, abstention and language/source slices.",
    (21, 4, 1, 0): "BART draft only for grievance-bearing letter/text pages. Historical page usefulness motivated filtering; current summary factuality and officer usefulness are unmeasured.",
    (21, 4, 2, 0): "A low-signal submission is skipped rather than summarized. Critical-fact recall, unsupported facts, PII leakage, edit burden and correct abstention need paired officer adjudication.",
    (21, 5, 2, 0): "Learned means historical destination agreement, not correct authority or best outcome. Chronological developmental top-1 is 45.15% overall and 54.96% on informative categories; the older 60.9/67.5/72.8% figures are in-sample.",
    (23, 2, 1, 0): "Citizen text remained on DPIC-controlled infrastructure, but the old runtime could still fetch public model bytes at startup. That made availability and exact rollback depend on mutable external state.",
    (23, 2, 2, 0): "Trust tiers remain explicit. The serving contract resolves pinned local artifacts from an activated release manifest or DVC mirror and makes no registry/public-model call at startup; no reviewed production manifest is active yet. Sarvam traffic remains isolated to egress/ and kill-switch controlled. A shape-screened redacted sample was also sent to hosted Codex for one-time development adjudication; exact hidden prompts, sampling settings and provider-retention evidence were unavailable, so this is recorded as a limitation rather than production precedent.",
    (24, 3, 3, 0): "Adjudicate the same grievance set by language and typed/scan source, freeze dataset and split fingerprints, then compare incumbent and candidate versions through the governed evaluation logger.",
    (24, 4, 1, 0): "Officer-confirmed duplicate actions are a historical baseline. The Sambalpur/2024 index groups 55,544 filings into 10,963 inferred problems, but those inferred groups are not adjudicated recall or precision evidence.",
    (24, 4, 2, 0): "No duplicate recall, candidate PPV, false-merge rate, or automation increment is claimed until the candidate pairs and clusters are adjudicated on a held-out sample.",
    (24, 4, 3, 0): "Freeze an officer-held-out sample and publish recall, reviewable-candidate PPV, extra matches beyond the officer baseline, campaign preservation and singleton false merges with ticket/cluster bootstrap intervals.",
    (24, 1, 1, 0): "Cached paired coverage/divergence only: a completed 5-page run and 56 paired successes from an interrupted 300-page run. Sarvam produced 1.3345× normalized characters on the 56-page aggregate; every normalized pair differed.",
    (24, 1, 2, 0): "No OCR accuracy, handwriting or observed-language conclusion: there is no hand transcription. Nine attempted pages were excluded and credit exhaustion interrupted the larger run, so failures must be reported separately.",
    (24, 3, 1, 0): "Historical MuRIL typed-subject reference: 71.04%. New production-domain categorization quality is not yet reportable. Routing chronological developmental holdout: 45.15% top-1 / 69.05% top-3 overall (n=208,267).",
    (24, 5, 1, 0): "Historical destination agreement only. The live category+district developmental holdout is 45.15% top-1 (95% CI 44.94–45.36) and 69.05% top-3; process-time contrasts remain descriptive.",
    (24, 5, 2, 0): "We do not report correct-authority rate or faster resolution caused by routing. Correctness needs jurisdiction adjudication; time/transfer benefit needs exposure logging and a locked rollout.",
    (24, 5, 3, 0): "Lock the draft stepped-wedge plan before outcomes: immutable intake-office transfer-network clusters, ITT, censoring-aware 30/90-day endpoints, exposure/decision events, a fixed-horizon citizen-satisfaction invitation rule with response/missingness reporting, spillover sensitivity and pause rules.",
    (25, 10, 0, 0): "Routing — chronological developmental",
    (25, 10, 1, 0): "Category+district: 45.15% top-1 (44.94–45.36), 69.05% top-3 / n=208,267; informative: 54.96% / 79.68% / n=142,181",
    (25, 10, 2, 0): "janasunani/evaluation/historical.py; docs/QUALITY_BENCHMARKS.md",
    (25, 10, 3, 0): "janasunani-evaluate-routing (freeze a future slice before release)",
    (25, 12, 0, 0): "Actionability — development + weak-label audit",
    (25, 12, 1, 0): "Canonical frontier-adjudicated test n=57: 94.74% accuracy; 13/13 review recall; 13/16 review precision; 3/44 actionable sent to review. Weak labels n=106,683; office max TV 0.522 (pooling gate fails).",
    (25, 12, 2, 0): "docs/evidence/actionability_frontier_benchmark_reproducible.json; janasunani/evaluation/actionability.py; weak_labels.py",
    (25, 12, 3, 0): "Binary development only; not five-class serving-compatible; no outside-purview support; freeze officer-reviewed future test before promotion",
    (25, 21, 1, 0): "DRAFT: stepped-wedge ITT framework; unit/event semantics, MDEs, extract hash and pause rules still must be locked",
    (25, 22, 1, 0): "Cached: 5 completed pages + 56 paired successes from interrupted run; 7/127 accepted jobs failed; divergence/coverage only, no accuracy",
    (25, 22, 2, 0): "docs/evidence/sarvam_cached_benchmark.json; sarvam_scorecard.py",
    (25, 22, 3, 0): "No paid rerun; add transcription/adjudication before a quality claim",
}


def _set_paragraph(paragraph, text: str) -> None:
    if paragraph.runs:
        paragraph.runs[0].text = text
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.add_run(text)


def patch_report(source: Path, destination: Path) -> None:
    document = Document(source)
    current = {paragraph.text: paragraph for paragraph in document.paragraphs}
    missing = sorted(
        old
        for old, new in PARAGRAPH_REPLACEMENTS.items()
        if old not in current
        and new not in current
        and not any(alternate in current for alternate in PARAGRAPH_ALTERNATES.get(old, ()))
    )
    if missing:
        raise RuntimeError(f"report paragraphs changed; missing {len(missing)} expected texts")
    for old, new in PARAGRAPH_REPLACEMENTS.items():
        if old in current:
            _set_paragraph(current[old], new)
            continue
        for alternate in PARAGRAPH_ALTERNATES.get(old, ()):
            if alternate in current:
                _set_paragraph(current[alternate], new)
                break

    for (table_index, row_index, cell_index, paragraph_index), text in CELL_REPLACEMENTS.items():
        cell = document.tables[table_index].cell(row_index, cell_index)
        if paragraph_index >= len(cell.paragraphs):
            raise RuntimeError(
                f"missing paragraph {paragraph_index} in table {table_index} "
                f"row {row_index} cell {cell_index}"
            )
        _set_paragraph(cell.paragraphs[paragraph_index], text)

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{destination.name}.", suffix=".docx", dir=destination.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        document.save(temporary)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    patch_report(args.input, args.output or args.input)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
