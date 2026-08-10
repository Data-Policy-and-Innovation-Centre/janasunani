# Performance record

Every number here was measured, not quoted. Baseline pass 2026-08-07 against
`main` at `ca58f31`, after the Sprint 3 merges and the fixes they surfaced.
The PII scorecard (§2) and the duplicate baseline (§5) were re-measured
**2026-08-10** and supersede the 7 August values; each says so in place.

Where a number is a legacy reference rather than a fresh measurement it says
so. Where a capability cannot yet be measured, that is stated instead of
being filled with a placeholder.

**Bench**: Apple Silicon laptop, CPU only (no CUDA). The CPU box is 2 vCPU /
7 GB and is slower; dedup timings below are from the box, serving timings
from the laptop.

## 1. Live demo path

`janasunani-api-live` with `processor: pipeline` (real models, not the mock
app), frontend on `npm run start`.

| | Measured |
|---|---|
| API cold start to `/health` ok (models on disk) | **19.4 s** |
| `POST /grievance`, warm, text submission | **median 4.44 s**, mean 4.77 s, min 4.26 s, max 6.20 s (n=8) |
| `POST /grievance`, first request after boot | 9.5 s |
| `GET /history?limit=20` over 1,371,288 rows | **median 0.13 s** (n=3) |
| `GET /supervisor` | closure panel `recorded` |
| Frontend routes `/`, `/history`, `/supervisor` | HTTP 200 |
| Frontend production build | exit 0, 4 routes |

First boot on a machine that has never run the demo is much slower: the
summarizer pulls `facebook/bart-large-cnn` (~1.6 GB) from the Hugging Face
hub. Pre-warm it before a demo. See [DEMO.md](DEMO.md) §6.

### What one submission produces

A synthetic grievance with name, mobile, email and Aadhaar returns
extraction, redaction with spans, category, summary, routing and the triage
advisory. Routing came back `method: "fallback"`, `confidence: 0.25` for a
Sambalpur water complaint: 62 categories are mapped but only 8 have a
derivable department, so the router degrades to the general grievance cell
rather than failing. That is expected and the UI labels it.

## 2. PII redaction, measured on the live path

This is the release-critical direction. §5.1 of the ROADMAP is explicit that
the false-negative rate is what matters, because F1 hides leaked PII.

| Entity | Result |
|---|---|
| PHONE | detected |
| EMAIL | detected |
| AADHAAR | detected — spaced, unspaced, and with no context word |
| PAN | detected |
| NAME (Odia/Indian names) | detected — was leaking 48%, closed in #184 |

Aadhaar is matched on shape alone (`[2-9]` + 11 digits), so context words
only boost confidence. Numbers beginning 0 or 1 are correctly not matched:
no real Aadhaar starts with those.

### The name gap, found and closed

40 probes, 10 Odia/Indian full names across 4 sentence framings, scored on
whether every token of the name was covered. Measured before and after the
recognizer added in #184:

| | Before | After |
|---|---|---|
| Fully redacted | 52% | **100%** |
| Partially redacted (given name left exposed) | 5% | 0% |
| Missed entirely | 42% | 0% |
| **Any leak** | **48%** | **0%** |

Before the fix, framing moved the result sharply, and the misses concentrated
where a label replaces a grammatical subject:

| Framing | Full | Partial | Missed |
|---|---|---|---|
| `{name} reports that …` | 6 | 2 | 2 |
| `My name is {name} and …` | 6 | 0 | 4 |
| `Applicant: {name}. …` | 3 | 0 | 7 |
| `This grievance is filed by {name} of Sambalpur district.` | 6 | 0 | 4 |

A Western control name (`John Smith`) was fully redacted where the Odia name
in the same sentence was not: `en_core_web_sm` NER is trained on English news
and is weak on Indian person names.

NAME now has a pattern recognizer behind it as well as the model — a surname
gazetteer plus name-introducing phrases, both yielding the whole name rather
than the surname alone. After the fix, 50 of 50 probes across five framings
are fully redacted, and nine realistic non-name sentences (places, offices,
scheme names, designations) produce no false positives.

Precision was the real risk and is guarded: `the road from Sambalpur to
Bargarh` and `Name of the scheme is Pradhan Mantri Awas Yojana` are both left
intact, because over-redaction removes what the officer needs to act on.

Legacy reference for comparison: the DSI report records **80.56% any-overlap,
50.00% exact** — English, typed text, and not a threshold. The probes above
are synthetic and are not a substitute for the gold set. What they establish
is a floor on a known failure mode, not a coverage claim.

### The scorecard, now wired

