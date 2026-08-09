"""Regression tests for the Sarvam benchmark sample draw.

Each test here corresponds to a failure observed on a real staging run on
2026-08-09. Every one produced a sample that looked fine in the selection log
and was wrong in the manifest, which is why the manifest is the artifact worth
asserting on.
"""

from __future__ import annotations

import json

import pytest

from janasunani.evaluation.sarvam_sample_builder import (
    allocate,
    build_manifest,
    draw_tickets,
    interleave,
    is_ambiguous_key,
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
