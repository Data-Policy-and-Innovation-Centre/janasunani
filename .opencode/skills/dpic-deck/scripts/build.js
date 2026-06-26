/**
 * DPIC Deck Builder — Boilerplate
 * Copy this file AND the sibling `assets/` folder to your working directory, then run:
 *   npm install pptxgenjs
 *   node build.js
 *
 * Output: output.pptx in the current directory.
 *
 * Assets (logos + Odisha map watermark) are loaded from ./assets relative to this
 * file. If the folder is missing, the title slide falls back to a typographic-only
 * layout — the build never crashes.
 */

const pptxgen = require("pptxgenjs");
const fs = require("fs");
const path = require("path");

// ── Palette (DPIC Design System) ──────────────────────────────────────────────
const MAROON   = "8B1524";   // Primary accent — titles' rule, footer bar, card headers, table headers, circles, labels
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

// ── Assets ────────────────────────────────────────────────────────────────────
// Resolve the assets folder whether build.js runs in place (scripts/, so ../assets)
// or was copied next to an assets/ folder in a working dir (./assets).
const ASSET_DIRS = [
  path.join(process.cwd(), "assets"),
  path.join(__dirname, "assets"),
  path.join(__dirname, "..", "assets"),
];
function asset(name) {
  for (const d of ASSET_DIRS) {
    const p = path.join(d, name);
    try { if (fs.existsSync(p)) return p; } catch { /* ignore */ }
  }
  return null;
}
const ODISHA_MAP = asset("odisha-map.png");
const UCHICAGO_LOGO = asset("uchicago-logo.png");
const ODISHA_LOGO = asset("odisha-logo.png");

// ── Geometry ──────────────────────────────────────────────────────────────────
// Slide is 10" × 5.625". The maroon footer bar occupies y 5.325–5.625, so all
// slide content must stay at y ≤ 5.30.

// ── Presentation setup ───────────────────────────────────────────────────────
let pres = new pptxgen();
pres.layout = "LAYOUT_16x9";  // 10" × 5.625"
pres.title  = "DPIC Presentation";
pres.author = "DPIC";

// ── Shared helpers ───────────────────────────────────────────────────────────

/** Bold title + maroon hairline rule + mandatory maroon footer bar. */
function addTitle(slide, text) {
  slide.addText(text, {
    x:0.5, y:0.25, w:9.0, h:0.5,
    fontFace:FONT, fontSize:28, bold:true, color:DARKTEXT,
    align:"left", valign:"middle", margin:0
  });
  // Full-width maroon hairline under the title
  slide.addShape(pres.shapes.RECTANGLE, {
    x:0.5, y:0.82, w:9.0, h:0.022,
    fill:{color:MAROON}, line:{color:MAROON}
  });
  addFooter(slide);
}

/** Mandatory solid maroon footer bar (no text) — every content slide. */
function addFooter(slide) {
  slide.addShape(pres.shapes.RECTANGLE, {
    x:0, y:5.325, w:10, h:0.3,
    fill:{color:MAROON}, line:{color:MAROON}
  });
}

/** Plain table cell. */
function cell(text, bg, opts = {}) {
  return {
    text,
    options: {
      fill:     { color: bg },
      color:    opts.color   || BODYTEXT,
      bold:     opts.bold    || false,
      italic:   opts.italic  || false,
      fontFace: FONT,
      fontSize: opts.fontSize || 13,
      align:    opts.align   || "left",
      valign:   "middle",
      margin:   [4, 8, 4, 8],
    }
  };
}

/** Header cell (maroon background, white text). */
function hdrCell(text, opts = {}) {
  return cell(text, MAROON, { color:WHITE, bold:true, align:"center", ...opts });
}

// ── Slide builders ───────────────────────────────────────────────────────────

