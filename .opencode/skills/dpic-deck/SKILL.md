---
name: dpic-deck
description: >
  Creates PowerPoint (.pptx) presentations in the official DPIC (Data, Policy and Innovation Centre)
  style — the Government of Odisha × University of Chicago Trust partnership. Use this skill
  whenever the user asks to create, build, or make a slide deck, presentation, or pptx for DPIC,
  Janasunani, ORTPSA, grievance redressal, or any related project. Also trigger for requests like
  "make a deck in our style", "create a progress report presentation", "build slides for the committee",
  or any presentation task where the user is working in the Grievance/DPIC/ORTPSA project context.
  Always use this skill instead of generic pptx approaches when any DPIC/government-of-Odisha context is present.
---

# DPIC Deck Skill

You are building a presentation in the official DPIC house style. The design is clean,
typography-driven, and minimal — maroon as the sole accent color on white and light-gray
backgrounds. No gold/yellow, no navy blue, no icons or photography in content slides. Every
content slide carries a bold title with a maroon hairline rule and ends with a solid maroon
footer bar.

**Before writing any code**, read `references/layouts.md` for coordinate templates for each
slide type, and `scripts/build.js` for the boilerplate you should copy and adapt. Copy the
sibling `assets/` folder (logos + Odisha map watermark) alongside build.js so the title slide
renders with the institutional marks.

---

## Workflow

1. Understand the slide deck's content and structure (ask if unclear)
2. Map each slide to a layout type (see `references/layouts.md`)
3. Copy `scripts/build.js` **and the `assets/` folder** to the working directory and adapt build.js with the actual content
4. Run `node build.js` in the working directory (pptxgenjs must be installed: `npm install pptxgenjs`)
5. Convert to PDF and render thumbnails for QA:
   ```bash
   libreoffice --headless --convert-to pdf output.pptx
   rm -f slide-*.jpg && pdftoppm -jpeg -r 150 output.pdf slide
   ```
6. If the project has a local OpenCode command or script for Office conversion, use that instead of the generic LibreOffice command
7. Visually inspect every slide; fix any overflow, clipping, or spacing issues
8. Save the final .pptx to the user's workspace

---

## Style Specification

### Slide size
Use `LAYOUT_16x9` (10" × 5.625"). All coordinates below are in this coordinate system.

### Colors
```
MAROON   = "8B1524"   // Primary accent — title rule, footer bar, card/table headers, circles, labels
DKMAROON = "800000"   // Full-bleed maroon — divider + closing slide backgrounds
DARKTEXT = "1A1A1A"   // Slide titles, key text
BODYTEXT = "444444"   // Body text, bullet points
SUBTEXT  = "666666"   // Secondary/footnote/caption text
WHITE    = "FFFFFF"
CARD     = "F2F2F2"   // Card bodies, alternating table rows
CARDALT  = "F7F7F7"   // Subtle panels
BORDER   = "DDDDDD"   // Card/table borders, hairlines
STAT     = "A6A6A6"   // Stat-tile fill
```

No other colors. Never use navy, gold, yellow, or bright blue. Maroon is the only accent.

### Typography
Font: **Calibri** throughout (bold, italic, and regular variants).