`janasunani.evaluation.pii_scorecard` no longer passes empty predictions by
construction. `score_per_language` calls `score_examples`, which wires the
live Presidio recognizers (`detect_pii_spans`) by default; it falls back to
empty predictions only if the analyzer cannot be imported, e.g. an
environment without the `pii` extra (#67, closed). Its slicing, language
split and thin-slice guard run against real predictions.

Re-measured **2026-08-10** on `main`, after the two name recognisers landed
on 7 August. The previous 49.6% figure was taken at 04:37 on 7 August, six
hours before the ALL-CAPS recogniser (`a62d3a3`) and sixteen before the
surname gazetteer (`968880a`), so it never saw either fix. It is superseded.

| | Overlap recall | Exact recall | Gold spans | Predicted |
|---|---|---|---|---|
| PHONE | 0.8276 | 0.8276 | 29 | 28 |
| AADHAAR | 0.8571 | 0.8571 | 7 | 14 |
| EMAIL | 0.7500 | 0.7250 | 40 | 30 |
| NAME | 0.7772 | 0.5074 | 404 | 730 |
| BANK_ACCOUNT | n/a | n/a | 0 | 20 |
| SCHEME_ID | n/a | n/a | 0 | 2 |
| **OVERALL** | **0.7792** | **0.5500** | **480** | **824** |
| COVERAGE | 0.7833 | 0.5521 | 480 | 824 |

`excluded_by_policy=49` (government email addresses, #56).

Three qualifications that belong beside the headline:

- **Precision is unmeasured and the recogniser over-fires.** 824 predicted
  spans against 480 gold, and 730 NAME spans against 404. The gold cannot
  distinguish a name the labeller missed from an over-redaction, so no
  precision number is reported. Over-redaction is not free — it removes the
  context the officer acts on.
- **Overlap and exact diverge sharply on names** (0.78 vs 0.51). A name is
  usually touched and often not fully covered. Quoting the overlap figure
  alone overstates coverage.
- **There is no by-language breakdown.** Every gold record scores into a
  single `unknown` bucket because the gold carries no language field. The
  "English only" statement is a claim about how the set was assembled, not
  something this scorecard verifies. DELIVERY.md Table 1 commits to a table
  "by data type and by language"; only the first half is deliverable.

**The gate fails.** `janasunani-evaluate-pii` exits 1: coverage 0.7833 is
below `LEGACY_OVERLAP_BASELINE = 0.8056` (`janasunani/pipeline/pii_eval.py:31`).
That constant is the DSI reference figure, which `dsi_baselines.py` marks
`reference_only=True` and every document here calls explicitly not a target —
yet the gate uses it as a hard threshold. Gate and reference should not be the
same constant. Filed rather than relaxed on the eve of a demo.

## 3. Models

Legacy figures from the DSI technical report. Not re-measured in this sprint.

| Model | Metric | Value |
|---|---|---|
| Categorizer (MuRIL) | accuracy, 65,999 typed subject lines | 0.7104 |
| Page-type classifier (ViT) | accuracy | 0.67 |

Do not compare the MuRIL figure against any scanned-page result. It is
computed on typed subject lines; against scans it measures the difference
between typed text and scans as much as between models. See #127.

## 4. Dedup index, production

Sambalpur 2024, the only district-year with redactions. Run on the CPU box
against production Postgres.

| | |
|---|---|
| Filings indexed | 55,544 |
| Distinct problems (clusters) | **10,963** |
| Distinct signatories | **8,560** |
| Groups with more than one member | 1,794 |
| Largest group | 26,203 |
| Comparison pairs | 16,138,623 |
| Large buckets | 310 |
| Wall clock, 2 vCPU | ~57 min |
| `dedup_groups` with `source_snapshot_id` | 55,544 / 55,544 |
| `dedup_signatures` with `source_record_digest` | 55,544 / 55,544 |

Provenance is complete: `source_name = oltp:complaints+grievance_redactions`,
snapshot `sha256:a7a01cde…`. Zero legacy NULLs.

Backfill confirmed complete on the CPU box, verified directly against
production Postgres (read-only counts): `grievance_redactions` = 55,544 rows,
`dedup_signatures` = 55,544 rows, `dedup_groups` populated, for
Sambalpur/2024. The 7 Aug re-run logged `55544 redacted complaints, 55544
already indexed`.

### Decomposition, and why the third number exists

| Group | Filings | Signatories | Reading |
|---|---|---|---|
| GOV2024999640 | 26,203 | **1** | one filer, not a campaign |
| DM2024854026 | 1,291 | 1,155 | campaign |
| DM2024947088 | 1,190 | 1,079 | campaign |
| DM2024577146 | 617 | 579 | campaign |

One group is 47% of the slice and resolves to a single identity key. Every
other large group runs 89-94% signatories. On filings alone these are
indistinguishable. This is the argument for the index, and it is why the
campaign badge must key off signatory count (#180).

## 5. Analytics over the full record

Lake: 1,371,288 complaints, 6,548,820 action rows, 1,209,144 resolved
complaints carrying a closing remark.

**Closure (#76)**

| | |
|---|---|
| Ladder closures | 776,922 |
| Bare disposals | 472,782 |
| Bare share of ladder | **60.85%** |
| Bare share of all resolved | **39.10%** |
| Ladder coverage | 64.25% |

Descriptive, not a failure rate. A correct closure and a premature one are
identical in this record.

**Duplicates (#72)** — **37,299** officer-confirmed action rows (21,117
`taken up` + 16,182 `duplicate copy`), against a ROADMAP reference of 34,671
(delta +2,628). Source: `outputs/findings/confirmed_duplicates.csv` and
`discard_reason_families.csv`, both re-run 8 August 14:11.

Corrected twice, so the lineage is worth stating. The mart originally matched
templates by equality against strings stored with suffixes and returned 8
`taken up` rows where ~21,000 exist, giving a published total of 18,432 —
still sitting in `outputs/findings/duplicate_baseline_summary.csv`, which is
stale and should not be read. An intermediate 39,937 (21,513 + 18,424) was
recorded here on 7 August and is also superseded by the 8 August re-run.
Quote 37,299.

**Discard families (#107)**

| Family | Measured | ROADMAP ref |
|---|---|---|
| Case already taken up / taken up earlier | 21,117 | 19,904 |
| No specific grievance | 16,375 | 16,340 |
| Duplicate copy | 16,182 | 14,767 |
| Needs a policy decision first | 9,125 | 9,090 |
| Not within the purview of this cell | 8,472 | 8,455 |
| Address not given | 4,114 | 4,110 |

**Misrouting** — 8,472 action rows out-of-purview.

**Spikes (#78)** — 11,935 candidate (category × district × week) spikes;
largest lift Infrastructure × Bargarh, week 2023-07-17, 2,144 filings against
a trailing mean of 2 (833.8×).

**Action-type lookup (#75)** — 74 templates covering **22.4%** of 6,548,820
action rows. The issue targets ~500 templates for ~62%. Measured against the
issue's own baseline: top-10 strings are 36.7% of rows, top-500 pairs 60.3%.

## 6. Sarvam

Not called. Not because governance blocks it — because nobody has supplied
`SARVAM_API_KEY` and run it.

All three provider-held-data controls (`retention_terms`,
`encryption_in_transit`, `encryption_at_rest`) are `verified=False`. That does
not gate a live call. Evaluated directly against the registered route
(`janasunani/egress/sarvam.py`):

| | |
|---|---|
| `live_use_ready` | False |
| `egress_permitted` | **True** |
| `egress_basis` | `accepted_risk` |
| Unverified controls | `retention_terms`, `encryption_in_transit`, `encryption_at_rest` |

A `RiskAcceptance` is recorded on the route (authority: Additional Chief
Secretary, Electronics & IT Department, Government of Odisha), so
`egress_permitted` is true on `accepted_risk` even though `live_use_ready`
stays false. The gate the adapter actually checks before a live call
(`janasunani/egress/sarvam.py:625`, `:825`) is `route.egress_permitted`, not
`live_use_ready`. With `enabled=True` and a real key, it would call Sarvam,
not fall back to pytesseract.

Sample size for the paired comparison, at 10 points detectable and 25%
discordance: **194 pages** at 80% power / 5% level, **259** at 90% power,
**368** at 90% power / 1% level.

## 7. Engineering

| | |
|---|---|
| `pytest` | **1019 passed, 7 skipped** |
| `ruff check .` | clean |
| Frontend build | exit 0 |
| Frontend tests | 3 passed |
| `eslint` | clean |

## 8. Known gaps

Two places where the data now exists and nothing reads it:

- `spike.sql` hardcodes `NULL::INTEGER AS distinct_clusters` with no join, so
  the mart still reports `pending dedup index` (#78).
- `UnwiredTriageProvider` returns spam only, so the duplicate and campaign
  banner branches cannot fire (#109).

Fixed since this file's `ca58f31` baseline, not re-verified end to end:

- `RecordedWorkloadPanel` and `RecordedSpikePanel` were flagged as
  constructed nowhere (#178, still open). `janasunani/serving/intelligence.py`
  now builds both from `workload.csv` / `spike.csv` (PR #201, commit
  `ce0eaea`). Whether the panels reach `recorded` for the demo slice depends
  on those two artifacts being published, which this file does not confirm.
- The PII scorecard reported no measurement (#67, closed). It is wired now
  — see §2.

Plus:

- The action-type lookup covers 22.4% against a ~62% target (#75).
