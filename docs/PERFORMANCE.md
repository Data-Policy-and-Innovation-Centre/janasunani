# Performance record

Every number here was measured, not quoted. Measured 2026-08-07 against
`main` at `0dd7909`, after the Sprint 3 merges.

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
| NAME (Odia/Indian names) | **see below** |

Aadhaar is matched on shape alone (`[2-9]` + 11 digits), so context words
only boost confidence. Numbers beginning 0 or 1 are correctly not matched:
no real Aadhaar starts with those.

### The name gap

40 probes, 10 Odia/Indian full names across 4 sentence framings, scored on
whether every token of the name was covered:

| | Count | Share |
|---|---|---|
| Fully redacted | 21 | **52%** |
| Partially redacted (given name left exposed) | 2 | 5% |
| Missed entirely | 17 | **42%** |
| **Any leak** | **19** | **48%** |

Framing changes the result sharply:

| Framing | Full | Partial | Missed |
|---|---|---|---|
| `{name} reports that …` | 6 | 2 | 2 |
| `My name is {name} and …` | 6 | 0 | 4 |
| `Applicant: {name}. …` | 3 | 0 | 7 |
| `This grievance is filed by {name} of Sambalpur district.` | 6 | 0 | 4 |

A Western control name (`John Smith`) was fully redacted where the Indian
name in the same sentence was not. The recognizer is spaCy `en_core_web_sm`
NER, which is weak on Indian person names.

**Operational consequence.** On a live demo, a submission containing an
Indian name has roughly a coin-flip chance of showing that name unredacted
on screen. Use a prepared submission, or expect to talk about this.

Legacy reference for comparison: the DSI report records **80.56% any-overlap,
50.00% exact** — English, typed text, and not a threshold. The 40-probe
figure above is synthetic and is not a substitute for the gold set.

### What is not measured

`janasunani-pipeline pii_scorecard` cannot yet report a missed-PII rate. It
passes empty predictions to the scorer, so every entity reads
`overlap_recall=0.000`, `missed_rate=1.000` by construction. See #67. Its
slicing, language split and thin-slice guard do work.

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

**Duplicates (#72)** — 39,937 officer-confirmed (21,513 `taken up` + 18,424
`duplicate copy`), **3.3%** of resolved-with-remark. Corrected this sprint
from a published 18,432: the mart matched templates by equality against
strings that are stored with suffixes, and matched 8 rows where ~21,500
exist.

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

Not called. All three provider-held-data controls (`retention_terms`,
`encryption_in_transit`, `encryption_at_rest`) are `verified=False`, so
`route.live_use_ready is False`. With `enabled=True` and a real key the
adapter falls back to pytesseract **without making a network call** and
audits `reason="SarvamGovernanceError"`.

Sample size for the paired comparison, at 10 points detectable and 25%
discordance: **194 pages** at 80% power / 5% level, **259** at 90% power,
**368** at 90% power / 1% level.

## 7. Engineering

| | |
|---|---|
| `pytest` | **914 passed, 11 skipped** |
| `ruff check .` | clean |
| Frontend build | exit 0 |
| Frontend tests | 3 passed |
| `eslint` | clean |

## 8. Known gaps

Three places where the data now exists and nothing reads it:

- `spike.sql` hardcodes `NULL::INTEGER AS distinct_clusters` with no join, so
  the mart still reports `pending dedup index` (#78).
- `RecordedWorkloadPanel` and `RecordedSpikePanel` are constructed nowhere, so
  those two supervisor panels cannot go `recorded` (#178).
- `UnwiredTriageProvider` returns spam only, so the duplicate and campaign
  banner branches cannot fire (#109).

Plus:

- Name redaction leaks 48% of Indian names on the live path (§2).
- The PII scorecard reports no measurement (#67).
- The action-type lookup covers 22.4% against a ~62% target (#75).