function titleSlide({ title, subtitle, date, tag }) {
  let s = pres.addSlide();
  s.background = { color: WHITE };

  // Top + bottom maroon edge rules
  s.addShape(pres.shapes.RECTANGLE, { x:0, y:0,     w:10, h:0.08, fill:{color:MAROON}, line:{color:MAROON} });
  s.addShape(pres.shapes.RECTANGLE, { x:0, y:5.545, w:10, h:0.08, fill:{color:MAROON}, line:{color:MAROON} });

  // Faint greyscale Odisha map watermark behind the title block
  if (ODISHA_MAP) {
    s.addImage({ path:ODISHA_MAP, x:2.85, y:1.35, w:4.3, h:3.35, transparency:88 });
  }

  // Two institutional logos, top corners
  if (UCHICAGO_LOGO) s.addImage({ path:UCHICAGO_LOGO, x:0.45, y:0.35, w:0.95, h:0.95 });
  if (ODISHA_LOGO)   s.addImage({ path:ODISHA_LOGO,   x:8.7,  y:0.35, w:0.82, h:0.95 });

  // Org name
  s.addText("Data, Policy and Innovation Centre", {
    x:1.0, y:1.55, w:8.0, h:0.6,
    fontFace:FONT, fontSize:30, bold:true, color:MAROON,
    align:"center", charSpacing:1, margin:0
  });
  // Partnership tagline
  s.addText("A Government of Odisha and University of Chicago Trust Partnership", {
    x:1.0, y:2.2, w:8.0, h:0.35,
    fontFace:FONT, fontSize:14, italic:true, color:SUBTEXT, align:"center", margin:0
  });

  // Maroon title band (partial width, white gaps left/right)
  const bandY = subtitle ? 2.95 : 3.1;
  const bandH = subtitle ? 1.05 : 0.85;
  s.addShape(pres.shapes.RECTANGLE, { x:1.5, y:bandY, w:7.0, h:bandH, fill:{color:MAROON}, line:{color:MAROON} });
  s.addText(
    subtitle
      ? [ { text:title,    options:{ fontSize:22, bold:true, color:WHITE, breakLine:true } },
          { text:subtitle, options:{ fontSize:13, italic:true, color:"F2DBDB" } } ]
      : title,
    { x:1.5, y:bandY, w:7.0, h:bandH, fontFace:FONT, fontSize:22, bold:true, color:WHITE, align:"center", valign:"middle", margin:0 }
  );

  // Date / location
  if (date) s.addText(date, {
    x:1.0, y:bandY+bandH+0.18, w:8.0, h:0.32,
    fontFace:FONT, fontSize:13, color:DARKTEXT, align:"center", margin:0
  });
  // Optional tag (e.g. "Ongoing Work", "Draft")
  if (tag) s.addText(tag, {
    x:1.0, y:bandY+bandH+0.55, w:8.0, h:0.3,
    fontFace:FONT, fontSize:12, italic:true, color:SUBTEXT, align:"center", margin:0
  });
}

function dividerSlide({ title, subtitle }) {
  // Full-bleed maroon section break (Thank You, Annexure, section titles).
  let s = pres.addSlide();
  s.background = { color: DKMAROON };
  s.addText(
    subtitle
      ? [ { text:title,    options:{ fontSize:40, bold:true, color:WHITE, breakLine:true } },
          { text:subtitle, options:{ fontSize:16, italic:true, color:"F2DBDB" } } ]
      : title,
    { x:1.0, y:2.0, w:8.0, h:1.6, fontFace:FONT, fontSize:40, bold:true, color:WHITE, align:"center", valign:"middle", margin:0 }
  );
}

function closingSlide({ text, contact }) {
  let s = pres.addSlide();
  s.background = { color: DKMAROON };
  s.addText(text || "Thank You", {
    x:1.0, y:2.0, w:8.0, h:1.4,
    fontFace:FONT, fontSize:44, bold:true, color:WHITE,
    align:"center", valign:"middle", margin:0
  });
  if (contact) s.addText(contact, {
    x:1.0, y:3.5, w:8.0, h:0.4,
    fontFace:FONT, fontSize:14, color:WHITE, align:"center", margin:0
  });
}

function outlineSlide({ title, items }) {
  // items: [{ num, text, subtitle? }]
  let s = pres.addSlide();
  s.background = { color: WHITE };
  addTitle(s, title || "Outline");

  const rowH   = items.some(i => i.subtitle) ? 0.88 : 0.72;
  const startY = 0.98;
  const gap    = 0.06;

  items.forEach((item, i) => {
    const y = startY + i * (rowH + gap);
    s.addShape(pres.shapes.RECTANGLE, { x:0.5, y, w:9.0, h:rowH, fill:{color:CARD}, line:{color:CARD} });
    s.addShape(pres.shapes.RECTANGLE, { x:0.5, y, w:0.9, h:rowH, fill:{color:MAROON}, line:{color:MAROON} });
    s.addText(item.num, { x:0.5, y, w:0.9, h:rowH, fontFace:FONT, fontSize:22, bold:true, color:WHITE, align:"center", valign:"middle", margin:0 });
    s.addText(item.text, { x:1.55, y:y+(item.subtitle ? 0.06 : 0), w:7.8, h:item.subtitle ? 0.44 : rowH, fontFace:FONT, fontSize:19, color:DARKTEXT, align:"left", valign:"middle", margin:[0,0,0,8] });
    if (item.subtitle) {
      s.addText(item.subtitle, { x:1.55, y:y+0.52, w:7.8, h:0.28, fontFace:FONT, fontSize:11, italic:true, color:SUBTEXT, align:"left", margin:[0,0,0,8] });
    }
  });
}

