# PII gold ensemble — Amendment 2026-08-05

## What this decides

The labeller left the project; there is no human capacity for a full 85-page pass before 14 August. The Privacy Scorecard is **Committed** (DELIVERY.md Table 1), so the choice is between an ensemble gold and no measurement. This doc locks the protocol that makes the ensemble valid and not circular.

## Decisions

- **Span the vendors.** Two models from different vendors agreeing is stronger evidence than three from one family — they do not share training data, so they do not share blind spots. The harness records `vendor` per member and the report surfaces `Y` overall and per-entity.
- **Zero-shot, independent, no draft.** Each member labels with no access to the Presidio draft (`scripts/bootstrap_pii_gold.py` → `detect_pii_spans`) and no sight of other members. Anchoring on a pre-annotation is the effect the original `do not automate` rule was avoiding; that reason survives.
- **Union is recall-favouring by construction.** `union_spans` deduplicates exact `(start,end,entity)` triples only; overlapping but non-identical spans stay separate and surface as contested rather than being merged away. This is §5.1's false-negative priority: leaked PII is the release-critical failure and F1 hides it. `unanimous` spans are accepted; contested form the adjudication queue.
- **Adjudication queue is contested only.** A few hundred items, not 85 pages. A person adjudicates the queue; expectation is seconds per item.
- **15-20 page full human verification** bounds the all-missed rate — the one direction union cannot self-check (every model missed the same PII). Deterministic by seed; manifest names the pages.
- **Publisher writes the claim sentence with N, Y, Z filled in** (`claim_sentence`) so downstream reporting cannot overstate a bare `X%` without provenance.

## Needs input

- **Named adjudicator** for the contested queue (contested spans only).
- **Odia reader** for the Odia slice and for the 15-20 page human check — the ensemble will produce Odia labels better than Presidio (which has no Odia name model) but the gap cannot be quantified without a reader. Same person as #65 plausibly, one ask.
- **AWS creds + DVC remote** to push the promoted gold (`data/external/pii_gold.jsonl.dvc` pointer only in git; bytes to private S3). Gold holds citizen text, never in git (`data/output/` is gitignored; `no-raw-data-in-git` CI guard).

## What may be claimed

> Presidio missed X% of PII on a gold set labelled by an ensemble of N independent frontier models, which agreed with one another on Y% of spans, with disagreements human-adjudicated and a Z-page sample fully human-verified.

Not: `PII coverage is X%` with no statement of how the gold was produced.

## Reuse

If #66's n=50 human labels land, score the ensemble against them and report agreement — that pass was substantive (52 PHONE deleted, 34→89 EMAIL, NAME reworked) so it is a real anchor.
