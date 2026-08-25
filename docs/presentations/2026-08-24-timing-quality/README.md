# Janasunani 2.0 — technical briefing, 24 August 2026

Ten slides, 15 to 20 minutes, for a technical audience.

The spine escalates: what we built, what we measured, where measurement runs
out, what sits outside.

1. Title
2. The record has never been read
3. Deduplication needs every record at once
4. Six stages, timed and scored
5. The department suggestion is learned from history
6. Closing a case costs nothing
7. The estimate did not survive the second year
8. What we cannot measure
9. It does more. We have not shown it does better
10. Thank you

Slides 5 to 7 are one movement. Five says what can be claimed about routing.
Six explains why speed alone cannot be the objective, since the most common
closing remark records disposal with no action taken. Seven is the estimate
that tried to respect that constraint and did not survive a second year.

The closure data sits at six rather than with the corpus at two on purpose. It
is not a standalone finding there. It is the reason the routing model needed a
correctness constraint at all.

| File | What it is |
|---|---|
| `Janasunani_2.0_Timing_and_Quality.pptx` | the deck |
| `build_deck.py` | builds it; source of truth for all copy and speaker notes |
| `architecture.svg` | the slide 3 diagram, editable |
| `architecture.png` | 300 DPI raster fallback, generated from the SVG |
| `SPEAKER_NOTES.md` | notes exported from the deck, for reading away from PowerPoint |

## Why the .pptx is committed

This directory does not follow the `make deck` convention used by
`2026-08-17-value-add`, which keeps a `slides.qmd` and gitignores the rendered
output. That convention assumes the source fully regenerates the output inside
the checkout. Here it does not: `build_deck.py` clones slide archetypes out of
the **GAPG 18 August 2026 reference deck**, which lives in Box and cannot be
committed (see below). Without that file nobody can rebuild, so the built deck
is committed as the artifact.

`make deck DECK=2026-08-24-timing-quality` will fail. That is expected.

## Rebuilding

The reference deck is required and is **not in the repo**. Its slides 4 and 8
are live portal screenshots carrying citizen names, mobile numbers and
addresses, and the source is marked not for circulation. Copy it in first:

```bash
cp "~/Library/CloudStorage/Box-Box/2. Projects/21. Governance/Grievance Redressal/Presentation/August 2026/GAPG_Grievance_Redressal_Briefing_Aug_18_2026 .pptx" reference.pptx
uv run --no-project --with python-pptx python build_deck.py
```

It is gitignored here, so a stray copy cannot be committed by accident.

## Editing the diagram

`architecture.svg` is hand-written and holds the reference deck's palette and
type. After editing, regenerate the raster fallback and rebuild:

```bash
rsvg-convert -w 3570 architecture.svg -o architecture.png   # 300 DPI at 11.9in
uv run --no-project --with python-pptx python build_deck.py
```

The picture is embedded as a **true SVG part** with the PNG as fallback, so
PowerPoint renders it as vector at any zoom. Fonts list Calibri and Cambria
first, with Carlito and Caladea as metric-compatible fallbacks, because neither
Microsoft face is installed on every build machine.

## Checking it

```bash
soffice --headless --convert-to pdf Janasunani_2.0_Timing_and_Quality.pptx
pdftoppm -jpeg -r 90 Janasunani_2.0_Timing_and_Quality.pdf slide
```

LibreOffice renders the **PNG fallback**, not the SVG, and substitutes
Carlito/Caladea for Calibri/Cambria. That is enough to check layout and
overflow. It does not prove the SVG resolved, so open the .pptx itself to
confirm the diagram is vector and its type matches the surrounding slides.

## Rules that travel with this deck

- No portal screenshots, ever. No real complaint text.
- Every figure on a slide traces to a named artifact, recorded in the speaker
  notes with its sample size.
- No release-gate vocabulary on slides.
- Withdrawn numbers stay off: the in-sample crosswalk figures (60.9 / 67.5 /
  72.8), PII coverage 49.6%, the superseded duplicate totals, MuRIL 71.04% as a
  current figure, and any routing time saving.
- No accuracy, latency, or billed-cost claim for the outside provider. None has
  been established.
