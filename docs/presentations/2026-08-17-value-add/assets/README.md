# assets/

Drop real figures here and replace the matching `.visual` placeholder block in
`slides.qmd` with `![](assets/<file>)`. The deck renders and presents **without**
any of these — the placeholders show what goes where — so the argument can be
reviewed and rehearsed before a single figure exists.

## The hard rule

**No screenshot of the live portal goes in this deck.** The 11 August 2026
captures carry real citizen names, mobile numbers, email addresses and home
addresses, and the source document is marked *internal, not for circulation*.
Facts derived from those screens are fine and are most of the value. The screens
themselves are not.

Where a slide needs to show a screen, it is redrawn as a schematic with invented
office names, or captured from the synthetic demo stack. Every entry below states
which.

Same rule for complaint text: the redaction before/after slide uses a petition
written by hand for the slide. Never a real complaint, redacted or otherwise.

## Checklist

Ordered by how much the deck loses without them.

- [ ] `routing_correction_waterfall.*` — **the most persuasive figure available.**
      Naive route-time gap on the left, descending waterfall as each of the four
      corrections lands, ending at the 11–23 day range. Annotate the largest drop.
      The number getting *smaller* as the work gets more careful is the point.
      Self-made from the ablation in `docs/experiments/routing-outcome-model.tex` §7.
      — slide "Four corrections"
- [ ] `past_vs_best_practice.*` — the conceptual pivot of the deck. One case
      fanning out to candidate routes, each annotated with historical resolution
      time, the historically-chosen one highlighted and visibly not the quickest.
      Self-made schematic; illustrative routes, real orders of magnitude.
      — slide "So we changed the question"
- [ ] `constraint_disagreement.*` — the correctness floor as a horizontal line
      with both estimates against it, one above (0.4335) and one below (0.3530),
      floor at 0.3868. **Reuse the visual grammar of `objective_trap.*`** so the
      audience recognises the line from the earlier slide.
      — slide "The honest gap"
- [ ] `result_interval.*` — one horizontal interval on a day axis spanning both
      estimators, historical average marked. Deliberately *not* a bar chart and
      *not* a single point: the width is the message and a point estimate would be
      misquoted within days.
      — slide "What we found"
- [ ] `reporting_surface_gap.*` — what the pendency screen shows (counts by
      office and status) beside a greyed panel of what it does not (median age,
      time to first action, time to resolve, ageing buckets).
      **REDRAWN, NOT SCREENSHOTTED.** Invented office names.
      — slide "Nobody can see how long anything takes"
- [ ] `routing_chain.*` — a two-link chain (Collector → BDO) and a four-link
      chain (CM Cell → Collector → BDO → Collector) as connected nodes, days on
      each hop, the return leg visibly doubling back. Representative chains, not
      a real case.
      — slide "Every grievance is routed"
- [ ] `objective_trap.*` — unconstrained speed ending in a route that closes
      everything on day one, beside the constrained version with the correctness
      floor drawn as a line the policy must stay above. Self-made.
      — slide "The trap"
- [ ] `pipeline_strip.*` — six linked stages left to right, simple line icons,
      the two that cost real time drawn heavier. **No stock robot or AI imagery.**
      The audience should be able to redraw this on a whiteboard.
      — slide "The six steps"
- [ ] `dedup_three_bars.*` — filings / distinct problems / distinct citizens for
      one district-year, gaps annotated. Restyle
      `docs/value-add-report/figures/fig_dedup.png` to the deck palette rather
      than reusing it at report resolution.
      — slide "The disposal rate counts the wrong thing"
- [ ] `latency_waterfall.*` — horizontal stacked bar of the scanned path, 13.7 s
      total, by stage. Reading the page (6.0 s) and drafting the summary (6.6 s)
      are 92%; the five stages between them total under a second and should be
      visibly hairline. From `pipeline_latency_development` in the benchmark
      bundle.
      — slide "Fourteen seconds"
- [ ] `redaction_before_after.*` — two panels of the same short petition,
      identical layout, redactions in the accent colour. **Include one
      over-redaction and label it** — showing a failure case on your own slide is
      the most persuasive thing available, and over-redaction has a real cost.
      **SYNTHETIC TEXT, written for this slide.**
      — slide "Privacy is the first step"
- [ ] `corpus_funnel.*` — filing to closure, with the four corpus counts overlaid
      at the right points. Not a stock database icon. Lowest priority: the slide
      works as text if this never arrives.
      — slide "What we were given"
- [ ] `workload_mass.*` — registration hours as a mass diagram, the three
      reduction mechanisms sized by area and shaded by **confidence**, not by
      magnitude. The biggest mechanism is the least certain and the figure should
      say so. Optional; the slide stands on the number alone.
      — slide "Officer workload"

## Style

Palette and type are in `custom.scss`: DPIC maroon `#8B1524`, terracotta
`#CC785C` for highlights, ivory `#FAF9F5` ground, near-black `#141413` ink,
muted `#6B6862`. Serif headings over a clean sans body.

Prefer SVG for anything self-made so it stays sharp on a projector and can be
recoloured without a re-export. Keep figures free of their own titles — the slide
heading is the title, and a figure that repeats it reads as a slide within a
slide.