| Element                    | Size  | Style        | Color    |
|----------------------------|-------|--------------|----------|
| Slide title                | 28pt  | Bold         | DARKTEXT |
| Title underrule            | —     | maroon hairline (~0.022") under title |
| Section / card header      | 15pt  | Bold         | MAROON or WHITE (on maroon fill) |
| Card label (metadata)      | 11pt  | Bold         | MAROON   |
| Body / bullet text         | 13pt  | Regular      | BODYTEXT |
| Sub-bullet / caption       | 11pt  | Italic       | SUBTEXT  |
| Stat number                | 18pt  | Bold         | DARKTEXT |
| Stat category label        | 11pt  | Italic       | MAROON   |
| Numbered item (big)        | 22pt  | Bold         | WHITE (in circle) |
| Footnote / callout         | 11–13pt | Italic     | SUBTEXT  |
| Divider / closing title    | 40–44pt | Bold       | WHITE    |

### Layout rules
- **No left accent bar; no gold, yellow, or blue of any kind**
- **Mandatory maroon footer bar on every content slide** — `x:0, y:5.325, w:10, h:0.3`, no text
- Title always at top-left: `x:0.5, y:0.25, w:9.0, h:0.5` (28pt bold DARKTEXT)
- Maroon hairline immediately under title: `x:0.5, y:0.82, w:9.0, h:0.022`
- Content area starts: `y:0.98`
- Bottom margin: all content must stay **`y ≤ 5.30`** (footer occupies `5.325–5.625`)
- Side margins: `x ≥ 0.5`, `x + w ≤ 9.5`
- Title / divider / closing slides are full-bleed: no title rule, no footer bar

---

## Slide Types

See `references/layouts.md` for exact coordinates and code patterns for each type.

| Type | When to use |
|------|-------------|
| `title` | Opening slide — logos, map watermark, org name, partnership line, title band, date |
| `divider` | Full-bleed maroon section break (Annexure, section titles) |
| `outline` | Table of contents — numbered sections |
| `header-card` | Row of 2–3 maroon-header + grey-body cards (objectives, comparisons) |
| `label-pair` | Metadata blocks — small maroon label + dark value |
| `left-rule` | White card with thick maroon left rule (multi-point breakdowns) |
| `two-col` | Two equal content panels side by side |
| `table` | Tabular data with maroon header row, alternating rows |
| `numbered-list` | Action items, recommendations, decisions — numbered circles |
| `stat-tile` | Bottom-of-slide grey stat callouts (number + maroon label) |
| `callout` | Single full-width italic synthesizing line |
| `text-block` | Single topic with body text or bullet points |
| `phase-cols` | Phased timeline (2–4 columns, each a phase) |
| `closing` | End slide — dark maroon background, "Thank You" or equivalent |

---

## Code Conventions

```javascript
// Always destructure palette at top of build.js
const MAROON   = "8B1524";
const DKMAROON = "800000";
const DARKTEXT = "1A1A1A";
const BODYTEXT = "444444";
const SUBTEXT  = "666666";
const WHITE    = "FFFFFF";
const CARD     = "F2F2F2";
const CARDALT  = "F7F7F7";
const BORDER   = "DDDDDD";
const STAT     = "A6A6A6";
const FONT     = "Calibri";

// Standard title block — call for every content slide. Adds title, maroon hairline,
// AND the mandatory footer bar.
function addTitle(slide, text) {
  slide.addText(text, {
    x:0.5, y:0.25, w:9.0, h:0.5,
    fontFace:FONT, fontSize:28, bold:true, color:DARKTEXT,
    align:"left", valign:"middle", margin:0
  });
  slide.addShape(pres.shapes.RECTANGLE, {                 // maroon hairline
    x:0.5, y:0.82, w:9.0, h:0.022, fill:{color:MAROON}, line:{color:MAROON}
  });
  slide.addShape(pres.shapes.RECTANGLE, {                 // mandatory footer bar
    x:0, y:5.325, w:10, h:0.3, fill:{color:MAROON}, line:{color:MAROON}
  });
}
```

Never use `#` before hex color values — pptxgenjs will corrupt the file.

---

## QA Checklist

Before presenting the file:
- [ ] All text fully visible, no overflow; nothing collides with the footer (content `y ≤ 5.30`)
- [ ] No blue, yellow, or gold anywhere; maroon is the only accent
- [ ] Maroon footer bar present on every content slide
- [ ] Title + maroon hairline present on every content slide
- [ ] Numbers, circles, headers, and highlights use `MAROON = "8B1524"` only
- [ ] Title slide shows both logos + faint Odisha map watermark
- [ ] Divider and closing slides have solid `DKMAROON = "800000"` background (no footer)
- [ ] Calibri throughout
