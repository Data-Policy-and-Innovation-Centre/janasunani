"""Tests for the nested DSI corpus draw.

Fixtures are synthetic files written into tmp_path. Nothing here touches the
real corpus or anything under data/.
"""

from __future__ import annotations

import json

import pytest

from janasunani.evaluation.dsi_sample import (
    DEFAULT_FLOOR,
    UNCATEGORISED,
    DocumentRecord,
    assert_nested,
    draw_nested,
    load_corpus,
    manifest_digest,
    summarise,
    write_manifest,
)


def _corpus(spec: dict[str, int]) -> list[DocumentRecord]:
    """Build records from {category: n_documents}."""
    out: list[DocumentRecord] = []
    for category, n in sorted(spec.items()):
        for i in range(n):
            ticket = f"{category[:3].upper()}{i:05d}"
            out.append(
                DocumentRecord(
                    ticket=ticket,
                    filename=f"{ticket}_complaint_2025_{i:05d}.pdf",
                    category=category,
                    size_bytes=1000 + i,
                )
            )
    return out


def test_tiers_nest_by_construction():
    """The smaller tier must be a strict subset of the larger one.

    This is the property the module exists to guarantee. If it ever fails,
    latency and quality stop describing the same documents.
    """
    corpus = _corpus({"Housing": 400, "Social Welfare": 220, "Traffic": 12, "Sports": 30})
    tiers = draw_nested(corpus, {"quality": 200, "latency": 40}, seed=7, floor=3)

    assert len(tiers["quality"]) == 200
    assert len(tiers["latency"]) == 40
    assert_nested(tiers)

    quality = {r.filename for r in tiers["quality"]}
    latency = {r.filename for r in tiers["latency"]}
    assert latency < quality
    assert quality < {r.filename for r in corpus}


def test_drawing_from_the_corpus_differs_from_drawing_from_the_tier_above():
    """Why `draw_nested` chains pools instead of redrawing from the corpus.

    The two are not the same operation. Drawing the small tier from the full
    corpus samples a different population than drawing it from the larger
    tier, so the results differ and nesting is not implied. `draw_nested`
    therefore feeds each tier the previous tier as its pool; nesting is a
    property of that construction, never of the seed.
    """
    corpus = _corpus({"Housing": 400, "Social Welfare": 220, "Traffic": 12, "Sports": 30})

    chained = draw_nested(corpus, {"quality": 200, "latency": 40}, seed=7, floor=3)
    independent = draw_nested(corpus, {"latency": 40}, seed=7, floor=3)["latency"]

    assert {r.filename for r in chained["latency"]} != {r.filename for r in independent}
    # The chained one is nested by construction; the independent one is not
    # required to be, which is exactly the risk.
    assert_nested(chained)


def test_rare_categories_survive_the_floor():
    """Without a floor the long tail vanishes and per-category accuracy dies."""
    corpus = _corpus({"Housing": 5000, "Traffic": 4, "Sports": 6})
    drawn = draw_nested(corpus, {"q": 100}, seed=1, floor=DEFAULT_FLOOR)["q"]
    per_category = summarise(drawn)["per_category"]
    assert per_category["Traffic"] >= 3
    assert per_category["Sports"] >= 3
    # Housing must not have eaten the budget.
    assert per_category["Housing"] == 100 - per_category["Traffic"] - per_category["Sports"]


def test_infeasible_floor_raises_rather_than_overshooting():
    """A budget smaller than the floors is a caller error, not a silent trim."""
    corpus = _corpus({"A": 10, "B": 10, "C": 10})
    with pytest.raises(ValueError, match="exceeds the budget"):
        draw_nested(corpus, {"q": 2}, seed=1, floor=3)


def test_draw_is_deterministic_for_a_seed():
    corpus = _corpus({"Housing": 300, "Legal": 80})
    a = draw_nested(corpus, {"q": 60, "l": 15}, seed=99, floor=3)
    b = draw_nested(corpus, {"q": 60, "l": 15}, seed=99, floor=3)
    assert manifest_digest(a["q"]) == manifest_digest(b["q"])
    assert manifest_digest(a["l"]) == manifest_digest(b["l"])

    c = draw_nested(corpus, {"q": 60, "l": 15}, seed=100, floor=3)
    assert manifest_digest(c["q"]) != manifest_digest(a["q"])


