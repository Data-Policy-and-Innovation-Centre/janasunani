"""Regression tests for the Sarvam benchmark sample draw.

Each test here corresponds to a failure observed on a real staging run on
2026-08-09. Every one produced a sample that looked fine in the selection log
and was wrong in the manifest, which is why the manifest is the artifact worth
asserting on.
"""

from __future__ import annotations

import json
import sys

import pytest

from janasunani.evaluation.sarvam_sample_builder import (
    PageCountBackendUnavailable,
    allocate,
    build_manifest,
    build_sample,
    draw_tickets,
    interleave,
    is_ambiguous_key,
    page_count,
    prepare_out_dir,
    select_within_caps,
    write_manifest,
)


# The observed Sambalpur/2024 shape: heavily concentrated, long tail.
SLICE_COUNTS = {
    "Social Welfare": 10485,
    "Miscellaneous": 8253,
    "Housing": 7361,
    "Infrastructure": 3993,
    "General": 3623,
    "COVID-19": 3,
    "Tourism": 1,
}


def test_allocation_gives_every_category_its_floor():
    alloc = allocate(SLICE_COUNTS, budget=220, floor=3)
    for category, available in SLICE_COUNTS.items():
        assert alloc[category] >= min(3, available)


def test_allocation_never_exceeds_what_a_category_actually_has():
    alloc = allocate(SLICE_COUNTS, budget=220, floor=8)
    assert alloc["Tourism"] == 1
    assert alloc["COVID-19"] == 3


def test_allocation_budgets_documents_not_pages():
    """A page-sized budget starves the tail; a document-sized one does not.

    The first real run passed ``target_pages * 2`` here and selected 220
    documents from 2 categories. Budgeting the document cap spreads the draw.
    """
    starved = allocate(SLICE_COUNTS, budget=600, floor=3)
    correct = allocate(SLICE_COUNTS, budget=220, floor=3)
    # With the larger budget the head takes far more of the draw.
    assert starved["Social Welfare"] > correct["Social Welfare"] * 2
    # The correct allocation leaves room for every category inside the cap.
    assert sum(correct.values()) <= 220 + len(SLICE_COUNTS)


def test_interleave_puts_one_of_each_category_before_repeating():
    chosen = (
        [{"gold_category": "Social Welfare", "file": f"sw{i}"} for i in range(3)]
        + [{"gold_category": "Housing", "file": f"h{i}"} for i in range(3)]
        + [{"gold_category": "Tourism", "file": "t0"}]
    )
    order = [d["gold_category"] for d in interleave(chosen, SLICE_COUNTS)]
    assert order[:3] == ["Social Welfare", "Housing", "Tourism"]


def test_interleave_preserves_coverage_when_the_budget_stops_early():
    """Truncating an interleaved list keeps every category represented.

    The failure this guards: 301 pages staged, 3 of 31 categories present,
    because the list was in category order when the page budget ran out.
    """
    chosen = [
        {"gold_category": c, "file": f"{c}-{i}"}
        for c in ("Social Welfare", "Miscellaneous", "Housing", "COVID-19")
        for i in range(5)
    ]
    truncated = interleave(chosen, SLICE_COUNTS)[:4]
    assert len({d["gold_category"] for d in truncated}) == 4

    in_category_order = chosen[:4]
    assert len({d["gold_category"] for d in in_category_order}) == 1


def test_page_cap_per_category_bounds_one_class():
    documents = [{"gold_category": "COVID-19", "file": f"c{i}"} for i in range(10)]
    pages = {f"c{i}": 5 for i in range(10)}
    taken = select_within_caps(
        documents,
        page_counts=pages,
        target_pages=300,
        max_pages_per_category=25,
        max_pages_per_document=8,
    )
    assert sum(d["pages"] for d in taken) <= 25


def test_a_single_long_document_cannot_fill_a_category():
    """The 22-page scan that took a whole category cell.

    COVID-19 held 3 tickets in a 36,909-ticket slice and received 22 of its 25
    allowed pages from one document, so the class result was really one scan.
    """
    documents = [
        {"gold_category": "COVID-19", "file": "long"},
        {"gold_category": "COVID-19", "file": "short-a"},
        {"gold_category": "COVID-19", "file": "short-b"},
    ]
    pages = {"long": 22, "short-a": 3, "short-b": 4}
    taken = select_within_caps(
        documents,
        page_counts=pages,
        target_pages=300,
        max_pages_per_category=25,
        max_pages_per_document=8,
    )
    assert [d["file"] for d in taken] == ["short-a", "short-b"]
    assert all(d["pages"] <= 8 for d in taken)


