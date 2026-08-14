"""Generate the Odisha complaint dot map partial for the value-add deck.

Reads two committed aggregate files and writes a self-contained Quarto partial
with the district outlines and every dot position already projected to SVG:

    assets/data/odisha-districts.geojson   30 district boundaries, WGS84
    assets/data/district-counts.json       complaint counts by district x category
    -> assets/_hotspot-map.qmd

Both inputs are aggregates and carry no citizen data, so **rebuilding the deck
never touches `data/`**. Regenerating the inputs themselves does, and is a
separate, deliberate step -- see the header of `assets/data/README.md`.

A DOT MAP, NOT A CHOROPLETH. Shading a whole district by volume answers "which
district is big", because a populous district files more of everything. Dots
carry two channels instead of one: colour is the theme and area is the count, so
water and housing are separable at a glance and a district's mix is visible
rather than averaged away. Each district gets one dot per theme, arranged on a
small ring around its centroid.

Dots are at DISTRICT centroids, which is the honest limit of the geometry we
hold. Block would be better -- `block` is populated on 82.7% of filings across
461 district-block pairs -- but there is no block boundary file in the repo and
no coordinate on any table, so block dots need a public boundary download plus a
crosswalk over 427 spellings. Until that exists a dot means "somewhere in this
district", and the readout says the district name for exactly that reason.
Do not fake it by scattering dots inside the district outline: that invents a
precision the data does not have.

Projection is equirectangular with a cos(lat) correction at Odisha's mean
latitude. At this extent the difference from a proper equal-area projection is
smaller than the 6 km simplification tolerance already applied to the outlines,
and it keeps the runtime dependency-free: the slide gets plain SVG and needs no
projection library, no deck.gl and no network.

    uv run python scripts/build_deck_map.py
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

DECK = Path("docs/presentations/2026-08-17-value-add")
WIDTH = 1000.0
PAD = 8.0

# One colour per theme. Chosen to stay distinguishable side by side and to sit
# in the deck's palette rather than fighting it; maroon is reserved for the
# largest single theme so the map still reads as part of this deck.
THEME_COLOURS = {
    "Housing": "#8B1524",
    "Social Welfare": "#CC785C",
    "Infrastructure": "#3E6B7C",
    "Land Matters": "#7A6A3E",
    "Police Case": "#5B4A6B",
    "Service Matters": "#4A7A5C",
}
# Largest dot radius in SVG units, for the biggest district-theme count in the
# data. Everything else scales by sqrt so AREA is proportional to the count --
# scaling the radius instead exaggerates big values by squaring them.
R_MAX = 26.0
R_MIN = 3.5
# Radius of the ring the theme dots sit on around a district centroid.
RING = 21.0


def _rings(geometry: dict) -> list[list[list[float]]]:
    kind, coords = geometry["type"], geometry["coordinates"]
    if kind == "Polygon":
        return coords
    if kind == "MultiPolygon":
        return [ring for polygon in coords for ring in polygon]
    raise ValueError(f"unsupported geometry {kind!r}")


def _centroid(rings: list[list[list[float]]]) -> tuple[float, float]:
    """Area-weighted centroid of the largest ring.

    The largest ring, not all of them: several Odisha districts carry small
    detached islands in the geojson, and averaging those in drags the dot off
    the landmass the district is actually on.
    """
    best, best_area = None, -1.0
    for ring in rings:
        a = cx = cy = 0.0
        for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
            cross = x0 * y1 - x1 * y0
            a += cross
            cx += (x0 + x1) * cross
            cy += (y0 + y1) * cross
        a *= 0.5
        if abs(a) > best_area:
            # Degenerate ring (zero area) would divide by zero; fall back to the
            # mean vertex, which is fine because such a ring is a speck anyway.
            if abs(a) < 1e-12:
                best = (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))
            else:
                best = (cx / (6 * a), cy / (6 * a))
            best_area = abs(a)
    assert best is not None
    return best


def build(deck: Path) -> Path:
    geo = json.loads((deck / "assets/data/odisha-districts.geojson").read_text())
    counts = json.loads((deck / "assets/data/district-counts.json").read_text())
    districts = counts["districts"]
    themes = [c for c in counts["categories"] if c != "All"]

    lons = [p[0] for f in geo["features"] for r in _rings(f["geometry"]) for p in r]
    lats = [p[1] for f in geo["features"] for r in _rings(f["geometry"]) for p in r]
    lon0, lon1, lat0, lat1 = min(lons), max(lons), min(lats), max(lats)
    kx = math.cos(math.radians((lat0 + lat1) / 2))
    scale = (WIDTH - 2 * PAD) / ((lon1 - lon0) * kx)
    height = (lat1 - lat0) * scale + 2 * PAD

    def project(lon: float, lat: float) -> tuple[float, float]:
        return (PAD + (lon - lon0) * kx * scale, height - PAD - (lat - lat0) * scale)

    paths, dots = [], []
    peak = max(
        (districts.get(f["properties"]["d"], {}).get(t, 0) for f in geo["features"] for t in themes),
        default=1,
    ) or 1

    for feature in sorted(geo["features"], key=lambda f: f["properties"]["d"]):
        name = feature["properties"]["d"]
        rings = _rings(feature["geometry"])
        d = []
        for ring in rings:
            pts = [f"{x:.1f},{y:.1f}" for x, y in (project(lon, lat) for lon, lat in ring)]
            d.append("M" + "L".join(pts) + "Z")
        paths.append(f'<path class="hs-d" data-d="{name}" d="{"".join(d)}"></path>')

        cx, cy = project(*_centroid(rings))
        entry = districts.get(name, {})
        for i, theme in enumerate(themes):
            n = entry.get(theme, 0)
            if not n:
                continue
            angle = 2 * math.pi * i / len(themes) - math.pi / 2
            x = cx + RING * math.cos(angle)
            y = cy + RING * math.sin(angle)
            r = max(R_MIN, R_MAX * math.sqrt(n / peak))
            dots.append(
                f'<circle class="hs-dot" data-t="{theme}" data-d="{name}" data-n="{n}" '
                f'cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{THEME_COLOURS[theme]}"></circle>'
            )

    # Largest dots first so a small dot is never hidden under a big one.
    dots.sort(key=lambda s: -float(s.split('r="')[1].split('"')[0]))

    chips = "".join(
        f'<button data-t="{t}" style="--c:{THEME_COLOURS[t]}"><i></i>{t}</button>' for t in themes
    )

    body = f"""```{{=html}}
<!--
  GENERATED by scripts/build_deck_map.py. Do not hand-edit; re-run the script.
  Outlines and dot positions are projected at build time, so this needs no
  projection library, no deck.gl and no network at presentation time.

  Colour is the theme, dot AREA is the count. Dots sit at district centroids,
  which is what the geometry in the repo supports -- see the script header on
  why they are not at block level and must not be scattered to look as if they
  are.
-->
<div class="hotspot fb-host" id="hotspot">
  <div class="fb"><img src="assets/fallback/hotspot.png" alt="Odisha districts with one dot per theme, sized by complaint volume"></div>

  <div class="hs-controls">
    <div class="hs-chips" id="hs-chips">
      <button data-t="__all" class="active"><i class="hs-all"></i>All themes</button>{chips}
    </div>
  </div>
  <svg class="hs-map" viewBox="0 0 {WIDTH:.0f} {height:.0f}" preserveAspectRatio="xMidYMid meet">
    <g class="hs-outlines">{"".join(paths)}</g>
    <g class="hs-dots">{"".join(dots)}</g>
  </svg>
  <div class="hs-readout" id="hs-readout">Hover a dot &middot; area is the number of complaints</div>
</div>

<script>
(function () {{
  var root = document.getElementById("hotspot");
  if (!root) return;
  var readout = document.getElementById("hs-readout");
  var REST = "Hover a dot &middot; area is the number of complaints";

  root.querySelectorAll(".hs-dot").forEach(function (c) {{
    c.addEventListener("mouseenter", function () {{
      readout.innerHTML = "<b>" + c.dataset.d + "</b> &middot; " + c.dataset.t.toLowerCase() +
        " &middot; " + (+c.dataset.n).toLocaleString() + " complaints";
    }});
  }});
  root.addEventListener("mouseleave", function () {{ readout.innerHTML = REST; }});

  document.getElementById("hs-chips").addEventListener("click", function (ev) {{
    var b = ev.target.closest("button");
    if (!b) return;
    var t = b.dataset.t;
    this.querySelectorAll("button").forEach(function (x) {{ x.classList.remove("active"); }});
    b.classList.add("active");
    root.querySelectorAll(".hs-dot").forEach(function (c) {{
      c.classList.toggle("hs-mute", t !== "__all" && c.dataset.t !== t);
    }});
  }});

  root.classList.add("live");
}})();
</script>
```
"""
    out = deck / "assets/_hotspot-map.qmd"
    out.write_text(body, encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck", type=Path, default=DECK)
    args = parser.parse_args()
    out = build(args.deck)
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
