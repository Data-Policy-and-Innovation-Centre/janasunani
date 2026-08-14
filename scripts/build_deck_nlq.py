"""Generate the natural-language query mock for the value-add deck.

Reads the committed aggregate and writes a self-contained Quarto partial:

    assets/data/district-counts.json  complaint counts by district x category
    -> assets/_nl-query.qmd

WHY A GENERATOR AND NOT HAND-WRITTEN HTML. Every answer the mock displays is
computed here, from the same aggregate the map uses. Nothing on the slide is a
number somebody typed to look plausible, so the demonstration can be believed
even though the parsing is canned. If the aggregate is regenerated, re-run this
and the answers move with it.

WHAT IS REAL AND WHAT IS NOT, because the slide has to say so:
  real   -- the answers, the counts, the rankings, the shares
  canned -- the parsing. The presets are fixed and nothing is typed freely.
            No model runs in the browser, and the query layer is specced
            (ROADMAP Phase 20) rather than built.

The structured form shown in the middle panel is the real target representation
from that spec: a model fills in the form and a hand-written compiler turns the
form into SQL. It never writes SQL itself. Showing the form is the honest way to
demonstrate the idea without implying the model is running.

    uv run python scripts/build_deck_nlq.py
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

DECK = Path("docs/presentations/2026-08-17-value-add")


def _rank(districts: dict, category: str, limit: int) -> list[tuple[str, int]]:
    rows = [(d, c.get(category, 0)) for d, c in districts.items()]
    return sorted(rows, key=lambda r: -r[1])[:limit]


def _share_rank(districts: dict, category: str, limit: int) -> list[tuple[str, float]]:
    rows = []
    for d, c in districts.items():
        total = c.get("All", 0)
        if total:
            rows.append((d, 100 * c.get(category, 0) / total))
    return sorted(rows, key=lambda r: -r[1])[:limit]


def _breakdown(districts: dict, district: str, limit: int) -> list[tuple[str, int]]:
    if district not in districts:
        # The aggregate is keyed by the SHAPEFILE spelling, not the lake's --
        # Baragarh not Bargarh, Anugul not Angul. Naming one that does not exist
        # should say so rather than raise a bare KeyError three frames down.
        raise SystemExit(
            f"district {district!r} is not in the aggregate. Available:\n  "
            + ", ".join(sorted(districts))
        )
    rows = [(k, v) for k, v in districts[district].items() if k != "All"]
    return sorted(rows, key=lambda r: -r[1])[:limit]


def build(deck: Path) -> Path:
    counts = json.loads((deck / "assets/data/district-counts.json").read_text())
    districts = counts["districts"]

    presets = []

    top = _rank(districts, "Infrastructure", 5)
    presets.append(
        {
            "q": "Which districts file the most infrastructure complaints?",
            "form": {
                "intent": "ranking",
                "measure": "filings",
                "dimensions": ["district"],
                "filters": [{"dim": "category", "op": "in", "values": ["Infrastructure"]}],
                "order": {"by": "measure", "dir": "desc"},
                "limit": 5,
            },
            "rows": [(d, f"{n:,}") for d, n in top],
            "bars": [n / top[0][1] for _, n in top],
            "unit": "complaints",
        }
    )

    bd = _breakdown(districts, "Sambalpur", 5)
    presets.append(
        {
            "q": "What do people in Sambalpur complain about most?",
            "form": {
                "intent": "breakdown",
                "measure": "filings",
                "dimensions": ["category"],
                "filters": [{"dim": "district", "op": "=", "values": ["Sambalpur"]}],
                "order": {"by": "measure", "dir": "desc"},
                "limit": 5,
            },
            "rows": [(k, f"{n:,}") for k, n in bd],
            "bars": [n / bd[0][1] for _, n in bd],
            "unit": "complaints",
        }
    )

    sh = _share_rank(districts, "Land Matters", 5)
    presets.append(
        {
            "q": "Where are land disputes the biggest share of complaints?",
            "form": {
                "intent": "ranking",
                "measure": "share_of_filings",
                "dimensions": ["district"],
                "filters": [{"dim": "category", "op": "in", "values": ["Land Matters"]}],
                "order": {"by": "measure", "dir": "desc"},
                "limit": 5,
            },
            "rows": [(d, f"{v:.1f}%") for d, v in sh],
            "bars": [v / sh[0][1] for _, v in sh],
            "unit": "of that district's filings",
        }
    )

    hp = _rank(districts, "Housing", 5)
    presets.append(
        {
            "q": "Which districts file the most housing complaints?",
            "form": {
                "intent": "ranking",
                "measure": "filings",
                "dimensions": ["district"],
                "filters": [{"dim": "category", "op": "in", "values": ["Housing"]}],
                "order": {"by": "measure", "dir": "desc"},
                "limit": 5,
            },
            "rows": [(d, f"{n:,}") for d, n in hp],
            "bars": [n / hp[0][1] for _, n in hp],
            "unit": "complaints",
        }
    )

    chips = "".join(
        f'<button data-i="{i}"{" class=\"active\"" if i == 0 else ""}>{html.escape(p["q"])}</button>'
        for i, p in enumerate(presets)
    )

    body = f"""```{{=html}}
<!--
  GENERATED by scripts/build_deck_nlq.py. Do not hand-edit; re-run the script.

  The ANSWERS are real, computed from assets/data/district-counts.json at build
  time. The PARSING is canned: four fixed presets, nothing typed freely, no model
  running. The slide says so and that label must stay -- the whole point of the
  measurement argument in Part 1 collapses if this slide overstates itself.
-->
<div class="nlq" id="nlq">
  <div class="nlq-chips" id="nlq-chips">{chips}</div>

  <div class="nlq-stage">
    <div class="nlq-panel nlq-ask">
      <div class="nlq-h">The question</div>
      <div class="nlq-q" id="nlq-q"></div>
    </div>
    <div class="nlq-arrow">&rarr;</div>
    <div class="nlq-panel nlq-form">
      <div class="nlq-h">What the machine understood</div>
      <pre id="nlq-form"></pre>
    </div>
    <div class="nlq-arrow">&rarr;</div>
    <div class="nlq-panel nlq-ans">
      <div class="nlq-h">The answer</div>
      <div class="nlq-rows" id="nlq-rows"></div>
    </div>
  </div>
</div>

<script>
(function () {{
  var P = {json.dumps(presets, separators=(",", ":"))};
  var root = document.getElementById("nlq");
  if (!root) return;
  var qEl = document.getElementById("nlq-q");
  var fEl = document.getElementById("nlq-form");
  var rEl = document.getElementById("nlq-rows");

  function show(i) {{
    var p = P[i];
    qEl.textContent = "\\u201c" + p.q + "\\u201d";
    fEl.textContent = JSON.stringify(p.form, null, 1);
    rEl.innerHTML = p.rows.map(function (row, k) {{
      return '<div class="nlq-row">' +
        '<span class="nlq-lab">' + row[0] + '</span>' +
        '<span class="nlq-bar"><i style="width:' + (100 * p.bars[k]).toFixed(1) + '%"></i></span>' +
        '<span class="nlq-val">' + row[1] + '</span></div>';
    }}).join("") + '<div class="nlq-unit">' + p.unit + '</div>';
  }}

  document.getElementById("nlq-chips").addEventListener("click", function (ev) {{
    var b = ev.target.closest("button");
    if (!b) return;
    this.querySelectorAll("button").forEach(function (x) {{ x.classList.remove("active"); }});
    b.classList.add("active");
    show(+b.dataset.i);
  }});

  show(0);
}})();
</script>
```
"""
    out = deck / "assets/_nl-query.qmd"
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
