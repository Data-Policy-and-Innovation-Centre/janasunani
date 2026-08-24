"""Queue-aware counterfactual simulation. NOT IMPLEMENTED.

This is a placeholder for plan E10 / model M6, and nothing in this package or in
`docs/experiments/routing-outcome-model.tex` may report a simulated Delta until
it exists. The first draft of the write-up quoted a congestion-adjusted
"9.5 -> ~7 days" from this file; no such number was ever computed.

The intended design, for whoever picks it up:

    Y^(m)(flow) = mu^(m)(x, flow) + U^(m)

with `U^(m)` resampled within `category x district` strata from the fitted
model's residuals. A full replay must treat the selected action as a multi-stage
route: enqueue and release the grievance at every role in sequence, and let
those events update the shared queues seen by later grievances. Capacity is a
rate in cases per day, estimated as trailing departures divided by 90 days;
`Q / trailing_departure_count` alone is measured in 90-day windows, not days.

An entry-role-only replay may be implemented as an explicitly labelled load
stress test, but it is not the interference correction. Nothing may report
`Delta_sim` until the full multi-role event path, service discipline, zero-
departure fallback, and shared-queue updates are implemented and tested.
"""

from __future__ import annotations

__all__: list[str] = []