def test_selection_stops_at_the_page_budget():
    documents = [{"gold_category": f"c{i}", "file": f"f{i}"} for i in range(50)]
    pages = {f"f{i}": 4 for i in range(50)}
    taken = select_within_caps(
        documents,
        page_counts=pages,
        target_pages=20,
        max_pages_per_category=25,
        max_pages_per_document=8,
    )
    assert sum(d["pages"] for d in taken) <= 20 + 4


def test_keys_with_a_path_separator_are_rejected():
    """``AN063/E/2021/00001_complaint_….pdf`` would join to ticket ``00001``."""
    assert is_ambiguous_key("AN063/E/2021/00001_complaint_20250918.pdf")
    assert not is_ambiguous_key("CMO202100019_complaint_20250915.pdf")


def test_the_draw_is_reproducible_under_a_seed():
    rows = [(f"T{i:04d}", "Social Welfare") for i in range(200)]
    first, counts_a = draw_tickets(rows, seed=20260809)
    second, counts_b = draw_tickets(rows, seed=20260809)
    other, _ = draw_tickets(rows, seed=1)
    assert first == second
    assert counts_a == counts_b == {"Social Welfare": 200}
    assert first["Social Welfare"] != other["Social Welfare"]


def test_rows_without_a_category_are_dropped():
    rows = [("T1", "Housing"), ("T2", ""), ("T3", "Housing")]
    by_category, counts = draw_tickets(rows, seed=1)
    assert counts == {"Housing": 2}


def test_manifest_records_what_a_reviewer_needs_to_reproduce_the_draw():
    documents = [
        {"ticket": "T1", "gold_category": "Housing", "s3_key": "T1_a.pdf", "file": "T1_a.pdf", "pages": 4},
        {"ticket": "T2", "gold_category": "Social Welfare", "s3_key": "T2_a.pdf", "file": "T2_a.pdf", "pages": 6},
    ]
    manifest = build_manifest(
        documents,
        slice_label="Sambalpur/2024",
        seed=20260809,
        target_pages=300,
        floor=3,
        max_pages_per_category=25,
        max_pages_per_document=8,
        restore_expiry="Mon, 31 Aug 2026 00:00:00 GMT",
    )
    assert manifest["pages_staged"] == 10
    assert manifest["tickets"] == 2
    assert manifest["categories"] == 2
    assert manifest["seed"] == 20260809
    assert manifest["estimated_cost_rupees_arm_both"] == 15.0
    assert manifest["restore_expiry"].endswith("2026 00:00:00 GMT")
    assert manifest["pages_by_category"]["Social Welfare"] == 6


def test_manifest_round_trips_to_disk(tmp_path):
    manifest = build_manifest(
        [{"ticket": "T1", "gold_category": "Housing", "s3_key": "k", "file": "f", "pages": 2}],
        slice_label="Sambalpur/2024",
        seed=1,
        target_pages=10,
        floor=1,
        max_pages_per_category=5,
        max_pages_per_document=8,
    )
    path = write_manifest(manifest, tmp_path / "nested" / "sample_manifest.json")
    assert json.loads(path.read_text())["pages_staged"] == 2


@pytest.mark.parametrize("pages", [0, -1, 99])
def test_documents_with_impossible_or_oversized_page_counts_are_skipped(pages):
    documents = [{"gold_category": "Housing", "file": "f"}]
    taken = select_within_caps(
        documents,
        page_counts={"f": pages},
        target_pages=300,
        max_pages_per_category=25,
        max_pages_per_document=8,
    )
    assert taken == []


def test_page_count_raises_loudly_when_pdf2image_is_not_installed(tmp_path, monkeypatch):
    """Codex finding on #233: a missing backend must not look like a bad document.

    The console script declares no runtime dependency on `pdf2image` (it
    lives only in the `pipeline-core`/`ocr-deepseek` extras), so a plain
    `uv run janasunani-build-sarvam-sample` hits `ImportError` here. Before
    the fix, the blanket `except Exception` caught it and returned a page
    count of 0 -- indistinguishable from a corrupt PDF -- so every fetched
    document was silently excluded and the CLI wrote an empty manifest and
    exited 0. Setting `sys.modules["pdf2image"] = None` forces the same
    `ImportError` regardless of whether pdf2image happens to be installed in
    the environment running this test.
    """
    monkeypatch.setitem(sys.modules, "pdf2image", None)
    pdf_path = tmp_path / "document.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%fake\n")

    with pytest.raises(PageCountBackendUnavailable):
        page_count(pdf_path)