function numberedListSlide({ title, items }) {
  // items: [{ num, header, body }]
  let s = pres.addSlide();
  s.background = { color: WHITE };
  addTitle(s, title);

  const N      = items.length;
  const rowH   = N <= 3 ? 1.28 : (N === 4 ? 1.0 : 0.84);
  const startY = 0.98;
  const gap    = 0.05;

  items.forEach((item, i) => {
    const y = startY + i * (rowH + gap);
    s.addShape(pres.shapes.RECTANGLE, { x:0.5, y, w:9.0, h:rowH, fill:{color:CARD}, line:{color:CARD} });
    s.addShape(pres.shapes.OVAL, { x:0.62, y:y+(rowH-0.58)/2, w:0.58, h:0.58, fill:{color:MAROON}, line:{color:MAROON} });
    s.addText(item.num, { x:0.62, y:y+(rowH-0.58)/2, w:0.58, h:0.58, fontFace:FONT, fontSize:22, bold:true, color:WHITE, align:"center", valign:"middle", margin:0 });
    s.addText(item.header, { x:1.4, y:y+0.1, w:7.95, h:0.36, fontFace:FONT, fontSize:13, bold:true, color:DARKTEXT, align:"left", valign:"middle", margin:0 });
    s.addText(item.body,   { x:1.4, y:y+0.5, w:7.95, h:rowH-0.58, fontFace:FONT, fontSize:12, color:BODYTEXT, align:"left", valign:"top", margin:0 });
  });
}

function twoColSlide({ title, left, right }) {
  // left/right: { header, body (string or string[]), secondary? }
  let s = pres.addSlide();
  s.background = { color: WHITE };
  addTitle(s, title);

  const panelY = 0.98;
  const panelH = 4.25;
  const panelW = 4.45;

  function addPanel(x, panel) {
    s.addShape(pres.shapes.RECTANGLE, { x, y:panelY, w:panelW, h:panelH, fill:{color:CARD}, line:{color:BORDER} });
    s.addText(panel.header, { x:x+0.15, y:panelY+0.12, w:panelW-0.3, h:0.35, fontFace:FONT, fontSize:15, bold:true, color:MAROON, align:"left", margin:0 });

    if (Array.isArray(panel.body)) {
      const bulletItems = panel.body.map((b, bi) => ({ text:b, options:{ bullet:true, breakLine: bi < panel.body.length-1 } }));
      s.addText(bulletItems, { x:x+0.15, y:panelY+0.55, w:panelW-0.3, h:panelH-0.8, fontFace:FONT, fontSize:13, color:BODYTEXT, align:"left", valign:"top", margin:0 });
    } else {
      s.addText(panel.body, { x:x+0.15, y:panelY+0.55, w:panelW-0.3, h:panel.secondary ? panelH-1.5 : panelH-0.8, fontFace:FONT, fontSize:16, color:DARKTEXT, align:"left", valign:"middle", margin:0 });
    }

    if (panel.secondary) {
      s.addShape(pres.shapes.RECTANGLE, { x:x+0.15, y:panelY+panelH-0.9, w:panelW-0.3, h:0.02, fill:{color:BORDER}, line:{color:BORDER} });
      s.addText(panel.secondary, { x:x+0.15, y:panelY+panelH-0.84, w:panelW-0.3, h:0.7, fontFace:FONT, fontSize:11, italic:true, color:SUBTEXT, align:"left", valign:"top", margin:0 });
    }
  }

  addPanel(0.5, left);
  addPanel(0.5 + panelW + 0.1, right);
}

// ── Card components (add onto a slide already built with addTitle) ─────────────

/** Header-Card row: maroon header + grey body, 2–3 across. cards:[{header,body}] */
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

/** Label-Pair row: grey block, small maroon label + larger dark value. pairs:[{label,value}] */
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

