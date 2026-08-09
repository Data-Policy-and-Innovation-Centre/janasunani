## Summary

-

## What Changed

-

## Validation

The full pre-PR list is in [CONTRIBUTING.md](../CONTRIBUTING.md#required-checks).
The ones that catch most problems:

- [ ] `uv run ruff check .`
- [ ] `uv run --extra serving --extra pipeline-core pytest`
- [ ] The Presidio-gated suite (`--extra pii`, its own environment) when
      redaction, PII detection or evaluation changed.
- [ ] Gold-metric gates run **locally**, with the observed numbers pasted
      below. DVC-tracked data never reaches Actions, so CI cannot run them.
- [ ] Notebooks stripped (`uv run nbstripout --install` or equivalent).
- [ ] No proprietary data files committed directly to Git.
- [ ] DVC pointers / `dvc.lock` updated when approved data or pipeline outputs
      changed.

## Review

- [ ] Self-reviewed the diff against the base branch end to end.
- [ ] `@codex review` requested, and **re-requested after the last push** — the
      verdict binds to the commit it read.
- [ ] Every review thread resolved. The `no-push-only-pr` ruleset will not let
      this merge otherwise, whether the finding was fixed, filed as an issue,
      or rejected with evidence.

A second human reviewer is the ideal and should be added whenever one is
available. In practice the team is often one engineer, so the Codex review is
the standing substitute rather than an extra step on top of a human pair. That
is not a lower bar: findings are claims to verify, not instructions to execute,
and the protocol for handling them is in
[CONTRIBUTING.md](../CONTRIBUTING.md#review).

Keep this PR small enough to be reviewed in one sitting, and split unrelated
changes into separate PRs before requesting review.

## Data And Delivery Notes

- [ ] This PR does not modify data.
- [ ] This PR ingests or versions data through DVC.
- [ ] This PR updates generated exhibits.
- [ ] This PR requires `make deliver` after merge.

Notes:

-