def test_image_pages_do_not_need_the_pdf_backend(tmp_path, monkeypatch):
    """The backend is only asked for when the file is actually a PDF."""
    monkeypatch.setitem(sys.modules, "pdf2image", None)
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"fake-png-bytes")
    assert page_count(image_path) == 1


# --- the executable path ----------------------------------------------------


class FakeDocumentSource:
    """A DocumentSource with no S3, so the whole path is testable.

    ~90% of the real corpus is in GLACIER, so a test that needed a restore
    would never run. This models the parts that matter: some tickets have no
    document, some keys are ambiguous, and some objects stay archived.
    """

    def __init__(self, *, keys, archived=(), never_ready=(), pages=None):
        self.keys = dict(keys)
        self.archived = set(archived)
        self.never_ready = set(never_ready)
        self.pages = pages or {}
        self.fetched: list[str] = []
        self.restored: list[str] = []

    def find(self, ticket):
        key = self.keys.get(ticket)
        if key is None:
            return None
        return key, "GLACIER" if key in self.archived else "STANDARD"

    def ensure_readable(self, keys):
        for key in keys:
            if key in self.archived:
                self.restored.append(key)
        return {k for k in keys if k in self.never_ready}

    def fetch(self, key, destination):
        self.fetched.append(key)
        destination.write_bytes(b"%PDF-1.4 fake")


def _rows(per_category):
    return [
        (f"T{category[:2]}{index:03d}", category)
        for category, n in per_category.items()
        for index in range(n)
    ]


def _source_for(rows, *, pages=1, **kwargs):
    keys = {ticket: f"{ticket}_complaint.pdf" for ticket, _ in rows}
    return FakeDocumentSource(
        keys=keys,
        pages={f"{ticket}_complaint.pdf": pages for ticket, _ in rows},
        **kwargs,
    )


def test_build_sample_runs_the_whole_path_and_writes_a_manifest(tmp_path):
    """Codex finding on #233: the module composed none of its own helpers.

    The operational draw lived in an uncommitted scratchpad, so the checked-in
    code could not reproduce the sample it described.
    """
    rows = _rows({"Housing": 10, "Social Welfare": 10, "Tourism": 2})
    source = _source_for(rows)

    manifest = build_sample(
        rows,
        source,
        out_dir=tmp_path / "stage",
        slice_label="Sambalpur/2024",
        seed=20260809,
        target_pages=12,
        floor=2,
        max_documents=20,
        max_pages_per_category=5,
        max_pages_per_document=8,
        page_counter=lambda p: 1,
    )

    assert (tmp_path / "stage" / "sample_manifest.json").is_file()
    assert manifest["pages_staged"] > 0
    assert manifest["categories"] >= 2
    assert manifest["slice"] == "Sambalpur/2024"
    # Every staged file is accounted for by the manifest, and vice versa.
    staged = {p.name for p in (tmp_path / "stage").iterdir() if p.suffix != ".json"}
    assert staged == {d["file"] for d in manifest["documents"]}


def test_build_sample_enforces_ambiguous_keys_on_a_real_key(tmp_path):
    """`is_ambiguous_key` was only ever exercised by its own unit test."""
    rows = [("T1", "Housing"), ("T2", "Housing")]
    source = FakeDocumentSource(
        keys={"T1": "AN063/E/2021/00001_complaint.pdf", "T2": "T2_complaint.pdf"}
    )

    manifest = build_sample(
        rows,
        source,
        out_dir=tmp_path / "stage",
        slice_label="Sambalpur/2024",
        seed=1,
        target_pages=10,
        floor=2,
        max_documents=10,
        max_pages_per_category=10,
        max_pages_per_document=8,
        page_counter=lambda p: 1,
    )
    tickets = {d["ticket"] for d in manifest["documents"]}
    assert "T1" not in tickets, "a key that joins to the wrong ticket was staged"
    assert "T2" in tickets


