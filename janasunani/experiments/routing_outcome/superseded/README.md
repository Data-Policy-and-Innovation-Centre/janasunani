# Superseded results — 11 August 2026

**Do not cite these numbers.** They are kept only as a record of what the first
run produced, and as the reference point for checking what the fixes change.

Both files came out of a pipeline with four defects that each moved the headline
Δ, documented in `../features.py`, `../policy.py` and `../ope.py`:

1. Categorical codes were fitted per dataframe, so every model scored on val or
   test was reading permuted category levels.
2. `benefitted.notna()` was a feature of the correctness classifier, and
   `benefitted` partly defines the `correct` label.
3. The two arms of the contrast used different estimators — a GBM prediction for
   history, a raw training cell mean for the policy — so `delta_policy` is
   largely the gap between the two estimators.
4. The "oracle" arm minimised the fitted value *over the realised rows in each
   cell*, i.e. over sampling noise, which is why it produced a 46.9-day bound.

`2026-08-11-val-ope.json` is the run reported in the first draft of
`docs/experiments/routing-outcome-model.tex` (val 2024). `2026-08-11-test-ope.json`
is test 2025, which is 34.4% censored and whose completers are selected on
having closed fast.

A seventh defect surfaced during the re-run itself: the corrected encoder
stringified its level index when fitting but not when applying it, so
`pending_with_id` (int64) never matched its own levels and was a constant -1 in
every split. Fixed, with a regression test.

## What replaced them

`dataset.py → train.py → ope.py --split val` on the corrected code. The mart
reproduced every split count exactly, and corrected two descriptive figures:
`correct=1` is 363,595 rather than 363,883 (which closes a 288-row discrepancy
in the old mart), and the decode rate is 100% of the 1,344,908 chains that
exist — the 26,380 remaining cases carry no chain at all rather than one that
fails to decode.

Headline: **17.96 days** DR on val 2024 (SE 6.80) for the top-3 eligible arm,
against 9.5 (22.4 DR) before. Positive under all four validation
specifications. See the package README and the .tex §Results.
