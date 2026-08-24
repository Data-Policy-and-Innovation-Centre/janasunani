# assets/data/

Aggregates, committed so that **rebuilding the deck never touches `data/`**.
Regenerating them does, and is a deliberate separate step.

| File | What it is | Provenance |
|---|---|---|
| `odisha-districts.geojson` | 30 district boundaries, WGS84, simplified to ~6 km | `ortps` repo, `data/external/district_boundaries/Odisha_Admin_District_BND_2021.shp`. Public administrative geometry, no citizen data |
| `district-counts.json` | Complaint counts by district × category | `data/interim/complaints.parquet`, grouped. Counts only: no text, no petitioner fields, nothing at record level |

District names differ between the two sources (`Khordha`/`Khurda`,
`Kendujhar`/`Keonjhar`, `Subarnapur`/`Sonepur` and six more). The crosswalk is
written out explicitly in the regeneration snippet rather than fuzzy-matched:
a wrong district on a map shown to the people who administer those districts is
the most embarrassing failure available, and near-miss names are exactly where
it would happen. All 30 map 1:1 and the build asserts it.

`scripts/build_deck_map.py` turns these two files into `../_hotspot-map.qmd`,
with the outlines pre-projected to SVG path data. That runs offline against the
committed aggregates and is the only step needed to rebuild the map.
