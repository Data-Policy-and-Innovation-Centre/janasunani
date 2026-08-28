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


def test_load_corpus_preserves_hierarchical_tickets(tmp_path):
    """1,446 of the 70,029 corpus documents (2.06%) have a ticket with slashes.

    Codex P1 on #326. The reference bucket stores those under the full key, so
    a synced copy has them one directory down. The old flat `iterdir()` did
    not mis-parse them, it never reached them — they were silently absent from
    the corpus and therefore from every tier drawn out of it. Taking the
    basename instead is the other failure: `00535_complaint_...` parses to
    `00535`, which matches no complaint and collapses distinct tickets sharing
    a trailing segment.
    """
    polars = pytest.importorskip("polars")

    docs = tmp_path / "documents"
    (docs / "OR159" / "P" / "2021").mkdir(parents=True)
    (docs / "OR160" / "P" / "2021").mkdir(parents=True)
    (docs / "OR159/P/2021/00535_complaint_20250101_000000.pdf").write_bytes(b"a")
    # Same trailing segment, different ticket. A basename parse merges these.
    (docs / "OR160/P/2021/00535_complaint_20250101_000001.pdf").write_bytes(b"bb")
    (docs / "CMO2024001_complaint_20250101_000002.pdf").write_bytes(b"ccc")
    # Hidden directories anywhere in the path are not documents.
    (docs / ".dvc" / "cache").mkdir(parents=True)
    (docs / ".dvc" / "cache" / "x_complaint_20250101_000009.pdf").write_bytes(b"no")

    parquet = tmp_path / "complaints.parquet"
    polars.DataFrame(
        {
            "ticket_no": ["OR159/P/2021/00535", "OR160/P/2021/00535", "CMO2024001"],
            "category": ["Housing", "Traffic", "Housing"],
        }
    ).write_parquet(parquet)

    records = load_corpus(docs, parquet)

    assert len(records) == 3
    by_ticket = {r.ticket: r for r in records}
    assert set(by_ticket) == {
        "OR159/P/2021/00535",
        "OR160/P/2021/00535",
        "CMO2024001",
    }
    # Both nested documents are found, categorised, and kept distinct.
    assert by_ticket["OR159/P/2021/00535"].category == "Housing"
    assert by_ticket["OR160/P/2021/00535"].category == "Traffic"
    assert all(r.is_categorised for r in records)
    # filename is the key relative to the corpus root, not the basename —
    # a basename is not unique across subdirectories.
    assert (
        by_ticket["OR159/P/2021/00535"].filename
        == "OR159/P/2021/00535_complaint_20250101_000000.pdf"
    )


def test_load_corpus_refuses_a_file_that_is_not_a_document(tmp_path):
    """A stray manifest kept as a document becomes its own stratum.

    Silently, and with a floor allocation taken from a real category.
    """
    polars = pytest.importorskip("polars")

    docs = tmp_path / "documents"
    docs.mkdir()
    (docs / "CMO2024001_complaint_20250101_000000.pdf").write_bytes(b"a")
    (docs / "manifest.tsv").write_text("ticket\ts3_key\n")

    parquet = tmp_path / "complaints.parquet"
    polars.DataFrame(
        {"ticket_no": ["CMO2024001"], "category": ["Housing"]}
    ).write_parquet(parquet)

    with pytest.raises(ValueError, match="no '_complaint_' marker"):
        load_corpus(docs, parquet)


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


def test_load_corpus_verifies_against_the_pinned_manifest(tmp_path):
    """Codex P1 on #326: without this the corpus is whatever is on disk.

    `draw_nested` then emits valid-looking tier manifests for a different
    population. That is not hypothetical — the Box copy holds 69,844 files
    against S3's 70,029, and the registry caveat in `janasunani.samples`
    already warns to source from the manifest rather than from Box.
    """
    polars = pytest.importorskip("polars")

    docs = tmp_path / "documents"
    docs.mkdir()
    (docs / "CMO2024001_complaint_20250101_000000.pdf").write_bytes(b"a")
    (docs / "CMO2024002_complaint_20250101_000001.pdf").write_bytes(b"bb")

    parquet = tmp_path / "complaints.parquet"
    polars.DataFrame(
        {"ticket_no": ["CMO2024001", "CMO2024002"], "category": ["Housing", "Traffic"]}
    ).write_parquet(parquet)

    from janasunani.evaluation.dsi_sample import ManifestEntry

    complete = {
        "CMO2024001_complaint_20250101_000000.pdf": ManifestEntry("CMO2024001", 1, None),
        "CMO2024002_complaint_20250101_000001.pdf": ManifestEntry("CMO2024002", 2, None),
    }
    records = load_corpus(docs, parquet, manifest=complete)
    assert {r.ticket for r in records} == {"CMO2024001", "CMO2024002"}

    # A manifest listing a document nobody synced: the Box-copy failure.
    short = dict(complete)
    short["CMO2024003_complaint_20250101_000002.pdf"] = ManifestEntry("CMO2024003", 3, None)
    with pytest.raises(ValueError, match="does not match the pinned"):
        load_corpus(docs, parquet, manifest=short)

    # A document on disk the manifest does not list: the other direction.
    (docs / "CMO2024999_complaint_20250101_000003.pdf").write_bytes(b"ccc")
    with pytest.raises(ValueError, match="does not match the pinned"):
        load_corpus(docs, parquet, manifest=complete)


