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

- [ ] `hotspot_map.*` — **the most persuasive artifact available, and the only
      genuinely interactive one.** Odisha's 30 districts as a choropleth by
      complaint rate for a selected category, switchable live in the room,
      drilling to block on click. The `aqli-map` deck.gl block in
      `../../../../ml-ai-climate-change/presentation/slides.qmd` is the working
      pattern; a plain inline SVG choropleth is an acceptable simpler route.
      Aggregates only, from `janasunani/analytics` category × district counts.
      **A static PNG fallback must sit on the same slide** for a projector with
      no GPU or no wifi.
      — slide "Question one · What are people complaining about?"
- [ ] `spike_decomposition.*` — one worked spike, built in three fragments: the
      weekly series against the same period last year, then the same rise split
      into filings, distinct problems, distinct citizens. The separation of the
      three lines is the capability, and it only lands as a surprise if the
      single line is seen first, so it must be steppable rather than one static
      image. Restyle `docs/value-add-report/figures/fig_spike.png` or rebuild
      from `janasunani/analytics/findings/spike.py`.
      — slide "Concentrated *and* rising"
- [ ] `campaign_two_lenses.*` — one detected campaign shown twice: the issue view
      (filings and citizens climbing, distinct problems flat) beside the people
      view (concentrated in a few blocks, skewed on channel). Needs both halves
      of Part 2 at once, which is why it closes the section. Aggregate
      description only, never a citizen.
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
- [ ] `latency_waterfall.*` — the 13.7 s scanned path as a horizontal bar
      segmented by stage, ideally filling left to right. Reading the page (6.0 s)
      and drafting the summary (6.6 s) are 92%; the five stages between them are
      hairline and should look it. From `pipeline_latency_development`.
      **The pipeline slide has no `.visual` block for this** — it carries two
      tables already and a third element overflows it. Replace the TIME row's
      prose with the figure when it exists, or give it its own slide.
      — slide "The pipeline"
- [ ] `dedup_three_bars.*` — filings / distinct problems / distinct citizens for
      one district-year, with the gaps annotated. The message is the *gap*, which
      a number cannot show. Restyle
      `docs/value-add-report/figures/fig_dedup.png` to the deck palette rather
      than reusing it at report resolution.
      — slide "Deduplication"

Slides with no figure and no need of one: the legend, the three section
dividers, the processing summary, the desk card (CSS, already built), "Who is
complaining", and everything in Part 3. Do not add art to them.

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
