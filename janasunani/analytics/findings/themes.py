"""Local issue themes (#78-style): concentrated + rising within one category.

Groups complaints by substance (redacted text TF-IDF clustering, one category at
a time), then filters for themes that are both concentrated in one district
and rising over time. Concentrated-and-rising is the alert worth acting on.

Reads only grievance_redacted from the lake — never the raw grievance column.
Aggregates only: theme counts, shares, and top terms. No row-level text.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Optional

import polars as pl
from loguru import logger

from janasunani.analytics.marts import open_lake
from janasunani.config import OUTPUTS_DIR

DEFAULT_K = 6
MIN_THEME_SIZE = 5
CONCENTRATION_THRESHOLD = 0.55
RISING_THRESHOLD = 1.4


def _strip_placeholders(text: str) -> str:
    return " ".join(re.sub(r"\[[A-Z]+\]", " ", text).split())


def _read_slice_redacted(lake_dir: Optional[Path], district: Optional[str], year: Optional[int]) -> pl.DataFrame:
    con = open_lake(lake_dir=lake_dir, tables=("complaints", "grievance_redactions"))
    try:
        district_filter = f"AND c.district = '{district}'" if district else ""
        year_filter = f"AND c.created_year = {int(year)}" if year else ""
        sql = f"""
            SELECT c.ticket_no, c.district, c.category, c.created_on,
                   g.grievance_redacted
            FROM complaints c
            JOIN grievance_redactions g USING (ticket_no)
            WHERE g.grievance_redacted IS NOT NULL
              AND trim(g.grievance_redacted) <> ''
              {district_filter}
              {year_filter}
        """
        df = con.execute(sql).pl()
        return df
    finally:
        con.close()


def _ensure_sklearn():
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: F401
        from sklearn.cluster import KMeans  # noqa: F401
        return True
    except Exception:
        return False


def compute_themes(
    lake_dir: Optional[Path] = None,
    district: Optional[str] = None,
    year: Optional[int] = None,
    category: Optional[str] = None,
    k: int = DEFAULT_K,
) -> dict[str, pl.DataFrame]:
    df = _read_slice_redacted(lake_dir, district, year)
    if df.height == 0:
        return {"themes": pl.DataFrame(), "theme_counts": pl.DataFrame()}
    if category:
        cat = category
    else:
        vc = df.filter(pl.col("category").is_not_null()).group_by("category").len().sort("len", descending=True)
        if vc.height == 0:
            cat = "Water"
        else:
            cat = vc.row(0, named=True)["category"]
    cat_df = df.filter(pl.col("category") == cat)
    if cat_df.height < MIN_THEME_SIZE * 2:
        return {
            "themes": pl.DataFrame({"category": [cat], "note": ["insufficient data for themes"]}),
            "theme_counts": pl.DataFrame(),
        }
    texts = [_strip_placeholders(t) for t in cat_df["grievance_redacted"].to_list()]

    if not _ensure_sklearn():
        themes = []
        for txt in texts:
            first = txt.split()[0].lower() if txt.split() else "empty"
            themes.append(first[:20])
        cat_df = cat_df.with_columns(pl.Series("theme_id", themes))
    else:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.cluster import KMeans

        vec = TfidfVectorizer(max_features=500, stop_words="english", ngram_range=(1, 2), min_df=2)
        try:
            X = vec.fit_transform(texts)
        except ValueError:
            return {
                "themes": pl.DataFrame({"category": [cat], "note": ["insufficient vocabulary"]}),
                "theme_counts": pl.DataFrame(),
            }
        n_clusters = min(k, cat_df.height)
        km = KMeans(n_clusters=n_clusters, random_state=0, n_init=10)
        labels = km.fit_predict(X)
        cat_df = cat_df.with_columns(pl.Series("theme_id", [f"theme_{label}" for label in labels]))
        order_centroids = km.cluster_centers_.argsort()[:, ::-1]
        terms = vec.get_feature_names_out()
        top_terms = {}
        for i in range(n_clusters):
            top = [terms[ind] for ind in order_centroids[i, :5]]
            top_terms[f"theme_{i}"] = ", ".join(top)
        cat_df = cat_df.with_columns(
            pl.col("theme_id").replace(top_terms, default="").alias("top_terms")
        )

    cat_df = cat_df.filter(pl.col("created_on").is_not_null())
    if cat_df.height == 0:
        return {"themes": pl.DataFrame(), "theme_counts": pl.DataFrame()}
    # Use overall time midpoint for rising (more robust than median when dates tie)
    all_dates = [d for d in cat_df["created_on"].to_list() if d is not None]
    min_date = min(all_dates)
    max_date = max(all_dates)
    # Midpoint between min and max
    try:
        midpoint = min_date + (max_date - min_date) / 2
    except TypeError:
        # Fallback if dates are not datetime
        dates_sorted = sorted(all_dates)
        midpoint = dates_sorted[len(dates_sorted) // 2]

    rows = []
    for theme_id in sorted(cat_df["theme_id"].unique().to_list()):
        sub = cat_df.filter(pl.col("theme_id") == theme_id)
        size = sub.height
        if size < MIN_THEME_SIZE:
            continue
        dist_counts = sub.group_by("district").len().sort("len", descending=True)
        top_district = dist_counts.row(0, named=True)["district"]
        top_count = dist_counts.row(0, named=True)["len"]
        concentration = top_count / size if size else 0
        early = sub.filter(pl.col("created_on") <= midpoint).height
        late = sub.filter(pl.col("created_on") > midpoint).height
        rising_lift = (late / early) if early > 0 else float(late)
        is_concentrated = concentration >= CONCENTRATION_THRESHOLD
        is_rising = rising_lift >= RISING_THRESHOLD
        rows.append(
            {
                "theme_id": theme_id,
                "category": cat,
                "filings": size,
                "top_district": top_district,
                "concentration": round(concentration, 3),
                "rising_lift": round(rising_lift, 3),
                "is_concentrated": is_concentrated,
                "is_rising": is_rising,
                "is_theme": is_concentrated and is_rising,
            }
        )
    themes_df = pl.DataFrame(rows) if rows else pl.DataFrame()
    filtered = themes_df.filter(pl.col("is_theme")) if themes_df.height else pl.DataFrame()
    return {"themes": themes_df, "filtered_themes": filtered, "category": pl.DataFrame({"category": [cat]})}


def render_markdown(tables: dict[str, pl.DataFrame]) -> str:
    themes = tables.get("themes")
    filtered = tables.get("filtered_themes")
    cat = tables.get("category")
    if cat is not None and cat.height:
        cat_name = cat.row(0, named=True)["category"]
    elif themes is not None and themes.height and "category" in themes.columns:
        # The early-exit branches (too few rows, insufficient vocabulary) carry
        # the attempted category on the placeholder "themes" frame instead of a
        # separate "category" table.
        cat_name = themes.row(0, named=True)["category"]
    else:
        cat_name = "unknown"
    lines = [
        f"## Local issue themes — {cat_name}",
        "",
        "**Grouping by substance (redacted text), filtered for concentrated + rising.**",
        "A theme is an alert only when it is mostly in one place and growing.",
        "",
    ]
    if themes is None or themes.height == 0:
        lines.append("No themes computed (insufficient data).")
        return "\n".join(lines)
    if "filings" not in themes.columns:
        # compute_themes' early-exit branches (too few rows in the largest
        # category, or too little vocabulary for TF-IDF) return a one-row
        # {category, note} frame rather than the full theme schema. Reading
        # "filings" off that frame is a KeyError, not a NULL, so it must be
        # caught before the table-rendering loop below.
        note = themes.row(0, named=True).get("note", "insufficient data")
        lines.append(f"No themes computed ({note}).")
        return "\n".join(lines)
    lines.append(f"Themes found: **{themes.height}** (min size {MIN_THEME_SIZE}).")
    lines.append(f"Concentrated (≥{CONCENTRATION_THRESHOLD:.0%}) + rising (≥{RISING_THRESHOLD:.1f}×): **{filtered.height if filtered is not None else 0}**.")
    lines.append("")
    lines.append("| Theme | Filings | Top district | Concentration | Rising lift | Alert |")
    lines.append("|---|---|---|---|---|---|")
    for r in themes.sort("filings", descending=True).iter_rows(named=True):
        alert = "yes" if r["is_theme"] else "no"
        lines.append(
            f"| {r['theme_id']} | {r['filings']} | {r['top_district']} | {r['concentration']:.2f} | {r['rising_lift']:.1f}× | {alert} |"
        )
    lines.append("")
    if filtered is not None and filtered.height:
        lines.append("**Alerts (concentrated + rising):**")
        for r in filtered.iter_rows(named=True):
            lines.append(f"- {r['theme_id']} in {r['top_district']}: {r['filings']} filings, {r['concentration']:.0%} concentrated, rising {r['rising_lift']:.1f}×.")
        lines.append("")
    lines.append(
        "> Redacted text only. No raw grievance. Themes are aggregates (counts + shares); "
        "top terms are bounded vocabulary, not citizen prose."
    )
    lines.append("")
    return "\n".join(lines)


def write(tables: dict[str, pl.DataFrame], out_dir: Optional[Path] = None) -> dict[str, Path]:
    out = Path(out_dir) if out_dir else OUTPUTS_DIR / "findings"
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for name in ("themes", "filtered_themes"):
        if name in tables and tables[name] is not None and tables[name].height:
            path = out / f"{name}.csv"
            tables[name].write_csv(path)
            written[name] = path
    md = out / "themes.md"
    md.write_text(render_markdown(tables))
    written["markdown"] = md
    return written


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Local issue themes (concentrated + rising, one category)")
    parser.add_argument("--lake-dir", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=OUTPUTS_DIR / "findings")
    parser.add_argument("--district", type=str, default=None)
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--category", type=str, default=None)
    args = parser.parse_args(argv)
    tables = compute_themes(lake_dir=args.lake_dir, district=args.district, year=args.year, category=args.category)
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        if df.height:
            df.write_csv(out / f"{name}.csv")
    (out / "themes.md").write_text(render_markdown(tables))
    logger.info("Wrote themes to {}", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