/** Left-Rule card: white fill, thin border, thick maroon left rule. */
function leftRuleCard(s, { x, y, w, h, title, body }) {
  s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill:{color:WHITE}, line:{color:BORDER} });
  s.addShape(pres.shapes.RECTANGLE, { x, y, w:0.08, h, fill:{color:MAROON}, line:{color:MAROON} });
  s.addText(title, { x:x+0.22, y:y+0.1, w:w-0.34, h:0.34, fontFace:FONT, fontSize:14, bold:true, color:DARKTEXT, align:"left", margin:0 });
  s.addText(body, { x:x+0.22, y:y+0.46, w:w-0.34, h:h-0.56, fontFace:FONT, fontSize:12, color:BODYTEXT, align:"left", valign:"top", margin:0 });
}

/** Stat-tile row (bottom-of-slide callouts): grey tiles, bold number + maroon italic label. */
function statTileRow(s, stats, { y = 4.35, h = 0.85, gap = 0.15 } = {}) {
  const N = stats.length;
  const colW = (9.0 - (N - 1) * gap) / N;
  stats.forEach((st, i) => {
    const x = 0.5 + i * (colW + gap);
    s.addShape(pres.shapes.RECTANGLE, { x, y, w:colW, h, fill:{color:STAT}, line:{color:STAT} });
    s.addText(st.number, { x:x+0.1, y:y+0.1, w:colW-0.2, h:0.4, fontFace:FONT, fontSize:18, bold:true, color:DARKTEXT, align:"left", valign:"middle", margin:0 });
    s.addText(st.label, { x:x+0.1, y:y+0.48, w:colW-0.2, h:h-0.52, fontFace:FONT, fontSize:11, italic:true, color:MAROON, align:"left", valign:"top", margin:0 });
  });
}

/** Italic callout line — full-width synthesizing/closing statement. */
function calloutLine(s, text, { y = 4.85 } = {}) {
  s.addText(text, {
    x:0.5, y, w:9.0, h:0.4,
    fontFace:FONT, fontSize:13, italic:true, color:SUBTEXT, align:"left", valign:"middle", margin:0
  });
}

// ── EXAMPLE DECK (replace with your content) ─────────────────────────────────

titleSlide({
  title:    "Your Presentation Title Here",
  subtitle: "Optional subtitle line",
  date:     "Month DD, YYYY · Bhubaneswar, Odisha",
  tag:      "Ongoing Work",  // remove if not needed
});

outlineSlide({
  items: [
    { num:"01", text:"First section" },
    { num:"02", text:"Second section" },
    { num:"03", text:"Third section" },
  ]
});

// Header-card row + stat-tile row + callout on one slide
(() => {
  let s = pres.addSlide();
  s.background = { color: WHITE };
  addTitle(s, "Program Objectives");
  headerCardRow(s, [
    { header:"Partnership", body:"Joint Government of Odisha and UChicago Trust effort to strengthen service delivery." },
    { header:"Approach",    body:"Data-driven diagnosis of grievance redressal bottlenecks." },
    { header:"Outcome",     body:"Faster, more transparent resolution for citizens." },
  ], { y:1.0, h:2.4 });
  statTileRow(s, [
    { number:"1.2M", label:"Grievances analyzed" },
    { number:"28",   label:"Departments covered" },
    { number:"45d",  label:"Median resolution time" },
  ], { y:3.6 });
  calloutLine(s, "Maroon is the only accent — every slide closes on the footer bar.", { y:4.6 });
})();

twoColSlide({
  title: "Background",
  left: {
    header: "Left Panel Header",
    body:   "Main text for the left panel goes here.",
    secondary: "Supporting context or status note here."
  },
  right: {
    header: "Right Panel Header",
    body:   ["First bullet point", "Second bullet point", "Third bullet point"]
  }
});

dividerSlide({ title: "Annexure", subtitle: "Supporting detail" });

numberedListSlide({
  title: "For the Committee's Consideration",
  items: [
    { num:"1", header:"First action item", body:"Description of what is being asked and why." },
    { num:"2", header:"Second action item", body:"Description here." },
    { num:"3", header:"Third action item", body:"Description here." },
  ]
});

closingSlide({ text: "Thank You", contact: "dpic@uchicagotrust.org" });

// ── Write file ────────────────────────────────────────────────────────────────
pres.writeFile({ fileName: "output.pptx" })
  .then(() => console.log("✓ output.pptx written"))
  .catch(e => { console.error(e); process.exit(1); });
