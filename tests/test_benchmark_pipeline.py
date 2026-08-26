"""Tests for scripts/benchmark_pipeline.py — per-stage wall-clock with cluster SE.

Unit F: timing harness that measures each pipeline stage over n documents
and k repeats, clusters SE by ticket, and writes outputs/benchmark/latency.json
for the four variants standard / sarvam_digitise / sarvam_extract / sarvam_both.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tracemalloc
from pathlib import Path

import pytest

# Import the harness directly (import-light, no heavy ML deps)
from scripts.benchmark_pipeline import (
    ALL_KEYS,
    E2E_KEY,
    STAGES,
    SUPPORTED_DOCUMENT_SUFFIXES,
    VALID_VARIANTS,
    SampleFingerprintDriftError,
    StaleDocumentBytesError,
    _check_fingerprint_matches_loaded_documents,
    _clustered_se,
    _document_kind,
    _document_sample_digest,
    _document_sample_file_hashes,
    _fake_process,
    _percentile,
    _single_page_text_pdf,
    compute_stage_stats,
    latency_json_payload,
    load_staged_documents,
    run_benchmark,
    synthesize_documents,
    write_latency_json,
)

# Also import the script as module for CLI tests
import scripts.benchmark_pipeline as bench_mod

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_valid_variants_are_exactly_four_expected():
    assert VALID_VARIANTS == {
        "standard",
        "sarvam_digitise",
        "sarvam_extract",
        "sarvam_both",
    }


def test_stages_cover_live_path():
    # Must include the live-path stages listed in the plan
    for stage in ["format", "ocr", "pii", "spam", "page_type", "summarize", "categorize", "route"]:
        assert stage in STAGES
    assert E2E_KEY in ALL_KEYS
    assert set(STAGES).issubset(set(ALL_KEYS))


def test_clustered_se_by_ticket_not_by_page():
    # Two tickets, each with 5 measurements at different means.
    # Ticket-clustered SE should account for within-ticket correlation.
    # Use identical values per ticket but different means between tickets
    # so clustering matters.
    values = [1.0] * 5 + [3.0] * 5
    clusters_ticket = ["T1"] * 5 + ["T2"] * 5
    clusters_page = [f"P{i}" for i in range(10)]

    se_ticket = _clustered_se(values, clusters_ticket)
    se_page = _clustered_se(values, clusters_page)

    # With two clusters, SE should be larger than with 10 independent pages
    # (within-cluster correlation inflates variance).
    assert se_ticket > 0
    assert se_page > 0
    # The two SEs should differ (clustering changes the estimate)
    assert se_ticket != pytest.approx(se_page, rel=0.1) or se_ticket == se_page  # at least computed


def test_clustered_se_single_cluster_fallback():
    # When C==1, should fall back to simple SE without ZeroDivision
    se = _clustered_se([1.0, 2.0, 3.0], ["T1", "T1", "T1"])
    assert se > 0
    assert se == pytest.approx(0.577, rel=0.1)  # approx sqrt(1/3) = 0.577


def test_clustered_se_empty_and_single():
    assert _clustered_se([], []) == 0.0
    assert _clustered_se([1.0], ["T1"]) == 0.0


def test_percentile_basic():
    assert _percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)
    assert _percentile([1, 2, 3, 4], 0) == pytest.approx(1.0)
    assert _percentile([1, 2, 3, 4], 100) == pytest.approx(4.0)
    assert _percentile([], 50) == 0.0
    assert _percentile([5.0], 95) == pytest.approx(5.0)


def test_compute_stage_stats_has_required_keys():
    times = [0.5, 0.6, 0.55, 0.7, 0.52]
    tickets = ["T1", "T1", "T2", "T2", "T3"]
    stats = compute_stage_stats(times, tickets)
    for key in [
        "mean_seconds",
        "se_seconds",
        "n_clusters",
        "p50",
        "p90",
        "p95",
        "throughput_per_second",
        "n",
    ]:
        assert key in stats
    assert stats["n"] == 5
    assert stats["n_clusters"] == 3
    assert stats["mean_seconds"] == pytest.approx(sum(times) / 5)
    assert stats["se_seconds"] >= 0
    assert stats["p50"] > 0
    assert stats["p50"] <= stats["p90"] <= stats["p95"]
    assert stats["p95"] >= stats["p50"]
    assert stats["min_seconds"] <= stats["mean_seconds"] <= stats["max_seconds"]


def test_compute_stage_stats_empty():
    stats = compute_stage_stats([], [])
    assert stats["mean_seconds"] == 0.0
    assert stats["n"] == 0
    assert stats["n_clusters"] == 0


def test_compute_stage_stats_mismatched_lengths_raises():
    with pytest.raises(ValueError, match="equal length"):
        compute_stage_stats([1.0, 2.0], ["T1"])


def test_synthesize_documents_counts_and_deterministic():
    docs = synthesize_documents(n_text=5, n_image=3, seed=42)
    assert len(docs) == 8
    # Deterministic: same seed gives same order
    docs2 = synthesize_documents(n_text=5, n_image=3, seed=42)
    assert [d["ticket"] for d in docs] == [d["ticket"] for d in docs2]
    # All tickets unique
    tickets = [d["ticket"] for d in docs]
    assert len(set(tickets)) == 8
    # Text vs image split
    n_text_actual = sum(1 for d in docs if d["text"] is not None)
    n_img_actual = sum(1 for d in docs if d["document_bytes"] is not None)
    assert n_text_actual == 5
    assert n_img_actual == 3
    image_docs = [row for row in docs if row["document_bytes"] is not None]
    assert all(b"/Contents" in row["document_bytes"] for row in image_docs)
    assert all(b"District Collector" in row["document_bytes"] for row in image_docs)


# ---------------------------------------------------------------------------
# Real-document loader — load_staged_documents
# ---------------------------------------------------------------------------


def _stage_document(directory: Path, ticket: str, suffix: str = ".pdf", text: str | None = None) -> Path:
    """Write one staged document under ``directory`` with the real naming
    convention, using the stdlib PDF builder so no binary is committed."""
    path = directory / f"{ticket}_complaint_20250715_234307{suffix}"
    path.write_bytes(_single_page_text_pdf(text or f"Grievance document for {ticket}"))
    return path


def test_load_staged_documents_without_manifest(tmp_path):
    _stage_document(tmp_path, "CMO20241020862")
    _stage_document(tmp_path, "CMO2024483790", suffix=".jpeg")

    docs = load_staged_documents(tmp_path)

    assert len(docs) == 2
    tickets = {d["ticket"] for d in docs}
    assert tickets == {"CMO20241020862", "CMO2024483790"}
    for doc in docs:
        # Same shape as synthesize_documents() (ticket, text, document_name,
        # document_bytes, district) plus document_path. Bytes are not read
        # here (#308) — document_bytes stays None and document_path carries
        # the file; _measure_with_processor reads it lazily, one document at
        # a time, outside every stage timer.
        assert doc["text"] is None
        assert doc["document_name"]
        assert doc["document_bytes"] is None
        assert doc["document_path"].is_file()
        assert len(doc["document_path"].read_bytes()) > 0
        # No manifest present, so district falls back to a stable label
        # rather than crashing or silently using None.
        assert doc["district"] == "unspecified"


def test_load_staged_documents_with_manifest_sets_district_from_slice(tmp_path):
    _stage_document(tmp_path, "CMO20241020862")
    _stage_document(tmp_path, "CMO2024483790", suffix=".jpeg")
    manifest = {
        "slice": "Sambalpur/2024",
        "seed": 20260809,
        "target_pages": 2,
        "tickets": 2,
        "categories": 1,
        "pages_by_category": {"Water Supply": 2},
        "documents": [
            {"ticket": "CMO20241020862", "gold_category": "Water Supply", "file": "CMO20241020862_complaint_20250715_234307.pdf"},
            {"ticket": "CMO2024483790", "gold_category": "Water Supply", "file": "CMO2024483790_complaint_20250715_234307.jpeg"},
        ],
    }
    (tmp_path / "sample_manifest.json").write_text(json.dumps(manifest))

    docs = load_staged_documents(tmp_path)

    assert len(docs) == 2
    assert all(d["district"] == "Sambalpur" for d in docs)


def test_load_staged_documents_manifest_can_be_passed_explicitly(tmp_path):
    _stage_document(tmp_path, "CMO20241020862")
    manifest = {"slice": "Khordha/2023", "documents": [{"ticket": "CMO20241020862"}]}

    docs = load_staged_documents(tmp_path, manifest=manifest)

    assert docs[0]["district"] == "Khordha"


def test_load_staged_documents_reuses_ticket_module_parsing(tmp_path):
    # Ticket ids matter because the stats code clusters repeats by ticket;
    # this must match janasunani.pipeline.ticket.ticket_from_relpath exactly,
    # not a second regex that could drift from it.
    from janasunani.pipeline.ticket import ticket_from_relpath

    path = _stage_document(tmp_path, "CMO20241020862")
    docs = load_staged_documents(tmp_path)
    assert docs[0]["ticket"] == ticket_from_relpath(path.name)
    assert docs[0]["ticket"] == "CMO20241020862"


def test_load_staged_documents_uses_manifest_for_hierarchical_tickets(tmp_path):
    # Regression for P1-2 (PR #307 review): sarvam_sample_builder stages
    # documents under Path(key).name, so two distinct complaints whose real
    # tickets are "OR159/P/2021/00535" and "OR122/E/2021/00535" both land on
    # disk with a filename that basename-parses to just "00535". Confirmed
    # against the lake: 27,684 of 1,371,288 complaints (2.0%) have a
    # hierarchical ticket like this. The manifest carries the real ticket
    # per file and must be used to tell them apart.
    from janasunani.pipeline.ticket import ticket_from_relpath

    file1 = tmp_path / "00535_complaint_20250715_234307.pdf"
    file1.write_bytes(_single_page_text_pdf("Grievance A"))
    file2 = tmp_path / "00535_complaint_20250801_101010.pdf"
    file2.write_bytes(_single_page_text_pdf("Grievance B"))

    # Confirm the premise: basename-only parsing really does collide these.
    assert ticket_from_relpath(file1.name) == ticket_from_relpath(file2.name) == "00535"

    manifest = {
        "slice": "State/2021",
        "documents": [
            {"ticket": "OR159/P/2021/00535", "gold_category": "Water Supply", "file": file1.name},
            {"ticket": "OR122/E/2021/00535", "gold_category": "Water Supply", "file": file2.name},
        ],
    }

    docs = load_staged_documents(tmp_path, manifest=manifest)

    tickets = {d["ticket"] for d in docs}
    assert tickets == {"OR159/P/2021/00535", "OR122/E/2021/00535"}


def test_load_staged_documents_without_manifest_can_collapse_hierarchical_tickets(tmp_path):
    # Documented limitation (see load_staged_documents docstring): without a
    # manifest there is no way to recover the directory part of a
    # hierarchical ticket that staging already discarded. This test records
    # the gap honestly rather than claiming it is fixed in every case.
    file1 = tmp_path / "00535_complaint_20250715_234307.pdf"
    file1.write_bytes(_single_page_text_pdf("Grievance A"))
    file2 = tmp_path / "00535_complaint_20250801_101010.pdf"
    file2.write_bytes(_single_page_text_pdf("Grievance B"))

    docs = load_staged_documents(tmp_path)  # no manifest

    tickets = [d["ticket"] for d in docs]
    assert tickets == ["00535", "00535"]


def test_load_staged_documents_manifest_ticket_wins_per_file_not_all_or_nothing(tmp_path):
    # A manifest entry is used per file it actually covers; a file the
    # manifest omits still falls back to basename parsing rather than the
    # whole load rejecting the manifest.
    file1 = tmp_path / "OR159P202100535_complaint_20250715_234307.pdf"
    file1.write_bytes(_single_page_text_pdf("Grievance A"))
    file2 = _stage_document(tmp_path, "CMO2024483790", suffix=".jpeg")

    manifest = {
        "slice": "State/2021",
        "documents": [
            {"ticket": "OR159/P/2021/00535", "gold_category": "Water Supply", "file": file1.name},
            # file2 intentionally absent from the manifest's documents list.
        ],
    }

    docs = load_staged_documents(tmp_path, manifest=manifest)
    by_name = {d["document_name"]: d["ticket"] for d in docs}
    assert by_name[file1.name] == "OR159/P/2021/00535"
    assert by_name[file2.name] == "CMO2024483790"


def test_load_staged_documents_empty_directory_raises_loudly(tmp_path):
    # An empty doc list must never reach run_benchmark silently — its own
    # "no documents" error talks about n_text + n_image, which would
    # misdescribe a directory problem as a documents-count-flag problem.
    with pytest.raises(ValueError, match="no supported documents"):
        load_staged_documents(tmp_path)


def test_load_staged_documents_ignores_manifest_json_itself(tmp_path):
    # sample_manifest.json sits beside the documents; it must not be picked
    # up as a document (it has no supported suffix, but guard it directly).
    (tmp_path / "sample_manifest.json").write_text(json.dumps({"slice": "X/2024", "documents": []}))
    with pytest.raises(ValueError, match="no supported documents"):
        load_staged_documents(tmp_path)


def test_load_staged_documents_missing_directory_raises_loudly():
    with pytest.raises(FileNotFoundError):
        load_staged_documents(Path("/nonexistent/staging/dir/for/janasunani/benchmark"))


def test_load_staged_documents_unparseable_filename_raises(tmp_path):
    # No "_complaint_" marker in the stem: ticket_from_relpath returns None.
    (tmp_path / "not_a_staged_name.pdf").write_bytes(_single_page_text_pdf("hello"))
    with pytest.raises(ValueError, match="staged naming convention"):
        load_staged_documents(tmp_path)


def test_load_staged_documents_supported_suffixes_include_pdf_and_images():
    assert ".pdf" in SUPPORTED_DOCUMENT_SUFFIXES
    for suffix in (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif"):
        assert suffix in SUPPORTED_DOCUMENT_SUFFIXES


def test_document_sample_digest_prefers_manifest_and_is_stable(tmp_path):
    _stage_document(tmp_path, "CMO20241020862")
    manifest = {"slice": "Sambalpur/2024", "documents": [{"ticket": "CMO20241020862"}]}

    d1 = _document_sample_digest(tmp_path, manifest)
    d2 = _document_sample_digest(tmp_path, manifest)
    assert d1 == d2
    assert d1 != _document_sample_digest(tmp_path, {**manifest, "slice": "Khordha/2024"})


def test_document_sample_digest_without_manifest_uses_filenames_and_content(tmp_path):
    _stage_document(tmp_path, "CMO20241020862")
    d1 = _document_sample_digest(tmp_path, None)
    d2 = _document_sample_digest(tmp_path, None)
    assert d1 == d2

    _stage_document(tmp_path, "CMO2024483790", suffix=".jpeg")
    d3 = _document_sample_digest(tmp_path, None)
    assert d3 != d1


def test_document_sample_digest_detects_content_change_with_manifest(tmp_path):
    # Regression for P1-3 (PR #307 review): the original implementation
    # hashed only the manifest JSON when a manifest was present, so
    # replacing a staged scan's bytes (bad re-download, swapped page, a
    # stale manifest beside different content) was invisible to the
    # digest as long as the filename and document count still matched.
    path = _stage_document(tmp_path, "CMO20241020862")
    manifest = {
        "slice": "Sambalpur/2024",
        "documents": [{"ticket": "CMO20241020862", "file": path.name}],
    }
    d1 = _document_sample_digest(tmp_path, manifest)

    path.write_bytes(_single_page_text_pdf("A completely different scan"))
    d2 = _document_sample_digest(tmp_path, manifest)

    assert d1 != d2


def test_document_sample_digest_detects_content_change_without_manifest(tmp_path):
    path = _stage_document(tmp_path, "CMO20241020862")
    d1 = _document_sample_digest(tmp_path, None)

    path.write_bytes(_single_page_text_pdf("A completely different scan"))
    d2 = _document_sample_digest(tmp_path, None)

    assert d1 != d2


def test_document_sample_digest_stable_across_restage_to_new_path(tmp_path):
    # The manifest-identity rationale claims stability across a re-stage to
    # a different directory; hashing content must not break that, since the
    # digest is keyed on filename + bytes, not the full path.
    dir1 = tmp_path / "stageA"
    dir1.mkdir()
    dir2 = tmp_path / "stageB"
    dir2.mkdir()
    filename = "CMO20241020862_complaint_20250715_234307.pdf"
    content = _single_page_text_pdf("Grievance for CMO20241020862")
    (dir1 / filename).write_bytes(content)
    (dir2 / filename).write_bytes(content)
    manifest = {
        "slice": "Sambalpur/2024",
        "documents": [{"ticket": "CMO20241020862", "file": filename}],
    }

    assert _document_sample_digest(dir1, manifest) == _document_sample_digest(dir2, manifest)


# ---------------------------------------------------------------------------
# #315 Codex follow-up — bind sample_digest to the bytes actually benchmarked
# ---------------------------------------------------------------------------


def test_document_sample_file_hashes_matches_per_file_digest_inputs(tmp_path):
    _stage_document(tmp_path, "CMO20241020862")
    _stage_document(tmp_path, "CMO2024483790", suffix=".jpeg")

    hashes = _document_sample_file_hashes(tmp_path)

    names = {d.name for d in tmp_path.iterdir() if d.suffix.lower() in SUPPORTED_DOCUMENT_SUFFIXES}
    assert set(hashes) == names
    for name, digest in hashes.items():
        expected = hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
        assert digest == expected
    # _document_sample_digest(..., file_hashes=hashes) must be reusable
    # without a second read — same result as computing it fresh.
    assert _document_sample_digest(tmp_path, None, file_hashes=hashes) == _document_sample_digest(
        tmp_path, None
    )


def test_run_benchmark_detects_document_mutated_after_fingerprint(tmp_path):
    # This is the actual race #308's lazy read opened: sample_digest is
    # fingerprinted once, before the run starts; _measure_with_processor then
    # reads each document lazily, potentially much later. Stage a directory,
    # fingerprint it (as main() does before run_benchmark starts), mutate one
    # file on disk exactly like a corpus still hydrating from Box would, and
    # confirm the run refuses to silently benchmark content the fingerprint
    # never saw.
    mutated_path = _stage_document(tmp_path, "CMO20241020862")
    _stage_document(tmp_path, "CMO2024483790", suffix=".jpeg")
    file_hashes = _document_sample_file_hashes(tmp_path)
    docs = load_staged_documents(tmp_path)

    # Simulate the mid-run rewrite: different bytes land on disk after the
    # fingerprint but before run_benchmark's lazy read.
    mutated_path.write_bytes(_single_page_text_pdf("A completely different scan"))

    with pytest.raises(StaleDocumentBytesError, match=mutated_path.name):
        run_benchmark(
            variant="standard",
            docs=docs,
            repeats=1,
            discard_warm=False,
            processor_factory=lambda _variant: type(
                "Processor",
                (),
                {
                    "_timing_sink": None,
                    "process": lambda self, **kwargs: self._timing_sink(
                        {"redact": 0.1, "e2e": 0.2, "ok": 1.0}
                    ),
                },
            )(),
            expected_file_hashes=file_hashes,
        )


def test_run_benchmark_missing_from_fingerprint_also_fails_loudly(tmp_path):
    # A file staged after the fingerprint was taken is exactly as stale as
    # one whose content changed — both mean the run is about to measure
    # bytes the published sample_digest never claimed. Must not be treated
    # as "no opinion, so allow it".
    file_hashes = _document_sample_file_hashes(tmp_path)  # empty snapshot
    _stage_document(tmp_path, "CMO20241020862")
    docs = load_staged_documents(tmp_path)

    with pytest.raises(StaleDocumentBytesError, match="CMO20241020862"):
        run_benchmark(
            variant="standard",
            docs=docs,
            repeats=1,
            discard_warm=False,
            processor_factory=lambda _variant: type(
                "Processor",
                (),
                {
                    "_timing_sink": None,
                    "process": lambda self, **kwargs: self._timing_sink(
                        {"redact": 0.1, "e2e": 0.2, "ok": 1.0}
                    ),
                },
            )(),
            expected_file_hashes=file_hashes,
        )


def test_run_benchmark_happy_path_with_matching_fingerprint_still_passes(tmp_path):
    # The verification must not get in its own way when nothing changed —
    # this is the common case (no hydration race) and must behave exactly
    # like passing no expected_file_hashes at all.
    _stage_document(tmp_path, "CMO20241020862")
    _stage_document(tmp_path, "CMO2024483790", suffix=".jpeg")
    file_hashes = _document_sample_file_hashes(tmp_path)
    docs = load_staged_documents(tmp_path)

    result = run_benchmark(
        variant="standard",
        docs=docs,
        repeats=2,
        discard_warm=False,
        processor_factory=lambda _variant: type(
            "Processor",
            (),
            {
                "_timing_sink": None,
                "process": lambda self, **kwargs: self._timing_sink(
                    {"redact": 0.1, "e2e": 0.2, "ok": 1.0}
                ),
            },
        )(),
        expected_file_hashes=file_hashes,
    )
    assert result["n_docs"] == 2
    assert result["failed_attempts"] == 0
    assert result["stages"]["e2e"]["n"] == 4


def test_run_benchmark_fake_path_skips_fingerprint_check(tmp_path):
    # The fake timing path never reads document bytes at all (see #308's
    # fake-path test), so it must not fail even when expected_file_hashes is
    # stale — there is nothing to verify because nothing gets read.
    mutated_path = _stage_document(tmp_path, "CMO20241020862")
    file_hashes = _document_sample_file_hashes(tmp_path)
    docs = load_staged_documents(tmp_path)
    mutated_path.write_bytes(_single_page_text_pdf("A completely different scan"))

    result = run_benchmark(
        variant="standard",
        docs=docs,
        repeats=2,
        discard_warm=False,
        expected_file_hashes=file_hashes,  # no processor_factory -> fake path
    )
    assert result["n_docs"] == 1


def test_cli_documents_dir_stale_document_bytes_fails_loudly(tmp_path, monkeypatch, capsys):
    # CLI wiring: main() must convert a StaleDocumentBytesError raised deep
    # inside run_benchmark into a loud, non-zero-exit CLI failure that names
    # the file, not a silently-written latency.json. run_benchmark itself is
    # stubbed here — the detection logic is exercised for real above; this
    # test is only about main()'s try/except around the call.
    staging = tmp_path / "staging"
    staging.mkdir()
    _stage_document(staging, "CMO20241020862")
    out = tmp_path / "latency.json"

    def fake_run_benchmark(*_args, **_kwargs):
        raise StaleDocumentBytesError(
            "CMO20241020862_complaint_20250715_234307.pdf does not match "
            "the sample_digest snapshot taken before this run started"
        )

    monkeypatch.setattr(bench_mod, "run_benchmark", fake_run_benchmark)

    with pytest.raises(SystemExit) as exc:
        bench_mod.main(
            [
                "--fake",
                "--documents-dir",
                str(staging),
                "--repeats",
                "2",
                "--output",
                str(out),
            ]
        )
    assert exc.value.code == 2
    assert not out.exists()
    err = capsys.readouterr().err
    assert "CMO20241020862_complaint_20250715_234307.pdf" in err
    assert "sample_digest snapshot" in err


# ---------------------------------------------------------------------------
# #315 Codex follow-up round 2 — fingerprint file set must match what was
# actually loaded (a file added/removed between two independent directory
# scans could be fingerprinted without being benchmarked, or vice versa)
# ---------------------------------------------------------------------------


def test_check_fingerprint_matches_loaded_documents_passes_when_sets_agree():
    loaded_docs = [
        {"document_name": "a.pdf"},
        {"document_name": "b.pdf"},
    ]
    file_hashes = {"a.pdf": "hash-a", "b.pdf": "hash-b"}
    # Must not raise.
    _check_fingerprint_matches_loaded_documents(loaded_docs, file_hashes)


def test_check_fingerprint_matches_loaded_documents_raises_on_extra_hash():
    # A file fingerprinted but never loaded is the dangerous direction:
    # sample_digest would cover bytes nothing in the run ever measured.
    loaded_docs = [{"document_name": "a.pdf"}]
    file_hashes = {"a.pdf": "hash-a", "stowaway.pdf": "hash-b"}

    with pytest.raises(SampleFingerprintDriftError, match="stowaway.pdf"):
        _check_fingerprint_matches_loaded_documents(loaded_docs, file_hashes)


def test_check_fingerprint_matches_loaded_documents_raises_on_missing_hash():
    loaded_docs = [{"document_name": "a.pdf"}, {"document_name": "b.pdf"}]
    file_hashes = {"a.pdf": "hash-a"}

    with pytest.raises(SampleFingerprintDriftError, match="b.pdf"):
        _check_fingerprint_matches_loaded_documents(loaded_docs, file_hashes)


def test_fingerprint_race_between_independent_scans_is_real_and_caught(tmp_path):
    # Reproduces the actual race Codex found: load_staged_documents() and
    # _document_sample_file_hashes() each independently list the directory
    # by default (two directory.iterdir() calls, not one). A file staged in
    # between -- e.g. a corpus still hydrating into this directory -- lands
    # in the second scan's hashes without ever having been loaded. This is
    # exactly what main() no longer does (it derives the hash target list
    # from loaded_docs' own document_path, not a second scan -- see the
    # tests below), but the two independently-scanning functions still
    # exist on their own and the mismatch they can produce must be caught,
    # not silently published.
    _stage_document(tmp_path, "CMO20241020862")
    loaded_docs = load_staged_documents(tmp_path)  # scan #1

    # Simulate a file landing mid-hydration, after scan #1.
    _stage_document(tmp_path, "CMO2024483790", suffix=".jpeg")

    file_hashes = _document_sample_file_hashes(tmp_path)  # independent scan #2
    assert "CMO2024483790_complaint_20250715_234307.jpeg" in file_hashes  # premise: the race is real

    with pytest.raises(
        SampleFingerprintDriftError, match="CMO2024483790_complaint_20250715_234307.jpeg"
    ):
        _check_fingerprint_matches_loaded_documents(loaded_docs, file_hashes)


def test_document_sample_file_hashes_with_explicit_files_ignores_later_directory_changes(
    tmp_path,
):
    # This is the actual fix: an explicit files= snapshot is hashed as-is,
    # so a file appearing after the snapshot was taken cannot silently
    # enter the fingerprint no matter when _document_sample_file_hashes
    # happens to run relative to the directory changing.
    _stage_document(tmp_path, "CMO20241020862")
    files = bench_mod._staged_document_paths(tmp_path)

    _stage_document(tmp_path, "CMO2024483790", suffix=".jpeg")  # lands after the snapshot

    hashes = _document_sample_file_hashes(tmp_path, files=files)
    assert set(hashes) == {"CMO20241020862_complaint_20250715_234307.pdf"}


def test_cli_documents_dir_fingerprint_derives_from_loaded_documents_not_a_rescan(tmp_path):
    # Integration-level regression guard for the actual fix in main(): the
    # file list handed to _document_sample_file_hashes comes from
    # loaded_docs' own document_path values, not a fresh directory scan. A
    # file staged after load_staged_documents() has already run (simulated
    # here by writing it right after staging the first two, before main()
    # runs at all -- main() only ever sees the directory once) must not
    # appear in sample_digest's file set unless it was actually loaded.
    # (The full race -- a file appearing *during* main()'s own run, between
    # its load and fingerprint steps -- can no longer happen by
    # construction, which is exactly what this test is standing in for:
    # there is only one directory scan for main() to race against itself.)
    staging = tmp_path / "staging"
    staging.mkdir()
    _stage_document(staging, "CMO20241020862")
    _stage_document(staging, "CMO2024483790", suffix=".jpeg")
    out = tmp_path / "latency.json"

    rc = bench_mod.main(
        [
            "--fake",
            "--documents-dir",
            str(staging),
            "--repeats",
            "2",
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["n_docs"] == 2
    assert data["benchmark_context"]["sample_digest"]


def test_cli_documents_dir_fingerprint_drift_fails_loudly(tmp_path, monkeypatch, capsys):
    # CLI wiring: main() must convert a SampleFingerprintDriftError raised
    # by the insurance check into a loud, non-zero-exit CLI failure, not a
    # silently-written latency.json whose sample_digest overclaims what was
    # measured. The detection logic itself is exercised for real above;
    # this test is only about main()'s try/except around the call.
    staging = tmp_path / "staging"
    staging.mkdir()
    _stage_document(staging, "CMO20241020862")
    out = tmp_path / "latency.json"

    def fake_check(*_args, **_kwargs):
        raise SampleFingerprintDriftError(
            "sample_digest's file set does not match the documents loaded "
            "for benchmarking (fingerprinted but never loaded, so never "
            "measured: ['stowaway.pdf']; loaded but never fingerprinted: [])"
        )

    monkeypatch.setattr(
        bench_mod, "_check_fingerprint_matches_loaded_documents", fake_check
    )

    with pytest.raises(SystemExit) as exc:
        bench_mod.main(
            [
                "--fake",
                "--documents-dir",
                str(staging),
                "--repeats",
                "2",
                "--output",
                str(out),
            ]
        )
    assert exc.value.code == 2
    assert not out.exists()
    err = capsys.readouterr().err
    assert "stowaway.pdf" in err


def test_run_benchmark_over_loaded_real_documents(tmp_path):
    # No processor_factory here, so this exercises the fake timing path,
    # which never touches document_bytes/document_path — docs from
    # load_staged_documents flow through run_benchmark exactly like any
    # other custom doc list.
    _stage_document(tmp_path, "CMO20241020862")
    _stage_document(tmp_path, "CMO2024483790", suffix=".jpeg")
    docs = load_staged_documents(tmp_path)

    result = run_benchmark(variant="standard", docs=docs, repeats=2, discard_warm=False)
    assert result["n_docs"] == 2
    assert result["stages"]["e2e"]["n"] == 4
    assert {"CMO20241020862", "CMO2024483790"} == set(result["_raw"]["tickets"]["e2e"])


# ---------------------------------------------------------------------------
# #308 — streaming: only one document's bytes resident/read at a time
# ---------------------------------------------------------------------------


def test_load_staged_documents_streams_bytes_bounded_memory(tmp_path):
    """load_staged_documents() used to call path.read_bytes() for every
    staged file and hold the whole list before run_benchmark started, so an
    n-document sample cost roughly n * average_document_size resident on top
    of the loaded models — ~630 MB for the 627 KB/doc Sambalpur/2024 sample
    at n=1,000, ~44 GB for the 69,844-document corpus this harness is about
    to run over (see #308). It now hands back a document_path per document,
    and the real bytes are read lazily by _measure_with_processor, one
    document at a time. Build documents large enough that eager loading
    would clearly show up in peak traced memory, and confirm streaming keeps
    the peak near a single document's size instead of scaling with n.
    """
    n_docs = 8
    doc_size = 3_000_000  # ~3 MB filler; deterministic, no citizen data
    filler = b"%PDF-1.4\n" + (b"A" * doc_size)
    for i in range(n_docs):
        path = tmp_path / f"CMO2024{i:07d}_complaint_20250715_234307.pdf"
        path.write_bytes(filler)

    docs = load_staged_documents(tmp_path)
    assert len(docs) == n_docs
    # Nothing has been read yet — the loader only records paths.
    assert all(doc["document_bytes"] is None for doc in docs)
    assert all(doc["document_path"] is not None for doc in docs)

    class Processor:
        _timing_sink = None

        def process(self, **kwargs):
            document_bytes = kwargs["document_bytes"]
            assert document_bytes is not None
            assert len(document_bytes) == len(filler)
            self._timing_sink({"redact": 0.001, "e2e": 0.002, "ok": 1.0})

    tracemalloc.start()
    try:
        run_benchmark(
            variant="standard",
            docs=docs,
            repeats=1,
            discard_warm=False,
            processor_factory=lambda _variant: Processor(),
        )
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    # Eager loading (the bug) would resident roughly n_docs * doc_size here
    # (~24 MB for 8 x 3 MB). Streaming should keep the traced peak within a
    # small multiple of one document, not scale with the sample size.
    assert peak < doc_size * 3, (
        f"peak traced memory {peak} bytes suggests more than one document's "
        f"bytes were resident at once (n_docs={n_docs}, doc_size={doc_size})"
    )


def test_measure_with_processor_reads_each_document_once_not_per_repeat(tmp_path, monkeypatch):
    # Bounded reads, not just bounded memory: a document's bytes must be
    # read once and reused across its repeats, not re-read from disk every
    # repeat (which would still be correct memory-wise but wasteful I/O).
    _stage_document(tmp_path, "CMO20241020862")
    _stage_document(tmp_path, "CMO2024483790", suffix=".jpeg")
    docs = load_staged_documents(tmp_path)

    read_calls: list[Path] = []
    original_read_bytes = Path.read_bytes

    def counting_read_bytes(self):
        read_calls.append(self)
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    class Processor:
        _timing_sink = None

        def process(self, **kwargs):
            self._timing_sink({"redact": 0.001, "e2e": 0.002, "ok": 1.0})

    run_benchmark(
        variant="standard",
        docs=docs,
        repeats=3,
        discard_warm=False,
        processor_factory=lambda _variant: Processor(),
    )

    # 2 documents x 3 repeats: without the fix this would be up to 6 reads
    # from inside the loop (or reads already done eagerly by the loader).
    # Reading once per document and reusing it across repeats caps this at 2.
    assert len(read_calls) == 2
    assert {p.name for p in read_calls} == {d["document_path"].name for d in docs}


def test_run_benchmark_fake_path_never_reads_staged_bytes(tmp_path, monkeypatch):
    # The fake timing path (no processor_factory) never calls process(), so
    # it has no use for document bytes at all; the lazy read must not fire
    # just because a real staged sample happens to be on disk.
    _stage_document(tmp_path, "CMO20241020862")
    docs = load_staged_documents(tmp_path)

    def failing_read_bytes(self):
        raise AssertionError(f"fake timing path must not read {self}")

    monkeypatch.setattr(Path, "read_bytes", failing_read_bytes)

    result = run_benchmark(variant="standard", docs=docs, repeats=2, discard_warm=False)
    assert result["n_docs"] == 1


def test_document_kind_reads_the_doc_not_the_ticket_string():
    # Regression for P1-1 (PR #307 review): input-path classification used
    # to match on a "SYN-TXT-"/"SYN-IMG-" ticket prefix, so every real
    # ticket (no such prefix) fell through to "unspecified". The doc's own
    # fields settle it instead.
    text_doc = {"ticket": "CMO20241020862", "text": "hello", "document_bytes": None}
    document_doc = {"ticket": "CMO20241020862", "text": None, "document_bytes": b"%PDF-1.4"}
    empty_doc = {"ticket": "CMO20241020862", "text": None, "document_bytes": None}
    assert _document_kind(text_doc) == "text"
    assert _document_kind(document_doc) == "document"
    assert _document_kind(empty_doc) == "unspecified"


def test_real_document_only_run_reaches_publication_ready(tmp_path):
    # Regression for P1-1 (PR #307 review): --documents-dir stages real
    # documents only (no text grievances mixed in), so a clean run has just
    # the "document" input path. The gate used to hardcode requiring BOTH
    # "text" and "document" paths with n > 0, which made every real-
    # document run structurally unpublishable regardless of how clean the
    # measurements were. This is the exact scenario the whole branch exists
    # to make publishable, so it must reach publication_ready: true when
    # everything else about the run is clean.
    _stage_document(tmp_path, "CMO20241020862")
    _stage_document(tmp_path, "CMO2024483790", suffix=".jpeg")
    docs = load_staged_documents(tmp_path)

    result = run_benchmark(
        variant="standard",
        docs=docs,
        repeats=2,
        discard_warm=False,
        processor_factory=lambda _variant: type(
            "Processor",
            (),
            {
                "_timing_sink": None,
                "process": lambda self, **kwargs: self._timing_sink(
                    {"redact": 0.1, "e2e": 0.2, "ok": 1.0}
                ),
            },
        )(),
    )
    result["benchmark_context"] = {
        "host_label": "release-host",
        "model_release_id": "model-release-1",
    }

    assert set(result["input_paths"]) == {"document"}
    payload = latency_json_payload(result)
    assert payload["publication_ready"] is True


# ---------------------------------------------------------------------------
# CLI wiring for --documents-dir / --slice
# ---------------------------------------------------------------------------


def test_cli_documents_dir_and_n_docs_are_mutually_exclusive(tmp_path, capsys):
    _stage_document(tmp_path, "CMO20241020862")
    out = tmp_path / "latency.json"
    with pytest.raises(SystemExit) as exc:
        bench_mod.main(
            [
                "--fake",
                "--documents-dir",
                str(tmp_path),
                "--n-docs",
                "2",
                "--repeats",
                "2",
                "--output",
                str(out),
            ]
        )
    assert exc.value.code == 2
    assert not out.exists()
    err = capsys.readouterr().err
    assert "mutually exclusive" in err


def test_cli_documents_dir_runs_fake_and_records_provenance(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    _stage_document(staging, "CMO20241020862")
    _stage_document(staging, "CMO2024483790", suffix=".jpeg")
    out = tmp_path / "latency.json"

    rc = bench_mod.main(
        [
            "--fake",
            "--documents-dir",
            str(staging),
            "--slice",
            "Sambalpur/2024",
            "--repeats",
            "2",
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["n_docs"] == 2
    ctx = data["benchmark_context"]
    assert ctx["sample_slice"] == "Sambalpur/2024"
    assert ctx["sample_document_count"] == 2
    assert ctx["sample_digest"]
    assert "real staged document sample" in ctx["fixture"]
    assert "synthetic" not in ctx["fixture"]
    # Additive-only: synthetic-path keys are still present.
    assert ctx["execution"] == "sequential single-process execution"


def test_cli_documents_dir_uses_manifest_slice_when_no_override(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    _stage_document(staging, "CMO20241020862")
    manifest = {"slice": "Khordha/2023", "documents": [{"ticket": "CMO20241020862"}]}
    (staging / "sample_manifest.json").write_text(json.dumps(manifest))
    out = tmp_path / "latency.json"

    rc = bench_mod.main(
        [
            "--fake",
            "--documents-dir",
            str(staging),
            "--repeats",
            "2",
            "--no-warm-discard",
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["benchmark_context"]["sample_slice"] == "Khordha/2023"


def test_cli_documents_dir_empty_directory_fails_loudly(tmp_path, capsys):
    empty = tmp_path / "empty_staging"
    empty.mkdir()
    out = tmp_path / "latency.json"
    with pytest.raises(SystemExit) as exc:
        bench_mod.main(
            ["--fake", "--documents-dir", str(empty), "--repeats", "2", "--output", str(out)]
        )
    assert exc.value.code == 2
    assert not out.exists()
    err = capsys.readouterr().err
    assert "no supported documents" in err


def test_cli_synthetic_path_benchmark_context_fixture_is_unchanged(tmp_path):
    # Regression guard for the actual defect: outputs/benchmark/latency.json
    # today records "deterministic synthetic grievances without citizen
    # data" for its fixture. Existing artifacts and any consumer of that
    # exact string must not break when the real-document path is added.
    out = tmp_path / "latency.json"
    rc = bench_mod.main(
        [
            "--fake",
            "--variant",
            "standard",
            "--n-docs",
            "2",
            "--n-image-docs",
            "1",
            "--repeats",
            "2",
            "--output",
            str(out),
            "--seed",
            "42",
        ]
    )
    assert rc == 0
    data = json.loads(out.read_text())
    ctx = data["benchmark_context"]
    assert ctx["fixture"] == "deterministic synthetic grievances without citizen data"
    assert ctx["execution"] == "sequential single-process execution"
    # No document-sample keys leak onto the synthetic path. The OCR
    # toolchain keys (#315 follow-up) are additive and present on every
    # path, including synthetic — see test_cli_synthetic_path_records_ocr_toolchain.
    assert set(ctx) == {
        "host_label",
        "model_release_id",
        "fixture",
        "execution",
        "tesseract_version",
        "tesseract_langs",
        "poppler_version",
    }


# ---------------------------------------------------------------------------
# #315 Codex follow-up — OCR toolchain provenance in benchmark_context
# ---------------------------------------------------------------------------


def test_probe_ocr_toolchain_populates_real_values_when_installed():
    # Real code path, no mocking of pytesseract/poppler. Base CI
    # (pipeline.yml) deliberately runs `uv sync --all-groups --extra
    # serving` without pipeline-core, so pytesseract/pdf2image are not
    # importable there and this test must skip rather than assert on
    # "unavailable" — that path has its own test below. Locally, with
    # pipeline-core installed (tests/README.md's gate requirement), this
    # must report real values: two machines with different tesseract
    # versions or a missing Odia (ori) pack must be distinguishable from
    # this output, which is the entire point of #315's follow-up.
    #
    # The importorskips gate on the Python wrappers; the assertions below
    # need the system binaries those wrappers shell out to. They are
    # separately installable, so `pip install pytesseract` on a host with no
    # `tesseract` on PATH satisfies the import and still probes
    # "unavailable" — correct behaviour that used to fail this test, and so
    # the repo's required test command, on a legitimate environment. Gate on
    # what is actually asserted.
    pytest.importorskip("pytesseract")
    pytest.importorskip("pdf2image")
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract binary not on PATH; _probe_tesseract has its own test")
    if shutil.which("pdftoppm") is None:
        pytest.skip("poppler (pdftoppm) not on PATH; _probe_poppler_version has its own test")

    toolchain = bench_mod._probe_ocr_toolchain()

    assert set(toolchain) == {"tesseract_version", "tesseract_langs", "poppler_version"}
    assert toolchain["tesseract_version"] != "unavailable"
    assert toolchain["tesseract_version"][0].isdigit()
    assert isinstance(toolchain["tesseract_langs"], list)
    assert toolchain["tesseract_langs"] == sorted(toolchain["tesseract_langs"])
    assert "eng" in toolchain["tesseract_langs"]
    assert toolchain["poppler_version"] != "unavailable"
    assert "pdftoppm" in toolchain["poppler_version"].lower()


def test_probe_ocr_toolchain_records_unavailable_when_tesseract_probe_fails(monkeypatch):
    # Simulate a box where tesseract is importable but misbehaves at runtime
    # (as opposed to not being installed at all, which base CI already
    # exercises for real — see test_probe_ocr_toolchain_composes_independent_
    # tesseract_and_poppler_probes, which asserts nothing about availability
    # and so passes either way). monkeypatch.setattr's string-path form has
    # to import pytesseract_backend to resolve the target, and that module
    # does `import pytesseract` at module scope, so this needs pytesseract
    # importable too — skip in base CI rather than error out on the mock
    # setup itself. The probe must not raise, must not silently omit the
    # keys, and must not let a tesseract failure take poppler's
    # (independently probed) result down with it.
    pytest.importorskip("pytesseract")

    def boom():
        raise RuntimeError("tesseract not installed on this box")

    monkeypatch.setattr(
        "janasunani.pipeline.stages.ocr_extraction.pytesseract_backend._configure_tesseract",
        boom,
    )

    toolchain = bench_mod._probe_ocr_toolchain()

    assert toolchain["tesseract_version"] == "unavailable"
    assert toolchain["tesseract_langs"] == "unavailable"
    assert toolchain["poppler_version"] != "unavailable"  # unaffected


def test_probe_ocr_toolchain_composes_independent_tesseract_and_poppler_probes():
    # _probe_ocr_toolchain() itself has no per-field try/except — it trusts
    # _probe_tesseract()/_probe_poppler_version() to each be defensive on
    # their own (tested directly above/below). What it must get right is
    # composition: both fields present, independently sourced.
    toolchain = bench_mod._probe_ocr_toolchain()
    tesseract_version, tesseract_langs = bench_mod._probe_tesseract()
    assert toolchain["tesseract_version"] == tesseract_version
    assert toolchain["tesseract_langs"] == tesseract_langs
    assert toolchain["poppler_version"] == bench_mod._probe_poppler_version()


def test_probe_poppler_version_never_raises_when_subprocess_fails(monkeypatch):
    # One level lower than the test above: exercises _probe_poppler_version
    # itself (not the composite _probe_ocr_toolchain) against the exact
    # failure it is written to catch — subprocess.run raising because
    # pdftoppm isn't installed.
    def boom(*_args, **_kwargs):
        raise FileNotFoundError("pdftoppm not found")

    monkeypatch.setattr(bench_mod.subprocess, "run", boom)

    assert bench_mod._probe_poppler_version() == "unavailable"


def test_cli_synthetic_path_records_ocr_toolchain(tmp_path):
    out = tmp_path / "latency.json"
    rc = bench_mod.main(
        ["--fake", "--n-docs", "2", "--repeats", "2", "--output", str(out)]
    )
    assert rc == 0
    ctx = json.loads(out.read_text())["benchmark_context"]
    for key in ("tesseract_version", "tesseract_langs", "poppler_version"):
        assert key in ctx


def test_cli_harness_still_runs_when_ocr_toolchain_probe_itself_raises(tmp_path, monkeypatch):
    # Belt-and-suspenders: even if _probe_ocr_toolchain's own internal
    # defensiveness were ever broken by a future change, main()'s outer
    # catch must still keep the synthetic (--fake) path — which has no OCR
    # dependency of its own — running to completion rather than crashing on
    # an environment probe.
    def boom():
        raise RuntimeError("unexpected probe failure")

    monkeypatch.setattr(bench_mod, "_probe_ocr_toolchain", boom)

    out = tmp_path / "latency.json"
    rc = bench_mod.main(
        ["--fake", "--n-docs", "2", "--repeats", "2", "--output", str(out)]
    )
    assert rc == 0
    ctx = json.loads(out.read_text())["benchmark_context"]
    assert ctx["tesseract_version"] == "unavailable"
    assert ctx["tesseract_langs"] == "unavailable"
    assert ctx["poppler_version"] == "unavailable"


def test_run_benchmark_standard_variant_basic():
    # Small n for fast test
    result = run_benchmark(variant="standard", n_text=4, n_image=2, repeats=3, seed=123)
    assert result["variant"] == "standard"
    assert result["n_docs"] == 6
    assert result["repeats"] == 3
    assert result["warm_discarded"] is True
    # n_measured = n_docs * (repeats - 1) when warm discarded
    assert result["n_measured"] == 6 * 2
    assert result["attempts"] == 18
    assert result["completed_attempts"] == 18
    assert result["failed_attempts"] == 0
    assert {"text", "document"} <= set(result["input_paths"])
    assert result["input_paths"]["text"]["e2e"]["n"] == 8
    assert result["input_paths"]["document"]["e2e"]["n"] == 4
    assert result["environment"]["python"]
    assert "stages" in result
    for key in ALL_KEYS:
        assert key in result["stages"]
        stage = result["stages"][key]
        for k in ["mean_seconds", "se_seconds", "n_clusters", "p50", "p90", "p95"]:
            assert k in stage
        # All clusters should be n_docs (each ticket is its own cluster)
        assert stage["n_clusters"] == 6
        assert stage["n"] == 12  # 6 docs * 2 measured repeats
        assert stage["mean_seconds"] > 0
        assert stage["se_seconds"] >= 0
        assert stage["p95"] >= stage["p50"]


def test_run_benchmark_warm_discard_vs_no_discard():
    r_warm = run_benchmark(variant="standard", n_text=4, n_image=0, repeats=3, discard_warm=True, seed=1)
    r_no_warm = run_benchmark(variant="standard", n_text=4, n_image=0, repeats=3, discard_warm=False, seed=1)
    assert r_warm["n_measured"] == 8  # 4 * 2
    assert r_no_warm["n_measured"] == 12  # 4 * 3
    # Warm discard should have same stages but different n
    assert r_warm["stages"]["e2e"]["n"] == 8
    assert r_no_warm["stages"]["e2e"]["n"] == 12


def test_run_benchmark_all_variants_produce_different_ocr_means():
    # Sarvam digitise should have higher OCR mean than standard (fake timings)
    r_std = run_benchmark(variant="standard", n_text=4, n_image=0, repeats=2, discard_warm=False, seed=99)
    r_digit = run_benchmark(variant="sarvam_digitise", n_text=4, n_image=0, repeats=2, discard_warm=False, seed=99)
    assert r_digit["stages"]["ocr"]["mean_seconds"] > r_std["stages"]["ocr"]["mean_seconds"]
    # sarvam_extract should differ in summarize/categorize
    r_ext = run_benchmark(variant="sarvam_extract", n_text=4, n_image=0, repeats=2, discard_warm=False, seed=99)
    assert r_ext["stages"]["summarize"]["mean_seconds"] != pytest.approx(r_std["stages"]["summarize"]["mean_seconds"])


def test_run_benchmark_invalid_variant_raises():
    with pytest.raises(ValueError, match="unknown variant"):
        run_benchmark(variant="invalid", n_text=2, n_image=0, repeats=2)


def test_run_benchmark_zero_docs_raises():
    with pytest.raises(ValueError, match="no documents"):
        run_benchmark(variant="standard", n_text=0, n_image=0, repeats=2)


def test_run_benchmark_custom_docs():
    docs = [
        {"ticket": "T1", "text": "hello", "document_name": None, "document_bytes": None, "district": "Sambalpur"},
        {"ticket": "T2", "text": "world", "document_name": None, "document_bytes": None, "district": "Sambalpur"},
    ]
    result = run_benchmark(variant="standard", docs=docs, repeats=2, discard_warm=False)
    assert result["n_docs"] == 2
    assert result["stages"]["e2e"]["n"] == 4  # 2 docs * 2 repeats
    assert result["stages"]["e2e"]["n_clusters"] == 2


def test_run_benchmark_with_fake_processor_factory():
    # Processor factory that returns an object with process method
    class FakeProcessor:
        def process(self, **kwargs):
            # Simulate tiny work
            pass

    def factory(variant):
        return FakeProcessor()

    result = run_benchmark(variant="standard", n_text=3, n_image=0, repeats=2, discard_warm=False, processor_factory=factory)
    assert result["stages"]["e2e"]["mean_seconds"] >= 0
    assert result["stages"]["e2e"]["n"] == 6


def test_real_processor_failures_are_counted_without_polluting_timings():
    class IntermittentProcessor:
        _timing_sink = None

        def __init__(self):
            self.calls = 0

        def process(self, **kwargs):
            self.calls += 1
            if self.calls == 2:
                self._timing_sink({"redact": 99.0, "e2e": 99.0})
                raise RuntimeError("synthetic failure")
            self._timing_sink({"redact": 0.1, "e2e": 0.2, "ok": 1.0})

    result = run_benchmark(
        variant="standard",
        n_text=2,
        n_image=0,
        repeats=2,
        discard_warm=False,
        processor_factory=lambda _variant: IntermittentProcessor(),
    )

    assert result["attempts"] == 4
    assert result["completed_attempts"] == 3
    assert result["failed_attempts"] == 1
    assert result["failures_by_error"] == {"RuntimeError": 1}
    assert result["n_measured"] == 3
    assert result["stages"]["redact"]["n"] == 3
    assert result["stages"]["redact"]["mean_seconds"] == pytest.approx(0.1)


def test_real_processor_stage_names_are_preserved():
    class TimedProcessor:
        _timing_sink = None

        def process(self, **kwargs):
            self._timing_sink(
                {"redact": 0.1, "categorize": 0.2, "e2e": 0.4, "ok": 1.0}
            )

    result = run_benchmark(
        variant="standard",
        n_text=1,
        n_image=0,
        repeats=2,
        discard_warm=False,
        processor_factory=lambda _variant: TimedProcessor(),
    )
    assert result["stages"]["redact"]["n"] == 2
    assert result["stages"]["categorize"]["mean_seconds"] == pytest.approx(0.2)
    assert "ok" not in result["stages"]


def test_latency_json_payload_single_variant(tmp_path):
    result = run_benchmark(variant="standard", n_text=3, n_image=0, repeats=2, discard_warm=False, seed=7)
    payload = latency_json_payload(result)
    assert payload["variant"] == "standard"
    assert "stages" in payload
    assert "variants" in payload
    assert "standard" in payload["variants"]
    assert payload["schema_version"] == "janasunani.pipeline-latency/v1"
    assert payload["publication_ready"] is False
    # Stages should have all required keys
    assert "e2e" in payload["stages"]
    assert "mean_seconds" in payload["stages"]["e2e"]


def test_latency_json_payload_multi_variant(tmp_path):
    r1 = run_benchmark(variant="standard", n_text=2, n_image=0, repeats=2, discard_warm=False, seed=1)
    r2 = run_benchmark(variant="sarvam_digitise", n_text=2, n_image=0, repeats=2, discard_warm=False, seed=1)
    payload = latency_json_payload({"standard": r1, "sarvam_digitise": r2})
    assert "variants" in payload
    assert "standard" in payload["variants"]
    assert "sarvam_digitise" in payload["variants"]
    assert payload["n_variants"] == 2
    assert payload["schema_version"] == "janasunani.pipeline-latency/v1"
    assert payload["publication_ready"] is False


def test_identified_real_latency_run_is_publication_ready():
    result = run_benchmark(
        variant="standard",
        n_text=1,
        n_image=1,
        repeats=2,
        discard_warm=False,
        processor_factory=lambda _variant: type(
            "Processor",
            (),
            {
                "_timing_sink": None,
                "process": lambda self, **kwargs: self._timing_sink(
                    {"redact": 0.1, "e2e": 0.2, "ok": 1.0}
                ),
            },
        )(),
    )
    result["benchmark_context"] = {
        "host_label": "release-host",
        "model_release_id": "model-release-1",
    }

    payload = latency_json_payload(result)

    assert payload["publication_ready"] is True
    assert payload["temperature_e2e"]["cold"]["n"] == 1
    assert payload["temperature_e2e"]["warm"]["n"] == 3

    result["git_sha"] = None
    assert latency_json_payload(result)["publication_ready"] is False
    result["git_sha"] = "abc1234"
    result["benchmark_context"]["host_label"] = "   "
    assert latency_json_payload(result)["publication_ready"] is False


def test_real_latency_with_failure_is_not_publication_ready():
    calls = 0

    class SometimesFailingProcessor:
        _timing_sink = None

        def process(self, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("benchmark failure")
            self._timing_sink({"redact": 0.1, "e2e": 0.2, "ok": 1.0})

    result = run_benchmark(
        variant="standard",
        n_text=1,
        n_image=1,
        repeats=2,
        discard_warm=False,
        processor_factory=lambda _variant: SometimesFailingProcessor(),
    )
    result["benchmark_context"] = {
        "host_label": "release-host",
        "model_release_id": "model-release-1",
    }

    assert result["failed_attempts"] == 1
    assert latency_json_payload(result)["publication_ready"] is False


def test_real_latency_single_kind_run_can_be_publication_ready():
    # Regression for P1-1 (PR #307 review): this used to assert the
    # opposite — that a text-only real run could never be
    # publication_ready — because the gate hardcoded requiring BOTH "text"
    # and "document" input_paths. That made every --documents-dir run
    # (document-only, no synthetic text mixed in) structurally
    # unpublishable no matter how clean the measurements were. The gate now
    # requires clean coverage of whichever kinds a run actually exercised;
    # a run that only ever saw text grievances is clean if its one path
    # (text) is clean, symmetric with a document-only run (see
    # test_real_document_only_run_reaches_publication_ready below).
    result = run_benchmark(
        variant="standard",
        n_text=1,
        n_image=0,
        repeats=2,
        discard_warm=False,
        processor_factory=lambda _variant: type(
            "Processor",
            (),
            {
                "_timing_sink": None,
                "process": lambda self, **kwargs: self._timing_sink(
                    {"redact": 0.1, "e2e": 0.2, "ok": 1.0}
                ),
            },
        )(),
    )
    result["benchmark_context"] = {
        "host_label": "release-host",
        "model_release_id": "model-release-1",
    }

    assert set(result["input_paths"]) == {"text"}
    assert latency_json_payload(result)["publication_ready"] is True


def test_real_latency_with_unspecified_input_kind_is_not_publication_ready():
    # A doc with neither text nor document_bytes can't be classified by
    # _document_kind, and "unspecified" must never satisfy the coverage
    # gate: it means the provenance of at least one measurement is unknown,
    # which is exactly what this gate exists to catch.
    docs = [
        {
            "ticket": "T1",
            "text": None,
            "document_name": None,
            "document_bytes": None,
            "district": "Sambalpur",
        },
    ]
    result = run_benchmark(
        variant="standard",
        docs=docs,
        repeats=2,
        discard_warm=False,
        processor_factory=lambda _variant: type(
            "Processor",
            (),
            {
                "_timing_sink": None,
                "process": lambda self, **kwargs: self._timing_sink(
                    {"redact": 0.1, "e2e": 0.2, "ok": 1.0}
                ),
            },
        )(),
    )
    result["benchmark_context"] = {
        "host_label": "release-host",
        "model_release_id": "model-release-1",
    }

    assert set(result["input_paths"]) == {"unspecified"}
    assert latency_json_payload(result)["publication_ready"] is False


def test_real_latency_without_warm_sample_is_not_publication_ready():
    result = run_benchmark(
        variant="standard",
        n_text=1,
        n_image=0,
        repeats=1,
        discard_warm=False,
        processor_factory=lambda _variant: type(
            "Processor",
            (),
            {
                "_timing_sink": None,
                "process": lambda self, **kwargs: self._timing_sink(
                    {"redact": 0.1, "e2e": 0.2, "ok": 1.0}
                ),
            },
        )(),
    )
    result["benchmark_context"] = {
        "host_label": "release-host",
        "model_release_id": "model-release-1",
    }

    assert latency_json_payload(result)["publication_ready"] is False


def test_temperature_aggregates_have_one_cold_request_per_processor():
    result = run_benchmark(
        variant="standard",
        n_text=2,
        n_image=0,
        repeats=2,
        discard_warm=False,
        processor_factory=lambda _variant: type(
            "Processor",
            (),
            {
                "_timing_sink": None,
                "process": lambda self, **kwargs: self._timing_sink(
                    {"redact": 0.1, "e2e": 0.2, "ok": 1.0}
                ),
            },
        )(),
    )

    assert result["temperature_e2e"]["cold"]["n"] == 1
    assert result["temperature_e2e"]["warm"]["n"] == 3
    assert result["temperature_definition"]["cold"].startswith(
        "first successful request"
    )


def test_write_latency_json_creates_file(tmp_path):
    result = run_benchmark(variant="standard", n_text=2, n_image=1, repeats=2, discard_warm=True, seed=5)
    out = tmp_path / "outputs" / "benchmark" / "latency.json"
    path = write_latency_json(result, out)
    assert path == out
    assert out.is_file()
    data = json.loads(out.read_text())
    assert "stages" in data
    assert data["variant"] == "standard"
    assert "e2e" in data["stages"]
    # Check JSON has mean/se/p50/p90/p95 per stage
    for stage_key in ALL_KEYS:
        entry = data["stages"][stage_key]
        assert "mean_seconds" in entry
        assert "se_seconds" in entry
        assert "p50" in entry
        assert "p90" in entry
        assert "p95" in entry
        assert "throughput_per_second" in entry
        assert "n_clusters" in entry


def test_write_latency_json_multi_variant_file(tmp_path):
    r1 = run_benchmark(variant="standard", n_text=2, n_image=0, repeats=2, discard_warm=False, seed=10)
    r2 = run_benchmark(variant="sarvam_both", n_text=2, n_image=0, repeats=2, discard_warm=False, seed=10)
    out = tmp_path / "latency.json"
    path = write_latency_json({"standard": r1, "sarvam_both": r2}, out)
    data = json.loads(path.read_text())
    assert "variants" in data
    assert data["variants"]["standard"]["variant"] == "standard"
    assert data["variants"]["sarvam_both"]["variant"] == "sarvam_both"


def test_cli_single_variant_writes_output(tmp_path):
    # `--fake` is now explicit. It used to be implied for every run, because
    # main() forced is_fake=True regardless, so this test exercised the
    # synthetic table while appearing to exercise the CLI end to end.
    out = tmp_path / "latency.json"
    rc = bench_mod.main(["--fake", "--variant", "standard", "--n-docs", "2", "--n-image-docs", "1", "--repeats", "2", "--output", str(out), "--seed", "42"])
    assert rc == 0
    assert out.is_file()
    data = json.loads(out.read_text())
    assert data["variant"] == "standard"
    assert data["stages"]["e2e"]["n"] == 3 * 1  # (2+1)=3 docs * (2-1)=1 measured
    assert data["is_fake_timing"] is True


def test_a_run_without_fake_does_not_silently_fabricate(tmp_path, monkeypatch):
    """The regression for the unconditional `is_fake = True` override.

    Before this, `main()` forced fake timings whether or not `--fake` was
    passed, so every latency number this harness ever produced came from
    `_FAKE_STAGE_MEANS`. A run that cannot measure must fail, not invent.
    """
    out = tmp_path / "latency.json"

    def _no_processor(*args, **kwargs):
        raise RuntimeError("models are not available in this environment")

    monkeypatch.setattr(
        "janasunani.inference.service.build_processor", _no_processor, raising=False
    )

    with pytest.raises((SystemExit, RuntimeError)):
        bench_mod.main(
            ["--variant", "standard", "--n-docs", "1", "--n-image-docs", "0",
             "--repeats", "2", "--output", str(out), "--seed", "42"]
        )
    # Nothing fabricated on the way out.
    if out.exists():
        assert json.loads(out.read_text()).get("is_fake_timing") is not False


def test_real_run_records_per_stage_from_the_timing_sink():
    """Per-stage comes from the processor, not from proportions of e2e."""
    from janasunani.inference.timing import StageTimer

    timer = StageTimer()
    with timer.stage("redact"):
        pass
    with timer.stage("categorize"):
        pass
    timings = timer.as_dict()
    assert {"redact", "categorize", "e2e"} <= set(timings)
    # e2e is measured independently, so the gap to the sum of stages stays
    # visible rather than being defined away.
    assert timings["e2e"] >= 0.0


def test_cli_multi_variants(tmp_path):
    # `--fake` is explicit: this asserts the CLI's output shape, not latency.
    # Without it the harness builds a real processor, which needs the DVC-
    # mirrored models that CI deliberately does not pull.
    out = tmp_path / "latency.json"
    rc = bench_mod.main(
        [
            "--fake",
            "--variants",
            "standard",
            "sarvam_digitise",
            "--n-docs",
            "2",
            "--n-image-docs",
            "0",
            "--repeats",
            "2",
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    data = json.loads(out.read_text())
    assert "variants" in data
    assert "standard" in data["variants"]
    assert "sarvam_digitise" in data["variants"]


def test_real_sarvam_variant_is_rejected_until_it_is_wired(tmp_path):
    with pytest.raises(SystemExit) as exc:
        bench_mod.main(
            [
                "--variant",
                "sarvam_digitise",
                "--n-docs",
                "1",
                "--n-image-docs",
                "0",
                "--repeats",
                "2",
                "--output",
                str(tmp_path / "latency.json"),
            ]
        )
    assert exc.value.code == 2


def test_cli_no_warm_discard(tmp_path):
    out = tmp_path / "latency.json"
    rc = bench_mod.main(
        ["--fake", "--variant", "standard", "--n-docs", "2", "--n-image-docs", "0", "--repeats", "2", "--output", str(out), "--no-warm-discard"]
    )
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["warm_discarded"] is False
    assert data["stages"]["e2e"]["n"] == 4  # 2 docs * 2 repeats


def test_fake_process_same_call_is_deterministic():
    # Same inputs, same process: must be bit-identical (sanity before the
    # cross-process PYTHONHASHSEED check below).
    t1 = _fake_process("standard", "SYN-TXT-0001", 1, seed=42)
    t2 = _fake_process("standard", "SYN-TXT-0001", 1, seed=42)
    assert t1.e2e == t2.e2e
    assert t1.per_stage == t2.per_stage


def test_fake_process_timings_stable_across_pythonhashseed():
    """Regression for #208: rng_seed must not be derived from hash().

    Python salts hash() per-interpreter via PYTHONHASHSEED, so seeding the
    RNG from hash((seed, ticket, repeat_idx, variant)) made identical
    --seed + docs produce different fake latency means/SEs across
    machines/runs. The fix (hashlib.sha256 digest) must give the same
    fake timings for the same inputs regardless of PYTHONHASHSEED.
    """
    script = (
        "import json, sys; "
        "from scripts.benchmark_pipeline import _fake_process; "
        "t = _fake_process('sarvam_extract', 'SYN-TXT-0007', 2, seed=42); "
        "print(json.dumps({'e2e': t.e2e, 'per_stage': t.per_stage}))"
    )
    outputs = []
    for hashseed in ("0", "1", "1337"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hashseed
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
            check=True,
        )
        outputs.append(json.loads(proc.stdout.strip()))
    assert outputs[0] == outputs[1] == outputs[2], (
        f"fake timings differ across PYTHONHASHSEED: {outputs}"
    )


def test_cli_repeats_one_with_warm_discard_rejected(tmp_path, capsys):
    # #209: --repeats 1 with default warmup discard means every observation
    # is the discarded warm one — the harness must reject this instead of
    # silently writing an all-zero latency report with exit 0.
    out = tmp_path / "latency.json"
    with pytest.raises(SystemExit) as exc:
        bench_mod.main(
            ["--repeats", "1", "--output", str(out), "--fake", "--n-docs", "2", "--n-image-docs", "0"]
        )
    assert exc.value.code == 2
    assert not out.exists()
    err = capsys.readouterr().err
    assert "--repeats" in err
    assert "--no-warm-discard" in err


def test_cli_repeats_one_with_no_warm_discard_is_allowed(tmp_path):
    # The sole observation is retained (not discarded) when warmup discard
    # is explicitly turned off, so repeats=1 is legitimate there.
    out = tmp_path / "latency.json"
    rc = bench_mod.main(
        [
            "--repeats",
            "1",
            "--no-warm-discard",
            "--output",
            str(out),
            "--fake",
            "--n-docs",
            "2",
            "--n-image-docs",
            "0",
        ]
    )
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["stages"]["e2e"]["n"] == 2
    assert data["stages"]["e2e"]["mean_seconds"] > 0


def test_benchmark_end_to_end_structure_matches_output_spec(tmp_path):
    """Ensure the on-disk JSON has exactly the spec-required shape."""
    result = run_benchmark(variant="sarvam_extract", n_text=3, n_image=1, repeats=3, seed=99)
    out = tmp_path / "latency.json"
    write_latency_json(result, out)
    data = json.loads(out.read_text())
    # Top-level keys required by spec
    assert "variant" in data
    assert "n_docs" in data
    assert "repeats" in data
    assert "stages" in data
    assert "timestamp" in data
    # Per-stage required metrics
    for stage in ALL_KEYS:
        s = data["stages"][stage]
        assert "mean_seconds" in s, f"missing mean_seconds for {stage}"
        assert "se_seconds" in s, f"missing se_seconds for {stage}"
        assert "n_clusters" in s, f"missing n_clusters for {stage}"
        assert "p50" in s, f"missing p50 for {stage}"
        assert "p90" in s, f"missing p90 for {stage}"
        assert "p95" in s, f"missing p95 for {stage}"
    # n_measured matches e2e.n when warm discarded
    assert data["n_measured"] == data["stages"]["e2e"]["n"]
