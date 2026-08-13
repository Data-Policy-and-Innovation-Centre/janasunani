# docs/presentations/

Slide **scripts**, one directory per talk, named `YYYY-MM-DD-slug/`.

These are Quarto + reveal.js projects, and they are written to be reviewable
before they are built. `slides.qmd` carries every slide, every speaker note, and
a dashed `.visual` placeholder wherever a figure goes — saying what figure, why
that figure and not another, and where the data comes from. The deck renders and
presents with the placeholders still in it, so the argument can be rehearsed
while the figures are still being made.

Build:

```bash
make deck                             # the default deck, set in the Makefile
make deck DECK=2026-08-17-value-add   # a named one
make deck-list                        # what is available
make deck-clean                       # remove the rendered output
```

Quarto is deliberately **not** a repository dependency — it is a separate binary
needed only by whoever is building a deck. `make deck` checks for it and tells
you how to install it rather than assuming it. Rendered output (`slides.html`)
is gitignored: the `.qmd` is the source of truth, as the Markdown is for
`docs/*.docx`.

Format options live in `_quarto.yml`, not in the document front matter: document
YAML overrides project profiles in Quarto and cannot be flipped per build.

## Rules that apply to every deck here

**No live-portal screenshots.** The 11 August 2026 captures carry real citizen
names, mobile numbers, email addresses and home addresses, and the source
document is marked internal and not for circulation. Facts derived from those
screens are fine and are most of the value. The screens are not. Redraw them as
schematics with invented names, or capture the synthetic demo stack.

**No real complaint text**, redacted or otherwise. Write sample petitions by hand
for the slide.

**Every claim carries its evidence state.** The `.chip-measured` /
`.chip-estimated` / `.chip-open` chips are a visual primitive, not decoration. A
deck that shows an unresolved disagreement as clearly as it shows a result is the
only kind worth giving to this audience.

**Numbers reconcile upward.** `docs/value-add-report/` is the evidence record and
`docs/QUALITY_BENCHMARKS.md` is the register. Where a slide disagrees with
either, the slide is stale.

## Decks

| Date | Talk | Status |
|---|---|---|
| 2026-08-17 | [Value add](2026-08-17-value-add/) — what the grievance record can tell us that no report does | Script complete; figures outstanding, see its `assets/README.md` |
