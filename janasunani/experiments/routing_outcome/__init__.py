"""Flow-aware outcome routing experiments.

Offline research only. Nothing here is on a serving or pipeline code path, and
no `JANASUNANI_ROUTER=outcome` provider exists yet.

Read `docs/experiments/routing-outcome-model.tex` for the framework and
`docs/plans/2026-08-11-routing-disposal-optimization.md` for the intended build
order. Both distinguish what has been run from what is designed but not built;
keep that distinction when adding to this package.

Stage order:

    dataset.py            build the mart, label C, cap T, write splits
    e0_flow_census.py     E0/E1 census and descriptive audit
    train.py              fit mu (days | correct) and pi (P(correct))
    ope.py --split val    off-policy evaluation of the flow policy

Shared pieces: `features.FeatureEncoder` (fit on train, reused everywhere),
`flow.decode_esc_chain` (the single chain decoder), `propensity`, `policy`.
"""