def test_budget_at_or_above_pool_returns_everything():
    corpus = _corpus({"A": 5, "B": 5})
    drawn = draw_nested(corpus, {"q": 50}, seed=1, floor=3)["q"]
    assert len(drawn) == 10


def test_assert_nested_catches_a_broken_chain():
    outer = _corpus({"A": 10})
    inner = _corpus({"B": 2})
    with pytest.raises(ValueError, match="not a subset"):
        assert_nested({"outer": outer, "inner": inner})


def test_load_corpus_joins_categories_and_keeps_uncategorised(tmp_path):
    """Real code path: files on disk joined to a real parquet."""
    polars = pytest.importorskip("polars")

    docs = tmp_path / "documents"
    docs.mkdir()
    for name in (
        "DM2024001_complaint_20250101_000000.pdf",
        "DM2024002_complaint_20250101_000001.jpg",
        "DM2024002_complaint_20250101_000002.jpg",  # same ticket, two documents
        "DM2024003_complaint_20250101_000003.pdf",  # in complaints, null category
        "DM2024999_complaint_20250101_000004.pdf",  # not in complaints at all
    ):
        (docs / name).write_bytes(b"%PDF-1.3 stub")
    (docs / ".DS_Store").write_bytes(b"junk")

    parquet = tmp_path / "complaints.parquet"
    polars.DataFrame(
        {
            "ticket_no": ["DM2024001", "DM2024002", "DM2024003"],
            "category": ["Housing", "Agriculture & Farming", None],
        }
    ).write_parquet(parquet)

    records = load_corpus(docs, parquet)

    assert len(records) == 5  # .DS_Store skipped, both docs for DM2024002 kept
    by_name = {r.filename: r for r in records}
    assert by_name["DM2024001_complaint_20250101_000000.pdf"].category == "Housing"
    # A literal ampersand is a real category name and must survive untouched.
    assert by_name["DM2024002_complaint_20250101_000001.jpg"].category == "Agriculture & Farming"
    # Present in complaints but with no category: bucketed, not dropped.
    assert by_name["DM2024003_complaint_20250101_000003.pdf"].category == UNCATEGORISED
    # Absent from complaints entirely: also bucketed, not dropped.
    assert by_name["DM2024999_complaint_20250101_000004.pdf"].category == UNCATEGORISED

    summary = summarise(records)
    assert summary["documents"] == 5
    assert summary["tickets"] == 4
    assert summary["categorised_documents"] == 3
    assert summary["distinct_categories"] == 2


def test_write_manifest_round_trips(tmp_path):
    corpus = _corpus({"Housing": 40, "Legal": 20})
    drawn = draw_nested(corpus, {"q": 20}, seed=5, floor=3)["q"]
    path = write_manifest(
        drawn, tmp_path / "m.json", name="quality", seed=5, floor=3, source="unit-test"
    )
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["name"] == "quality"
    assert body["seed"] == 5
    assert body["documents"] == 20
    assert body["digest"] == manifest_digest(drawn)
    assert len(body["documents_list"]) == 20
    # A manifest must not carry document text.
    assert "text" not in json.dumps(body)


def test_escaped_category_raises_rather_than_splitting_a_stratum(tmp_path):
    """The DSI corpus is clean; our lake is not. Fail loudly on a corpus swap.

    `Scheme & Benefits` is stored double-escaped in the lake. If this module
    were ever pointed at that corpus, keying strata on the raw string would
    split one category in two and quietly halve its floor. Raising is the
    point: the alternative is a second copy of the scoring-side unescaper
    drifting away from the original.
    """
    polars = pytest.importorskip("polars")

    docs = tmp_path / "documents"
    docs.mkdir()
    (docs / "DM2024001_complaint_20250101_000000.pdf").write_bytes(b"%PDF-1.3 stub")

    parquet = tmp_path / "complaints.parquet"
    polars.DataFrame(
        {"ticket_no": ["DM2024001"], "category": ["Scheme &amp;amp; Benefits"]}
    ).write_parquet(parquet)

    with pytest.raises(ValueError, match="HTML entity"):
        load_corpus(docs, parquet)
