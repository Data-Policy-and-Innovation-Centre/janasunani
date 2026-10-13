#!/usr/bin/env bash
# Run a note notebook on the lake and export it as HTML, PDF and Word, code hidden.
#
#   scripts/analysis/export_note.sh notebooks/disposal_2024_25.ipynb
#
# Writes outputs/reports/<name>.{html,pdf,docx}. That folder is gitignored: the
# exports carry figures and counts derived from citizen grievances.
#
# One execution feeds all three, so they cannot disagree:
#   html  nbconvert, code hidden
#   pdf   the same HTML printed by headless Chrome, A4, slightly scaled so a
#         section fits a page; page breaks are <div style="page-break-before">
#         cells in the notebook
#   docx  nbconvert to Markdown, then pandoc with a DPIC-styled template built
#         here (Calibri, maroon headings). Tables reach Word as real tables only
#         if the notebook gives DataFrames a text/markdown repr; see the
#         formatter in notebooks/disposal_2024_25.ipynb.
#
# Needs: the dev group synced (`uv sync --group dev`), the lake under data/
# (open_lake reads it), pandoc, and Chrome or Chromium ($CHROME overrides).
set -euo pipefail

NB=${1:?usage: export_note.sh notebooks/<note>.ipynb}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
NB=$(cd "$(dirname "$NB")" && pwd)/$(basename "$NB")
NAME=$(basename "$NB" .ipynb)
OUT=$REPO/outputs/reports
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

CHROME=${CHROME:-}
if [[ -z $CHROME ]]; then
  for c in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
           google-chrome chromium chromium-browser; do
    if command -v "$c" >/dev/null 2>&1 || [[ -x $c ]]; then CHROME=$c; break; fi
  done
fi
[[ -n $CHROME ]] || { echo "Chrome or Chromium not found; set \$CHROME" >&2; exit 1; }
command -v pandoc >/dev/null || { echo "pandoc not found" >&2; exit 1; }

# --no-sync: the dev group must already be installed; a sync here would try to
# fetch every extra's wheels before a two-minute notebook run.
nbc() { uv run --no-sync jupyter nbconvert "$@" 2>&1 | grep -E "Error|Writing" || true; }

# Executed from the notebook's own folder, so its relative paths resolve.
(cd "$(dirname "$NB")" && nbc --to notebook --execute "$NB" \
   --ExecutePreprocessor.timeout=3600 --output-dir "$TMP" --output executed)
EX=$TMP/executed.ipynb

mkdir -p "$OUT"
nbc --to html --no-input "$EX" --output-dir "$OUT" --output "$NAME"

# PDF: drop the filename <title> and nbconvert's heading anchors, set the page.
sed -E -e 's#<title>[^<]*</title>##' -e 's#<a class="anchor-link"[^>]*>[^<]*</a>##g' \
  -e 's#</head>#<style>@page { size: A4; margin: 12mm 14mm; } body { zoom: 0.82; }</style></head>#' \
  "$OUT/$NAME.html" > "$TMP/print.html"
"$CHROME" --headless=new --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="$OUT/$NAME.pdf" "file://$TMP/print.html" >/dev/null 2>&1

# Word: Markdown export reading each output's text/markdown first.
mkdir -p "$TMP/md"
nbc --to markdown --no-input "$EX" --output-dir "$TMP/md" --output "$NAME" \
  --NbConvertBase.display_data_priority="['text/markdown','image/png','text/plain']"
pandoc -o "$TMP/ref.docx" --print-default-data-file reference.docx
uv run --no-sync python - "$TMP/ref.docx" <<'PY'
import sys
from docx import Document
from docx.shared import Pt, RGBColor

path = sys.argv[1]
doc = Document(path)
MAROON, INK = RGBColor(0x8B, 0x15, 0x24), RGBColor(0x1A, 0x1A, 0x1A)
for style in doc.styles:
    font = getattr(style, "font", None)
    if font is None:
        continue
    if style.name in ("Normal", "Body Text", "First Paragraph", "Compact", "Table"):
        font.name, font.size, font.color.rgb = "Calibri", Pt(10.5), INK
    if style.name.startswith("Heading") or style.name == "Title":
        font.name, font.color.rgb, font.bold = "Calibri", MAROON, True
doc.save(path)
PY
# implicit_figures off: otherwise every image's alt text ("png") becomes a caption.
(cd "$TMP/md" && pandoc "$NAME.md" -f markdown-implicit_figures -t docx \
   --reference-doc="$TMP/ref.docx" -o "$OUT/$NAME.docx")

ls -la "$OUT/$NAME".{html,pdf,docx}