def test_build_sample_skips_objects_that_never_restore(tmp_path):
    rows = [("T1", "Housing"), ("T2", "Housing")]
    source = FakeDocumentSource(
        keys={"T1": "T1_c.pdf", "T2": "T2_c.pdf"},
        archived=["T1_c.pdf", "T2_c.pdf"],
        never_ready=["T1_c.pdf"],
    )

    manifest = build_sample(
        rows,
        source,
        out_dir=tmp_path / "stage",
        slice_label="Sambalpur/2024",
        seed=1,
        target_pages=10,
        floor=2,
        max_documents=10,
        max_pages_per_category=10,
        max_pages_per_document=8,
        page_counter=lambda p: 1,
    )
    assert {d["ticket"] for d in manifest["documents"]} == {"T2"}
    assert "T1_c.pdf" not in source.fetched


def test_build_sample_leaves_no_unaccounted_files_on_disk(tmp_path):
    """A fetched-but-unselected file would let a later run read pages the
    manifest does not account for, and those pages would be billed."""
    rows = _rows({"Housing": 8})
    source = _source_for(rows)

    manifest = build_sample(
        rows,
        source,
        out_dir=tmp_path / "stage",
        slice_label="Sambalpur/2024",
        seed=1,
        target_pages=3,
        floor=1,
        max_documents=8,
        max_pages_per_category=10,
        max_pages_per_document=8,
        page_counter=lambda p: 1,
    )
    on_disk = {p.name for p in (tmp_path / "stage").iterdir() if p.suffix != ".json"}
    assert on_disk == {d["file"] for d in manifest["documents"]}
    assert len(on_disk) <= 3


def test_build_sample_rejects_a_non_empty_out_dir_by_default(tmp_path):
    """Codex finding on #233: a reused --out silently mixed a prior draw's
    files into this one's manifest.

    `sarvam_evaluate.discover_pages` enumerates every supported document
    under the staging directory without consulting `sample_manifest.json`,
    so a leftover file from an earlier run with a different seed or cap
    would be submitted and billed even though the new manifest never
    mentions it.
    """
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "leftover_complaint.pdf").write_bytes(b"stale")

    rows = _rows({"Housing": 4})
    with pytest.raises(FileExistsError):
        build_sample(
            rows,
            _source_for(rows),
            out_dir=stage,
            slice_label="Sambalpur/2024",
            seed=1,
            target_pages=10,
            floor=1,
            max_documents=4,
            max_pages_per_category=10,
            max_pages_per_document=8,
            page_counter=lambda p: 1,
        )
    # The rejected run must not have touched what was already there.
    assert (stage / "leftover_complaint.pdf").is_file()


def test_build_sample_can_clear_a_non_empty_out_dir_on_request(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "leftover_complaint.pdf").write_bytes(b"stale")

    rows = _rows({"Housing": 4})
    manifest = build_sample(
        rows,
        _source_for(rows),
        out_dir=stage,
        slice_label="Sambalpur/2024",
        seed=1,
        target_pages=10,
        floor=1,
        max_documents=4,
        max_pages_per_category=10,
        max_pages_per_document=8,
        page_counter=lambda p: 1,
        clean_out_dir=True,
    )
    assert not (stage / "leftover_complaint.pdf").exists()
    on_disk = {p.name for p in stage.iterdir() if p.suffix != ".json"}
    assert on_disk == {d["file"] for d in manifest["documents"]}


def test_build_sample_raises_loudly_when_the_page_backend_is_missing(tmp_path, monkeypatch):
    """The composed path must fail loudly too, not just the unit under it.

    Without `page_counter` overridden, `build_sample` uses the real
    `page_count`, which needs `pdf2image` for the PDFs `FakeDocumentSource`
    fetches. Before the fix this would have quietly produced an empty
    manifest (every page count 0, every document excluded) and exited
    successfully instead of raising.
    """
    monkeypatch.setitem(sys.modules, "pdf2image", None)
    rows = _rows({"Housing": 4})

    with pytest.raises(PageCountBackendUnavailable):
        build_sample(
            rows,
            _source_for(rows),
            out_dir=tmp_path / "stage",
            slice_label="Sambalpur/2024",
            seed=1,
            target_pages=10,
            floor=1,
            max_documents=4,
            max_pages_per_category=10,
            max_pages_per_document=8,
            # No page_counter override: exercise the real page_count.
        )
    # No manifest was written -- the failure must not look like a completed,
    # merely-empty draw.
    assert not (tmp_path / "stage" / "sample_manifest.json").exists()


