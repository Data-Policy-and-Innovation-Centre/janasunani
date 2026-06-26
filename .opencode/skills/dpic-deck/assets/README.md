# DPIC Deck Assets

Institutional marks used by the `title` layout in `scripts/build.js`. Extracted from the
official `Opening Slide.pptx` template in `docs/comms-templates/`.

| File | What it is | Use |
|------|------------|-----|
| `odisha-map.png` | Black silhouette of Odisha (1383×1079) | Faint greyscale watermark behind the title block (rendered at ~88% transparency) |
| `uchicago-logo.png` | University of Chicago seal (247×247) | Top-left corner of the title slide |
| `odisha-logo.png` | Government of Odisha emblem (234×272) | Top-right corner of the title slide |

When copying `build.js` to a working directory, copy this folder alongside it. build.js
resolves the assets from `./assets`, `<scriptdir>/assets`, or `<scriptdir>/../assets`, and
falls back to a typographic-only title slide if they are missing.
