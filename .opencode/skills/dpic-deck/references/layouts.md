# DPIC Slide Layout Reference

All coordinates are for `LAYOUT_16x9` (10" × 5.625").
Copy the code patterns below and substitute your content.

**Slide chrome rules (every content slide):**
- Bold title top-left + a full-width maroon **hairline** rule under it.
- A solid **maroon footer bar** (`8B1524`, 0.3" tall, full width, no text) at the very bottom.
- The footer occupies `y 5.325–5.625`, so **all content must stay at `y ≤ 5.30`**.
- Title / divider / closing slides are full-bleed and do **not** get the title rule or footer bar.

---

## Palette

```javascript
const MAROON   = "8B1524";   // Primary accent — rule, footer, card headers, table headers, circles, labels
const DKMAROON = "800000";   // Full-bleed maroon — divider + closing backgrounds
const DARKTEXT = "1A1A1A";   // Slide titles, key text
const BODYTEXT = "444444";   // Card / body copy
const SUBTEXT  = "666666";   // Secondary text, captions, footnotes
const WHITE    = "FFFFFF";
const CARD     = "F2F2F2";   // Card bodies, alternating table rows
const CARDALT  = "F7F7F7";   // Subtle panels
const BORDER   = "DDDDDD";   // Hairlines, card/table borders
const STAT     = "A6A6A6";   // Stat-tile fill
const FONT     = "Calibri";
```

---

## Shared helper: `addTitle(slide, text)`

Every content slide starts with this. It adds the title, the maroon hairline rule, **and the
mandatory footer bar**. Add it once at the top of your build.js and call it on each slide.

```javascript
function addTitle(slide, text) {
  slide.addText(text, {
    x:0.5, y:0.25, w:9.0, h:0.5,
    fontFace:FONT, fontSize:28, bold:true, color:DARKTEXT,
    align:"left", valign:"middle", margin:0
  });
  slide.addShape(pres.shapes.RECTANGLE, {        // full-width maroon hairline
    x:0.5, y:0.82, w:9.0, h:0.022,
    fill:{color:MAROON}, line:{color:MAROON}
  });
  addFooter(slide);
}

function addFooter(slide) {                        // mandatory maroon footer bar
  slide.addShape(pres.shapes.RECTANGLE, {
    x:0, y:5.325, w:10, h:0.3,
    fill:{color:MAROON}, line:{color:MAROON}
  });
}
// Content area: y from 0.98 down to 5.30 (footer starts at 5.325)
```

---

## Layout: `title`

Opening slide — white background, faint Odisha map watermark, two institutional logos in the
top corners, a maroon title band, plus date/location. Assets load from an `assets/` folder
(`odisha-map.png`, `uchicago-logo.png`, `odisha-logo.png`) resolved next to build.js; if
missing, the slide still renders typographically. No title rule / footer bar on this slide.

```javascript
let s = pres.addSlide();
s.background = { color: WHITE };

// Top + bottom maroon edge rules
s.addShape(pres.shapes.RECTANGLE, { x:0, y:0,     w:10, h:0.08, fill:{color:MAROON}, line:{color:MAROON} });
s.addShape(pres.shapes.RECTANGLE, { x:0, y:5.545, w:10, h:0.08, fill:{color:MAROON}, line:{color:MAROON} });

// Faint greyscale map watermark behind the title block (transparency ~88)
if (ODISHA_MAP)    s.addImage({ path:ODISHA_MAP, x:2.85, y:1.35, w:4.3, h:3.35, transparency:88 });
// Logos, top corners
if (UCHICAGO_LOGO) s.addImage({ path:UCHICAGO_LOGO, x:0.45, y:0.35, w:0.95, h:0.95 });
if (ODISHA_LOGO)   s.addImage({ path:ODISHA_LOGO,   x:8.7,  y:0.35, w:0.82, h:0.95 });

// Org name + tagline
s.addText("Data, Policy and Innovation Centre", {
  x:1.0, y:1.55, w:8.0, h:0.6,
  fontFace:FONT, fontSize:30, bold:true, color:MAROON, align:"center", charSpacing:1, margin:0
});
s.addText("A Government of Odisha and University of Chicago Trust Partnership", {
  x:1.0, y:2.2, w:8.0, h:0.35,
  fontFace:FONT, fontSize:14, italic:true, color:SUBTEXT, align:"center", margin:0
});

// Maroon title band (partial width, white gaps left/right) with the presentation title
s.addShape(pres.shapes.RECTANGLE, { x:1.5, y:3.1, w:7.0, h:0.85, fill:{color:MAROON}, line:{color:MAROON} });
s.addText("YOUR PRESENTATION TITLE HERE", {
  x:1.5, y:3.1, w:7.0, h:0.85,
  fontFace:FONT, fontSize:22, bold:true, color:WHITE, align:"center", valign:"middle", margin:0
});

// Date / location
s.addText("Month DD, YYYY · Bhubaneswar, Odisha", {
  x:1.0, y:4.13, w:8.0, h:0.32, fontFace:FONT, fontSize:13, color:DARKTEXT, align:"center", margin:0
});
```

---

## Layout: `divider`

Full-bleed maroon section break (`Thank You`, `Annexure`, section titles). Uses `DKMAROON`
(`800000`), centered white title, optional italic subtitle. No title rule / footer bar.

```javascript
let s = pres.addSlide();
s.background = { color: DKMAROON };
s.addText("Section Title", {
  x:1.0, y:2.0, w:8.0, h:1.6,
  fontFace:FONT, fontSize:40, bold:true, color:WHITE, align:"center", valign:"middle", margin:0
});
// Optional subtitle: add a second run at fontSize:16, italic, color:"F2DBDB"
```

---

## Layout: `outline`

Table of contents. Each item = a maroon numbered box + light-gray row with description.

```javascript
let s = pres.addSlide();
s.background = { color: WHITE };
addTitle(s, "Outline");

const items = [
  { num:"01", text:"First section title" },
  { num:"02", text:"Second section title" },
  { num:"03", text:"Third section title" },   // max ~5 items comfortably
];

const rowH = 0.72, startY = 0.98, gap = 0.06;
items.forEach((item, i) => {
  const y = startY + i * (rowH + gap);
  s.addShape(pres.shapes.RECTANGLE, { x:0.5, y, w:9.0, h:rowH, fill:{color:CARD}, line:{color:CARD} });
  s.addShape(pres.shapes.RECTANGLE, { x:0.5, y, w:0.9, h:rowH, fill:{color:MAROON}, line:{color:MAROON} });
  s.addText(item.num,  { x:0.5,  y, w:0.9, h:rowH, fontFace:FONT, fontSize:22, bold:true, color:WHITE, align:"center", valign:"middle", margin:0 });
  s.addText(item.text, { x:1.55, y, w:7.8, h:rowH, fontFace:FONT, fontSize:19, color:DARKTEXT, align:"left", valign:"middle", margin:[0,0,0,8] });
});
```

For an item with a sub-line (italic caption below), increase `rowH` to 0.88 and add a
`fontSize:11, italic:true, color:SUBTEXT` line at `y+0.52`.

---

## Layout: `header-card` (primary content pattern)

A row of 2–3 two-row cards: maroon header (bold white) over a light-grey body. Use for program
objectives, theme summaries, comparisons. Call after `addTitle`.

```javascript
function headerCardRow(s, cards, { y = 1.0, h = 1.7, headerH = 0.5, gap = 0.15 } = {}) {
  const N = cards.length;
  const colW = (9.0 - (N - 1) * gap) / N;
  cards.forEach((c, i) => {
    const x = 0.5 + i * (colW + gap);
    s.addShape(pres.shapes.RECTANGLE, { x, y, w:colW, h:headerH, fill:{color:MAROON}, line:{color:MAROON} });
    s.addText(c.header, { x:x+0.12, y, w:colW-0.24, h:headerH, fontFace:FONT, fontSize:15, bold:true, color:WHITE, align:"left", valign:"middle", margin:0 });
    s.addShape(pres.shapes.RECTANGLE, { x, y:y+headerH, w:colW, h:h-headerH, fill:{color:CARD}, line:{color:BORDER} });
    s.addText(c.body, { x:x+0.12, y:y+headerH+0.08, w:colW-0.24, h:h-headerH-0.16, fontFace:FONT, fontSize:13, color:BODYTEXT, align:"left", valign:"top", margin:0 });
  });
}
// headerCardRow(s, [{header,body}, ...], { y:1.0, h:2.4 });
```

---

## Layout: `label-pair`

Metadata-style row: light-grey blocks, each with a small bold maroon label over a larger dark
value.

```javascript
function labelPairRow(s, pairs, { y = 1.0, h = 1.0, gap = 0.15 } = {}) {
  const N = pairs.length;
  const colW = (9.0 - (N - 1) * gap) / N;
  pairs.forEach((p, i) => {
    const x = 0.5 + i * (colW + gap);
    s.addShape(pres.shapes.RECTANGLE, { x, y, w:colW, h, fill:{color:CARD}, line:{color:BORDER} });
    s.addText(p.label.toUpperCase(), { x:x+0.12, y:y+0.1, w:colW-0.24, h:0.28, fontFace:FONT, fontSize:11, bold:true, color:MAROON, align:"left", margin:0 });
    s.addText(p.value, { x:x+0.12, y:y+0.38, w:colW-0.24, h:h-0.46, fontFace:FONT, fontSize:16, color:DARKTEXT, align:"left", valign:"top", margin:0 });
  });
}
```

---

## Layout: `left-rule`

White card with a thin grey border and a thick maroon vertical rule on the left edge only.
Bold dark title, then grey description. Use for multi-point breakdowns within a single theme.

```javascript
function leftRuleCard(s, { x, y, w, h, title, body }) {
  s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill:{color:WHITE}, line:{color:BORDER} });
  s.addShape(pres.shapes.RECTANGLE, { x, y, w:0.08, h, fill:{color:MAROON}, line:{color:MAROON} });
  s.addText(title, { x:x+0.22, y:y+0.1, w:w-0.34, h:0.34, fontFace:FONT, fontSize:14, bold:true, color:DARKTEXT, align:"left", margin:0 });
  s.addText(body,  { x:x+0.22, y:y+0.46, w:w-0.34, h:h-0.56, fontFace:FONT, fontSize:12, color:BODYTEXT, align:"left", valign:"top", margin:0 });
}
```

---

## Layout: `table`

Clean data table — maroon header row, alternating white/`CARD` rows, optional footnote.

```javascript
let s = pres.addSlide();
s.background = { color: WHITE };
addTitle(s, "Slide Title");

function cellVal(text, bg) {
  return { text, options:{ fill:{color:bg}, color:BODYTEXT, fontFace:FONT, fontSize:13, align:"left", valign:"middle", margin:[4,8,4,8] } };
}
const hdr = (t) => ({ text:t, options:{ fill:{color:MAROON}, color:WHITE, bold:true, fontFace:FONT, fontSize:13, align:"center", margin:[4,8,4,8] } });

const tableData = [
  [ hdr("Column 1"), hdr("Column 2"), hdr("Column 3") ],
  [ cellVal("Row 1 Col 1", WHITE), cellVal("Row 1 Col 2", WHITE), cellVal("Row 1 Col 3", WHITE) ],
  [ cellVal("Row 2 Col 1", CARD),  cellVal("Row 2 Col 2", CARD),  cellVal("Row 2 Col 3", CARD)  ],
];

s.addTable(tableData, {
  x:0.5, y:1.0, w:9.0,
  colW:[3.5, 1.5, 4.0],          // adjust to content; must sum to 9.0
  border:{ pt:0.5, color:BORDER },
  autoPage:false,
});
// Keep the table above the footer (bottom of last row ≤ 5.30).
// For a row-label column: set color:MAROON, bold:true on the first cell of each row.
```

---

## Layout: `numbered-list`

Action items / recommendations. Each item: maroon circle with number + bold header + body, on
a light-gray row.

```javascript
let s = pres.addSlide();
s.background = { color: WHITE };
addTitle(s, "Slide Title");

const items = [
  { num:"1", header:"First action item header", body:"What this means and what is being asked." },
  { num:"2", header:"Second action item",        body:"Description here." },
];

const N = items.length;
const rowH = N <= 3 ? 1.28 : (N === 4 ? 1.0 : 0.84);
const startY = 0.98, gap = 0.05;
items.forEach((item, i) => {
  const y = startY + i * (rowH + gap);
  s.addShape(pres.shapes.RECTANGLE, { x:0.5, y, w:9.0, h:rowH, fill:{color:CARD}, line:{color:CARD} });
  s.addShape(pres.shapes.OVAL, { x:0.62, y:y+(rowH-0.58)/2, w:0.58, h:0.58, fill:{color:MAROON}, line:{color:MAROON} });
  s.addText(item.num,    { x:0.62, y:y+(rowH-0.58)/2, w:0.58, h:0.58, fontFace:FONT, fontSize:22, bold:true, color:WHITE, align:"center", valign:"middle", margin:0 });
  s.addText(item.header, { x:1.4, y:y+0.1, w:7.95, h:0.36, fontFace:FONT, fontSize:13, bold:true, color:DARKTEXT, align:"left", valign:"middle", margin:0 });
  s.addText(item.body,   { x:1.4, y:y+0.5, w:7.95, h:rowH-0.58, fontFace:FONT, fontSize:12, color:BODYTEXT, align:"left", valign:"top", margin:0 });
});
```

---

## Layout: `stat-tile` (bottom-of-slide callouts)

A row of equal-width grey (`A6A6A6`) tiles: bold number/descriptor on the first line (dark),
maroon italic category label on the second. Sits low on the slide, above the footer.

```javascript
function statTileRow(s, stats, { y = 4.35, h = 0.85, gap = 0.15 } = {}) {
  const N = stats.length;
  const colW = (9.0 - (N - 1) * gap) / N;
  stats.forEach((st, i) => {
    const x = 0.5 + i * (colW + gap);
    s.addShape(pres.shapes.RECTANGLE, { x, y, w:colW, h, fill:{color:STAT}, line:{color:STAT} });
    s.addText(st.number, { x:x+0.1, y:y+0.1, w:colW-0.2, h:0.4, fontFace:FONT, fontSize:18, bold:true, color:DARKTEXT, align:"left", valign:"middle", margin:0 });
    s.addText(st.label,  { x:x+0.1, y:y+0.48, w:colW-0.2, h:h-0.52, fontFace:FONT, fontSize:11, italic:true, color:MAROON, align:"left", valign:"top", margin:0 });
  });
}
```

---

## Component: `callout` (italic synthesizing line)

A single full-width italic sentence used as a closing/synthesizing statement on a slide (not a
quote box — just styled text).

```javascript
function calloutLine(s, text, { y = 4.85 } = {}) {
  s.addText(text, { x:0.5, y, w:9.0, h:0.4, fontFace:FONT, fontSize:13, italic:true, color:SUBTEXT, align:"left", valign:"middle", margin:0 });
}
```

---

## Layout: `two-col`

Two side-by-side panels. Each panel: maroon section header + body text or bullets, optional
secondary note. Panels are `CARD` fill with a `BORDER` hairline. `panelH` is capped at 4.25 so
the panels clear the footer bar.

```javascript
let s = pres.addSlide();
s.background = { color: WHITE };
addTitle(s, "Slide Title");

const panelY = 0.98, panelH = 4.25, panelW = 4.45, gap = 0.1;

function addPanel(x, panel) {
  s.addShape(pres.shapes.RECTANGLE, { x, y:panelY, w:panelW, h:panelH, fill:{color:CARD}, line:{color:BORDER} });
  s.addText(panel.header, { x:x+0.15, y:panelY+0.12, w:panelW-0.3, h:0.35, fontFace:FONT, fontSize:15, bold:true, color:MAROON, align:"left", margin:0 });
  // body: string, or an array of bullets via { bullet:true }
  s.addText(panel.body, { x:x+0.15, y:panelY+0.55, w:panelW-0.3, h:panelH-0.8, fontFace:FONT, fontSize:13, color:BODYTEXT, align:"left", valign:"top", margin:0 });
}
addPanel(0.5, left);
addPanel(0.5 + panelW + gap, right);
```

---

## Layout: `phase-cols`

Phased timeline — 2 to 4 columns, each a phase: number band, period, header, bullets, output.
`colH` capped at 4.05 (bottom ≈ 5.23) to clear the footer.

```javascript
let s = pres.addSlide();
s.background = { color: WHITE };
addTitle(s, "Slide Title");

const phases = [
  { num:"01", period:"Month 1", header:"Phase Name", bullets:["Bullet one","Bullet two"], output:"Output: deliverable" },
  // ...
];
const N = phases.length;
const colW = (9.0 - (N-1)*0.1) / N;
const startX = 0.5, colY = 1.0, colH = 4.05;

phases.forEach((ph, i) => {
  const cx = startX + i * (colW + 0.1);
  s.addShape(pres.shapes.RECTANGLE, { x:cx, y:colY, w:colW, h:colH, fill:{color:CARD}, line:{color:BORDER} });
  s.addShape(pres.shapes.RECTANGLE, { x:cx, y:colY, w:colW, h:0.58, fill:{color:MAROON}, line:{color:MAROON} });
  s.addText(ph.num,    { x:cx+0.08, y:colY, w:0.5, h:0.58, fontFace:FONT, fontSize:20, bold:true, color:WHITE, align:"left", valign:"middle", margin:0 });
  s.addText(ph.period, { x:cx+0.62, y:colY, w:colW-0.72, h:0.58, fontFace:FONT, fontSize:12, color:"F2DBDB", align:"left", valign:"middle", margin:0 });
  s.addText(ph.header, { x:cx+0.1, y:colY+0.66, w:colW-0.2, h:0.38, fontFace:FONT, fontSize:15, bold:true, color:MAROON, align:"left", margin:0 });
  const bullets = ph.bullets.map((b, bi) => ({ text:b, options:{ bullet:true, breakLine: bi < ph.bullets.length-1 } }));
  s.addText(bullets,   { x:cx+0.1, y:colY+1.1, w:colW-0.2, h:2.2, fontFace:FONT, fontSize:12, color:BODYTEXT, align:"left", valign:"top", margin:0 });
  s.addText(ph.output, { x:cx+0.1, y:colY+3.45, w:colW-0.2, h:0.5, fontFace:FONT, fontSize:11, italic:true, color:SUBTEXT, align:"left", valign:"top", margin:0 });
});
```

---

## Layout: `text-block`

Single large text area — for a key finding, a request to the committee, or a short narrative.

```javascript
let s = pres.addSlide();
s.background = { color: WHITE };
addTitle(s, "Slide Title");

s.addText("Your main text here — a direct ask, a key finding, or a short narrative paragraph. Keep it readable at a glance.", {
  x:0.8, y:1.1, w:8.4, h:2.9, fontFace:FONT, fontSize:21, color:DARKTEXT, align:"left", valign:"middle", margin:0
});
s.addText("Supporting note or caveat here.", {
  x:0.5, y:4.4, w:9.0, h:0.5, fontFace:FONT, fontSize:13, color:SUBTEXT, align:"left", valign:"middle", margin:0
});
```

---

## Layout: `closing`

Dark maroon (`DKMAROON` = `800000`) full-bleed background, centered white text. Final slide.
Optional contact line below in smaller white text. No footer bar.

```javascript
let s = pres.addSlide();
s.background = { color: DKMAROON };
s.addText("Thank You", {
  x:1.0, y:2.0, w:8.0, h:1.4, fontFace:FONT, fontSize:44, bold:true, color:WHITE, align:"center", valign:"middle", margin:0
});
```