def test_prepare_out_dir_accepts_a_missing_or_already_empty_directory(tmp_path):
    fresh = tmp_path / "fresh"
    prepare_out_dir(fresh)
    assert fresh.is_dir()
    # Calling again on the now-empty, now-existing directory must not raise.
    prepare_out_dir(fresh)


def test_the_same_seed_reproduces_the_same_sample(tmp_path):
    rows = _rows({"Housing": 12, "Social Welfare": 12})
    first = build_sample(
        rows, _source_for(rows), out_dir=tmp_path / "a", slice_label="S/2024",
        seed=99, target_pages=8, floor=2, max_documents=12,
        max_pages_per_category=10, max_pages_per_document=8, page_counter=lambda p: 1,
    )
    second = build_sample(
        list(reversed(rows)), _source_for(rows), out_dir=tmp_path / "b", slice_label="S/2024",
        seed=99, target_pages=8, floor=2, max_documents=12,
        max_pages_per_category=10, max_pages_per_document=8, page_counter=lambda p: 1,
    )
    assert [d["ticket"] for d in first["documents"]] == [
        d["ticket"] for d in second["documents"]
    ]


# --- the two arithmetic guarantees -----------------------------------------


@pytest.mark.parametrize(
    "counts, budget, floor",
    [
        (SLICE_COUNTS, 220, 3),
        ({"A": 100, "B": 100, "C": 100}, 5, 1),
        ({"A": 10, "B": 10, "C": 10, "D": 10}, 7, 1),
        (SLICE_COUNTS, 31, 3),
        ({"A": 1, "B": 1}, 50, 3),
    ],
)
def test_allocation_never_exceeds_the_budget(counts, budget, floor):
    """Codex finding on #233: independent rounding overshot the budget.

    `allocate(SLICE_COUNTS, budget=220, floor=3)` returned 221, and three
    equal categories on a budget of 5 returned 6. A function whose purpose is
    to respect a budget must respect it.
    """
    alloc = allocate(counts, budget=budget, floor=floor)
    total = sum(alloc.values())
    feasible = min(budget, sum(counts.values()))
    assert total <= budget, f"allocated {total} against a budget of {budget}"
    # And it should not leave the budget unspent when capacity exists.
    assert total == feasible or total >= min(feasible, sum(min(floor, c) for c in counts.values()))


def test_allocation_rejects_floors_that_exceed_the_budget():
    """Codex finding on #233: the floor stage was never checked against budget.

    Three populated categories with `budget=2, floor=1` used to allocate 3
    documents (the floor alone overshoots), which `choose_documents` then
    resolved on its own by truncating in largest-category order -- silently
    dropping whichever categories the floor was meant to protect. Infeasible
    floor/budget combinations must be rejected, not quietly resolved twice in
    two different places.
    """
    with pytest.raises(ValueError, match="floor"):
        allocate({"A": 5, "B": 5, "C": 5}, budget=2, floor=1)


def test_allocation_accepts_a_floor_that_exactly_fits_the_budget():
    """The boundary case must not be rejected along with the infeasible one."""
    alloc = allocate({"A": 5, "B": 5}, budget=2, floor=1)
    assert alloc == {"A": 1, "B": 1}


def test_choose_documents_propagates_the_infeasible_allocation_error():
    """The composed path must not swallow the error and truncate instead."""
    from janasunani.evaluation.sarvam_sample_builder import choose_documents

    rows = _rows({"Housing": 5, "Social Welfare": 5, "Tourism": 5})
    by_category, counts = draw_tickets(rows, seed=1)
    source = _source_for(rows)

    with pytest.raises(ValueError, match="floor"):
        choose_documents(by_category, counts, source, max_documents=2, floor=1)


def test_the_draw_does_not_depend_on_input_row_order():
    """The manifest claims reproducibility from the seed; row order broke it."""
    rows = [("T1", "A"), ("T2", "A"), ("T3", "A"), ("U1", "B"), ("U2", "B")]
    forward, counts_a = draw_tickets(rows, seed=7)
    reversed_draw, counts_b = draw_tickets(list(reversed(rows)), seed=7)
    assert forward == reversed_draw
    assert counts_a == counts_b
    assert list(forward) == sorted(forward), "category order must be canonical too"
