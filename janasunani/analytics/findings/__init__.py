"""Findings: reproducible, tested numbers from the record.

One module per finding. Each computes its own aggregates off a mart, renders a
Markdown fragment carrying the caveats that number must never be quoted
without, and writes both to ``outputs/findings/``.

House rules, enforced by tests rather than by convention:

* **Aggregates only.** No finding prints a row of citizen writing. Where a
  finding emits strings at all they are high-frequency dropdown templates,
  bounded by a minimum-use threshold.
* **Insight or capability, said out loud.** An *insight* is something the
  record already contained and nobody had queried; a *capability* is something
  no existing dashboard could produce. Presenting the first as the second is
  the failure mode.
"""