def test_load_corpus_takes_the_ticket_from_the_manifest_not_the_path(tmp_path):
    """The key and the ticket agree by construction, but the manifest is the
    pinned record and the parser is a derivation. Where both exist, the
    record wins — which is what makes hierarchical tickets exact rather than
    reconstructed."""
    polars = pytest.importorskip("polars")

    docs = tmp_path / "documents"
    (docs / "OR159" / "P" / "2021").mkdir(parents=True)
    key = "OR159/P/2021/00535_complaint_20250101_000000.pdf"
    (docs / key).write_bytes(b"a")

    parquet = tmp_path / "complaints.parquet"
    polars.DataFrame(
        {"ticket_no": ["OR159/P/2021/00535"], "category": ["Housing"]}
    ).write_parquet(parquet)

    from janasunani.evaluation.dsi_sample import ManifestEntry

    records = load_corpus(
        docs, parquet, manifest={key: ManifestEntry("OR159/P/2021/00535", 1, None)}
    )
    assert records[0].ticket == "OR159/P/2021/00535"
    assert records[0].is_categorised


def test_load_reference_manifest_reads_the_tsv_and_rejects_a_wrong_one(tmp_path):
    from janasunani.evaluation.dsi_sample import load_reference_manifest

    good = tmp_path / "manifest.tsv"
    good.write_text(
        "ticket\ts3_key\tsize_bytes\tmd5\n"
        "CMO2024001\tCMO2024001_complaint_20250101_000000.pdf\t100\tabc\n"
        "OR159/P/2021/00535\tOR159/P/2021/00535_complaint_20250101_000001.pdf\t200\tdef\n"
    )
    got = load_reference_manifest(good)
    first = got["CMO2024001_complaint_20250101_000000.pdf"]
    assert (first.ticket, first.size_bytes, first.md5) == ("CMO2024001", 100, "abc")
    # The hierarchical key keeps its directory prefix, which is the whole point.
    assert got["OR159/P/2021/00535_complaint_20250101_000001.pdf"].ticket == "OR159/P/2021/00535"

    wrong = tmp_path / "other.tsv"
    wrong.write_text("a\tb\n1\t2\n")
    with pytest.raises(ValueError, match="not a reference manifest"):
        load_reference_manifest(wrong)


def test_load_corpus_catches_altered_bytes_under_the_right_key(tmp_path):
    """Codex P1 on #326: matching keys is not matching content.

    A truncated, stale or replaced document stored under the expected key
    passes the key comparison, and `draw_nested` then emits well-formed tier
    manifests for altered bytes — the same silent-wrong-population failure
    the key check exists to stop, one level down.
    """
    polars = pytest.importorskip("polars")

    from janasunani.evaluation.dsi_sample import ManifestEntry

    docs = tmp_path / "documents"
    docs.mkdir()
    name = "CMO2024001_complaint_20250101_000000.pdf"
    (docs / name).write_bytes(b"the real document")

    parquet = tmp_path / "complaints.parquet"
    polars.DataFrame(
        {"ticket_no": ["CMO2024001"], "category": ["Housing"]}
    ).write_parquet(parquet)

    import hashlib

    good_md5 = hashlib.md5(b"the real document").hexdigest()  # noqa: S324
    good = {name: ManifestEntry("CMO2024001", len(b"the real document"), good_md5)}

    assert load_corpus(docs, parquet, manifest=good)  # size and md5 both agree
    assert load_corpus(docs, parquet, manifest=good, verify="md5")

    # Truncated in place: right key, wrong bytes. Caught by size alone,
    # which is why size is the default — a stat() per file, free at 70,029.
    (docs / name).write_bytes(b"trunc")
    with pytest.raises(ValueError, match="recorded bytes"):
        load_corpus(docs, parquet, manifest=good)

    # Replaced with something the same length: size cannot see it, md5 can.
    (docs / name).write_bytes(b"THE FAKE DOCUMENT")
    assert len(b"THE FAKE DOCUMENT") == len(b"the real document")
    load_corpus(docs, parquet, manifest=good)  # size check passes
    with pytest.raises(ValueError, match="md5 mismatch"):
        load_corpus(docs, parquet, manifest=good, verify="md5")

    # And the escape hatch for a manifest without those columns.
    load_corpus(docs, parquet, manifest=good, verify="keys")

    with pytest.raises(ValueError, match="verify must be"):
        load_corpus(docs, parquet, manifest=good, verify="checksum")
