"""Queue-aware counterfactual simulation. NOT IMPLEMENTED.

This is a placeholder for plan E10 / model M6, and nothing in this package or in
`docs/experiments/routing-outcome-model.tex` may report a simulated Delta until
it exists. The first draft of the write-up quoted a congestion-adjusted
"9.5 -> ~7 days" from this file; no such number was ever computed.

The intended design, for whoever picks it up:

    Y^(m)(flow) = mu^(m)(x, flow) + U^(m)

with `U^(m)` resampled within `category x district` strata from the fitted
model's residuals, and `Q_{entry_role}(t)` replayed as a discrete-event queue in
arrival order, capacity set to trailing 90-day throughput per role. The point is
to price the SUTVA violation: rerouting many cases to a popular Secretary raises
that Secretary's backlog for everyone else, so `Delta_sim < Delta_naive` exactly
when the policy concentrates load.
"""

from __future__ import annotations

__all__: list[str] = []
