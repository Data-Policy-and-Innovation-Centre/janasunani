# assets/

Drop real figures here and replace the matching `.visual` placeholder block in
`slides.qmd` with `![](assets/<file>)`. The deck renders and presents **without**
any of these — the placeholders show what goes where — so the argument can be
reviewed and rehearsed before a single figure exists.

Check your work by looking at it: `make deck-check` reports slides whose content
runs off the bottom, `make deck-shots` writes a PNG of every slide.

## The hard rule

**No screenshot of the live portal goes in this deck.** The 11 August 2026
captures carry real citizen names, mobile numbers, email addresses and home
addresses, and the source document is marked *internal, not for circulation*.
Facts derived from those screens are fine and are most of the value. The screens
themselves are not.

Where a slide needs to show a screen, it is redrawn as a schematic with invented
office names, or captured from the synthetic demo stack. The officer's-desk card
on "At the desk" is built in CSS for exactly this reason: it can never be
mistaken for a capture of the real thing.

Same rule for complaint text. Never a real complaint, redacted or otherwise.

## Checklist

Ordered by how much the deck loses without them. The first two are the reason to
give this talk at all.

- [x] `hotspot_map` — **built**, as a generated dot map (`_hotspot-map.qmd`, from
      `scripts/build_deck_map.py`), over 1,132,341 real filings. One dot per
      district per theme: colour is the theme, dot *area* is the count, chips
      filter. Dots cluster near **district centroids**, with every centre kept
      inside its source district. That is what the geometry in this repo
      supports. Block would be better — `block` is populated on 82.7%
      of filings across 461 district-block pairs — but that needs a public
      boundary download and a crosswalk over 427 spellings. **Do not scatter dots
      inside a district outline to look block-level**; it invents precision the
      data does not have. Static fallback shipped.
      — slide "What is the state learning?"
- [x] `nl_query` — **built**, as a mock (`_nl-query.qmd`, from
      `scripts/build_deck_nlq.py`). Four preset questions, the structured form
      each parses into, and the answer. **Answers are real**, computed from the
      same aggregate as the map; **parsing is canned** and the slide says so.
      Static fallback shipped.
      — slide "Can anyone ask it a question?"
- [ ] `spike_decomposition.*` — **optional now.** The slide was rewritten as four
      plain questions rather than one worked example, which is the honest form:
      the year-on-year baseline is not populated in the current output of
      `janasunani/analytics/findings/spike.py`, and its strongest candidate is a
      week when all 31 districts and 35 categories rose together — a system
      event, not a local story. Any figure added here must survive both problems.
      — slide "More than usual, for this place, at this time of year"
- [ ] `campaign_two_lenses.*` — one detected campaign shown twice: the issue view
      (filings and citizens climbing, distinct problems flat) beside the people
      view (concentrated in a few blocks, skewed on channel). **No detected
      campaign artifact exists yet**, so this cannot currently be built from real
      data — and it would sit beside a map that is real, which is exactly the
      contrast that makes an invented figure dangerous. Preferred route is to
      fold it into the spike slide as a labelled case rather than build it.
      — slide "Where the two questions meet: campaigns"
- [x] `reporting_surface_gap` — **built**, as `_reporting-gap.qmd`, in CSS. The
      pendency screen redrawn (office types only, never a named office, invented
      numbers that add up) beside the fields already in the database that never
      reach a report. Each field names its actual column in
      `janasunani/db/models.py`, so the claim is checkable rather than rhetorical.
      Reveal fragments light them one at a time; `.rg-f` overrides reveal's
      default hide so they start visible-but-grey, which is what makes an absence
      visible. No custom JS, so it degrades to the all-grey state.
      **REDRAWN, NEVER SCREENSHOTTED**, and the panel header says so on its face.
      — slide "What the officer sees today"
- [x] `latency_waterfall` — **built**, as the pipeline replay
      (`_pipeline-replay.qmd`), on its own slide. Runs at the real measured
      timings with an Odia petition feeding in, and toggles between the scanned
      and typed paths on a shared time axis. Static fallback shipped.
      — slide "Watch it run"
- [ ] `dedup_three_bars.*` — filings / distinct problems / distinct citizens for
      one district-year, with the gaps annotated. The message is the *gap*, which
      a number cannot show. Restyle
      `docs/value-add-report/figures/fig_dedup.png` to the deck palette rather
      than reusing it at report resolution. Reconcile the count first: the deck
      and briefs say 37,299 officer-confirmed repeats, while
      `outputs/findings/duplicate_baseline_summary.csv` says 18,432 confirmed and
      34,671 roadmap total. Different vintage; settle it before drawing.
      — slide "Deduplication"

Slides with no figure and no need of one: the legend, the three section
dividers, the processing summary, the three Part 1 argument slides (measurement,
cheap baselines, open vs bought), the desk card (CSS, already built), "Who is
complaining", and everything in Part 3. Do not add art to them.

## Generated and interactive components

Four components are code, not images. Three of them need JavaScript.

| Component | Source | Rebuild with |
|---|---|---|
| Pipeline replay | `_pipeline-replay.qmd`, hand-written | edit directly |
| Dot map | `_hotspot-map.qmd`, **generated** | `make deck-assets` |
| Query mock | `_nl-query.qmd`, **generated** | `make deck-assets` |
| Reporting gap | `_reporting-gap.qmd`, hand-written, CSS only | edit directly |

Do not hand-edit a generated partial: the next `make deck-assets` overwrites it.

**Every JavaScript component ships a picture of itself** in `fallback/`, and
shows that picture by default. The component's own script adds `.live` as its
last statement, which swaps the picture for the real thing. So a script that
throws halfway still leaves something on screen instead of a blank slide.

`<noscript>` would not help and is deliberately not used: reveal.js is itself
JavaScript, so with scripts fully blocked the deck does not render at all.

After changing how any component looks:

```
make deck && make deck-fallbacks && make deck
```

The second render embeds the new images. Then check the swap still works both
ways, healthy and failed, rather than assuming it.

## Style

Palette and type are in `custom.scss`: DPIC maroon `#8B1524`, terracotta
`#CC785C` for highlights, ivory `#FAF9F5` ground, near-black `#141413` ink,
muted `#6B6862`. Serif headings over a clean sans body.

Prefer SVG for anything self-made so it stays sharp on a projector and can be
recoloured without a re-export. Keep figures free of their own titles — the slide
heading is the title, and a figure that repeats it reads as a slide within a
slide.

Grey means *today* and maroon means *what we add*, consistently, in the gutter
down each slide, in the section scorecards and in the `.compare` tables. A figure
that shows a before and an after should use the same two colours the same way.
