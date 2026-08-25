"""Unit E — janasunani-evaluate-sarvam CLI (arm / schema / metadata join).

Recorded / dry-run only; no live Sarvam call.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


def test_grievance_schema_is_pinned_and_versioned():
    from janasunani.evaluation.sarvam_grievance_schema import (
        GRIEVANCE_EXTRACT_FIELDS_V1,
        GRIEVANCE_EXTRACT_SCHEMA_V1,
        SCHEMA_VERSION,
        SUPPORTED_SCHEMA_VERSIONS,
        get_schema,
    )

    assert SCHEMA_VERSION == "v1"
    assert "v1" in SUPPORTED_SCHEMA_VERSIONS
    assert SUPPORTED_SCHEMA_VERSIONS["v1"] is GRIEVANCE_EXTRACT_SCHEMA_V1
    schema = get_schema("v1")
    assert schema is GRIEVANCE_EXTRACT_SCHEMA_V1
    # illustrative fields from plan, now under the JSON Schema root
    for field in ("grievance_category", "summary", "district", "grievance_text"):
        assert field in GRIEVANCE_EXTRACT_FIELDS_V1
        assert GRIEVANCE_EXTRACT_FIELDS_V1[field]["type"] == "string"
        assert GRIEVANCE_EXTRACT_FIELDS_V1[field]["description"].strip()
    with pytest.raises(ValueError, match="unknown schema"):
        get_schema("v9")
    with pytest.raises(ValueError, match="unknown schema"):
        get_schema("")


def test_pinned_schema_satisfies_the_provider_contract():
    """The pinned schema must be a whole JSON Schema document.

    Sarvam answers a bare field map with HTTP 400. Verified live on
    2026-08-09: bare map -> 400 on every page, wrapped -> job completed.
    The old assertions checked ``schema["summary"]["type"]``, which only
    passes for the shape the provider rejects, so the test agreed with the
    bug. This asserts the shape that actually submits.
    """
    from janasunani.egress.sarvam import _validate_extract_schema
    from janasunani.evaluation.sarvam_grievance_schema import get_schema

    schema = get_schema("v1")
    assert schema["type"] == "object"
    assert schema["properties"]
    _validate_extract_schema(schema)  # must not raise


def test_extract_rejects_a_bare_field_map_before_any_egress():
    """The exact payload that failed 5 of 5 pages must now fail loudly."""
    from janasunani.egress.sarvam import _validate_extract_schema
    from janasunani.evaluation.sarvam_grievance_schema import GRIEVANCE_EXTRACT_FIELDS_V1

    with pytest.raises(ValueError, match="must be"):
        _validate_extract_schema(GRIEVANCE_EXTRACT_FIELDS_V1)


@pytest.mark.parametrize(
    "schema, expected",
    [
        ({"type": "array", "properties": {"a": {"type": "string", "description": "x"}}}, "must be"),
        ({"type": "object"}, "non-empty 'properties'"),
        ({"type": "object", "properties": {}}, "non-empty 'properties'"),
        ({"type": "object", "properties": {"a": {"description": "x"}}}, "needs a 'type'"),
        (
            {"type": "object", "properties": {"a": {"type": "nonsense", "description": "x"}}},
            "unsupported type",
        ),
        ({"type": "object", "properties": {"a": {"type": "string"}}}, "non-empty 'description'"),
        (
            {"type": "object", "properties": {"a": {"type": "string", "description": "  "}}},
            "non-empty 'description'",
        ),
    ],
)
def test_extract_schema_guard_rejects_malformed_schemas(schema, expected):
    from janasunani.egress.sarvam import _validate_extract_schema

    with pytest.raises(ValueError, match=expected):
        _validate_extract_schema(schema)


def _nested_schema(object_levels: int) -> dict:
    """A schema whose root is an object nested *object_levels* deep."""
    node: dict = {"type": "string", "description": "leaf"}
    for _ in range(object_levels):
        node = {"type": "object", "description": "nested", "properties": {"child": node}}
    return node


def test_a_schema_exactly_at_the_depth_cap_is_accepted():
    """Pin the boundary rather than leave the off-by-one implicit."""
    from janasunani.egress.sarvam import MAX_EXTRACT_SCHEMA_DEPTH, _validate_extract_schema

    _validate_extract_schema(_nested_schema(MAX_EXTRACT_SCHEMA_DEPTH))


def test_extract_schema_guard_enforces_the_provider_depth_cap():
    from janasunani.egress.sarvam import MAX_EXTRACT_SCHEMA_DEPTH, _validate_extract_schema

    with pytest.raises(ValueError, match="nests deeper than"):
        _validate_extract_schema(_nested_schema(MAX_EXTRACT_SCHEMA_DEPTH + 1))


def test_a_malformed_nested_field_is_caught_not_just_the_top_level():
    """Codex finding on #232: the provider's field rules apply at every depth.

    Validating only the root lets a nested field with no description through
    the guard and into the HTTP 400 the guard exists to prevent.
    """
    from janasunani.egress.sarvam import _validate_extract_schema

    schema = {
        "type": "object",
        "properties": {
            "petitioner": {
                "type": "object",
                "description": "who filed it",
                "properties": {
                    "name": {"type": "string"},  # no description
                },
            }
        },
    }
    with pytest.raises(ValueError, match="petitioner.name"):
        _validate_extract_schema(schema)


def test_nested_objects_inside_arrays_are_validated_too():
    from janasunani.egress.sarvam import _validate_extract_schema

    schema = {
        "type": "object",
        "properties": {
            "attachments": {
                "type": "array",
                "description": "documents attached to the grievance",
                "items": {
                    "type": "object",
                    "properties": {"kind": {"type": "string"}},  # no description
                },
            }
        },
    }
    with pytest.raises(ValueError, match=r"attachments\[\].kind"):
        _validate_extract_schema(schema)


def test_a_well_formed_nested_schema_is_accepted():
    from janasunani.egress.sarvam import _validate_extract_schema

    _validate_extract_schema(
        {
            "type": "object",
            "properties": {
                "petitioner": {
                    "type": "object",
                    "description": "who filed it",
                    "properties": {
                        "district": {"type": "string", "description": "district name"},
                    },
                }
            },
        }
    )


def _make_dummy_input(tmp_path: Path, n: int = 2) -> Path:
    """Create *n* dummy document files (PNG) under a temp input dir."""
    inp = tmp_path / "input"
    inp.mkdir(parents=True, exist_ok=True)
    # Create minimal PNGs via PIL if available, else empty files.
    try:
        from PIL import Image

        for i in range(n):
            img = Image.new("RGB", (10, 10), color="white")
            img.save(inp / f"TICK{i:03d}_doc.png", format="PNG")
        return inp
    except Exception:
        for i in range(n):
            (inp / f"TICK{i:03d}_doc.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return inp


def test_evaluate_dry_run_digitise_arm(tmp_path: Path):
    from janasunani.evaluation.sarvam_evaluate import main

    inp = _make_dummy_input(tmp_path, n=2)
    out = tmp_path / "out"
    rc = main(["--input", str(inp), "--out", str(out), "--arm", "digitise", "--dry-run"])
    assert rc == 0
    # outputs exist
    json_path = out / "sarvam_scorecard.json"
    md_path = out / "sarvam_scorecard.md"
    assert json_path.is_file()
    assert md_path.is_file()
    data = json.loads(json_path.read_text())
    assert data["n_pages"] == 2
    assert data["arm"] == "digitise"
    assert data["schema_version"] == "v1"
    assert data["cost_rupees"] == 0.0  # dry-run
    assert "summary_divergence" in data
    progress_path = out / "sarvam_progress.json"
    progress = json.loads(progress_path.read_text())
    assert progress["schema_version"] == "janasunani.sarvam-progress/v1"
    assert progress["complete"] is True
    assert progress["pages_processed"] == 2
    assert progress["pages_scored"] == 2
    assert "partial_scorecard" not in progress
    assert progress["privacy"]["contains_page_or_ticket_ids"] is False
    assert progress["privacy"]["contains_text_or_provider_payloads"] is False
    assert progress["privacy"]["contains_category_or_demographic_breakdowns"] is False
    assert progress_path.stat().st_mode & 0o777 == 0o600
    # markdown mentions arm/slice
    md = md_path.read_text()
    assert "Sarvam" in md
    assert "Sambalpur" in md


def test_evaluate_arm_choices_and_schema_version(tmp_path: Path):
    from janasunani.evaluation.sarvam_evaluate import build_parser

    parser = build_parser()
    # valid
    args = parser.parse_args(["--input", "a", "--out", "b", "--arm", "extract", "--schema-version", "v1"])
    assert args.arm == "extract"
    assert args.schema_version == "v1"
    args2 = parser.parse_args(["--input", "a", "--out", "b", "--arm", "both"])
    assert args2.arm == "both"
    # invalid arm
    with pytest.raises(SystemExit):
        parser.parse_args(["--input", "a", "--out", "b", "--arm", "invalid"])
    # invalid schema version
    with pytest.raises(SystemExit):
        parser.parse_args(["--input", "a", "--out", "b", "--schema-version", "v9"])


def test_evaluate_both_arm_cost(tmp_path: Path):
    from janasunani.evaluation.sarvam_evaluate import main

    inp = _make_dummy_input(tmp_path, n=3)
    out = tmp_path / "out2"
    # dry-run cost is 0, but payload records arm
    rc = main(["--input", str(inp), "--out", str(out), "--arm", "both", "--dry-run"])
    assert rc == 0
    data = json.loads((out / "sarvam_scorecard.json").read_text())
    assert data["arm"] == "both"
    # When not dry-run, cost would be 1.50/page; verify helper
    from janasunani.evaluation.sarvam_evaluate import _price_for_arm

    assert _price_for_arm("digitise", 10) == pytest.approx(5.0)
    assert _price_for_arm("extract", 10) == pytest.approx(10.0)
    assert _price_for_arm("both", 10) == pytest.approx(15.0)


def test_progress_checkpoint_contains_only_aggregates(tmp_path: Path):
    from janasunani.evaluation.sarvam_evaluate import _write_progress_checkpoint
    from janasunani.evaluation.sarvam_scorecard import PageRecord

    page = tmp_path / "SECRET-TICKET_document.png"
    page.write_bytes(b"pixels")
    records = [
        PageRecord(
            ticket="SECRET-TICKET",
            page_id="SECRET-TICKET:p1",
            handwritten="printed",
            language="English",
            pytesseract_text="local grievance text",
            sarvam_markdown="remote grievance text",
        )
    ]
    destination = _write_progress_checkpoint(
        out_dir=tmp_path / "out",
        input_snapshot_id="sha256:" + "a" * 64,
        pages_discovered=1,
        pages_processed=1,
        records=records,
        failures=[{"page_id": "SECRET-TICKET:p1", "error": "HTTP402", "arm": "extract"}],
        page_lengths=[
            {"page_id": "SECRET-TICKET:p1", "pytesseract_chars": 20, "sarvam_chars": 21}
        ],
        arm="both",
        schema_version="v1",
        slice_label="Sambalpur/2024",
        dry_run=False,
        complete=False,
    )

    encoded = destination.read_text()
    assert "SECRET-TICKET" not in encoded
    assert "grievance text" not in encoded
    payload = json.loads(encoded)
    assert payload["failure_events"] == 1
    assert payload["failures_by_error"] == {"HTTP402": 1}
    assert payload["paired_exact_divergence_count"] == 1


def test_evaluate_hashes_the_input_snapshot_once_per_run(tmp_path: Path, monkeypatch):
    from janasunani.evaluation import sarvam_evaluate

    inp = _make_dummy_input(tmp_path, n=3)
    out = tmp_path / "out"
    calls = []

    def fake_snapshot(pages):
        calls.append(list(pages))
        return "sha256:" + "b" * 64

    monkeypatch.setattr(sarvam_evaluate, "_input_snapshot_id", fake_snapshot)

    rc = sarvam_evaluate.main(
        ["--input", str(inp), "--out", str(out), "--arm", "digitise", "--dry-run"]
    )

    assert rc == 0
    assert len(calls) == 1
    assert len(calls[0]) == 3
    progress = json.loads((out / "sarvam_progress.json").read_text())
    assert progress["input_snapshot_id"] == "sha256:" + "b" * 64


def test_input_snapshot_binds_content_page_and_ticket_not_mtime(tmp_path: Path):
    from janasunani.evaluation.sarvam_evaluate import _input_snapshot_id

    first = tmp_path / "SECRET-TICKET_first.png"
    renamed = tmp_path / "SECRET-TICKET_copy.png"
    reassigned = tmp_path / "DIFFERENT-TICKET_copy.png"
    first.write_bytes(b"pixel-content-v1")
    renamed.write_bytes(first.read_bytes())
    reassigned.write_bytes(first.read_bytes())

    original = _input_snapshot_id([(first, 1)])
    assert _input_snapshot_id([(renamed, 1)]) == original
    assert _input_snapshot_id([(reassigned, 1)]) != original

    stat = first.stat()
    first.write_bytes(b"pixel-content-v2")
    assert len(b"pixel-content-v1") == len(b"pixel-content-v2")
    first.touch()
    import os

    os.utime(first, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    assert _input_snapshot_id([(first, 1)]) != original
    assert _input_snapshot_id([(renamed, 2)]) != original

    first.write_bytes(b"first-document")
    renamed.write_bytes(b"second-document")
    forward = _input_snapshot_id([(first, 1), (renamed, 1)])
    assert _input_snapshot_id([(renamed, 1), (first, 1)]) == forward


def test_evaluate_join_metadata_from_lake(tmp_path: Path):
    """--join-metadata should populate gold_category from lake complaints parquet."""
    import polars as pl

    inp = _make_dummy_input(tmp_path, n=2)
    # Align ticket ids with input files: TICK000, TICK001
    lake_dir = tmp_path / "lake"
    lake_dir.mkdir()
    complaints = pl.DataFrame(
        {
            "ticket_no": ["TICK000", "TICK001"],
            "district": ["Sambalpur", "Sambalpur"],
            "created_year": [2024, 2024],
            "category": ["Police", "Revenue"],
        }
    )
    complaints.write_parquet(lake_dir / "complaints.parquet")

    out = tmp_path / "out3"
    from janasunani.evaluation.sarvam_evaluate import main

    # Category is only scored for extract/both (digitise-only would fabricate 0% Sarvam)
    rc = main(
        [
            "--input",
            str(inp),
            "--out",
            str(out),
            "--arm",
            "extract",
            "--dry-run",
            "--join-metadata",
            "--lake-dir",
            str(lake_dir),
            "--slice",
            "Sambalpur/2024",
        ]
    )
    assert rc == 0
    data = json.loads((out / "sarvam_scorecard.json").read_text())
    # With gold present, category headline should be computed (not None)
    assert data["category"] is not None
    assert data["category"]["n_tickets"] == 2
    # Digitise-only with same gold should NOT score category (avoids fabricated zero)
    out2 = tmp_path / "out3_digitise"
    rc2 = main(
        [
            "--input",
            str(inp),
            "--out",
            str(out2),
            "--arm",
            "digitise",
            "--dry-run",
            "--join-metadata",
            "--lake-dir",
            str(lake_dir),
            "--slice",
            "Sambalpur/2024",
        ]
    )
    assert rc2 == 0
    data2 = json.loads((out2 / "sarvam_scorecard.json").read_text())
    assert data2["category"] is None


def test_evaluate_extract_arm_with_mocked_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """--arm extract should call adapter.extract(schema=...) and map to PageRecord."""
    inp = _make_dummy_input(tmp_path, n=1)
    out = tmp_path / "out4"

    # Fake adapter that returns a known extract payload
    class FakeAdapter:
        def __init__(self, *args, **kwargs):
            pass

        def digitise(self, *args, **kwargs):
            return "fake markdown"

        def extract(self, doc_bytes, filename, language, context, schema=None, config_id=None):
            assert schema is not None
            # Assert the shape the provider actually accepts. The fake stands
            # in for the transport, so it must hold the same contract the real
            # endpoint does or the mock re-hides the HTTP 400.
            from janasunani.egress.sarvam import _validate_extract_schema

            _validate_extract_schema(schema)
            assert "grievance_category" in schema["properties"]
            return {"grievance_category": "Police", "summary": "Sarvam summary text", "district": "Sambalpur"}

    # Patch the class used by evaluate
    import janasunani.evaluation.sarvam_evaluate as eval_mod

    monkeypatch.setattr(eval_mod, "SarvamVisionAdapter", FakeAdapter, raising=False)
    # Also need to patch the import inside main (it does `from janasunani.egress.sarvam import ...`)
    # So patch the egress module's class as well
    import janasunani.egress.sarvam as egress_mod

    monkeypatch.setattr(egress_mod, "SarvamVisionAdapter", FakeAdapter, raising=False)
    # Patch SqliteAuditLog to avoid creating real DB
    class FakeLog:
        def __init__(self, *a, **kw):
            pass

    monkeypatch.setattr(egress_mod, "SqliteAuditLog", FakeLog, raising=False)
    # Need a fake PROVIDER_REGISTRY that permits egress
    from dataclasses import replace

    from janasunani.egress.sarvam import GovernanceControl, PROVIDER_REGISTRY

    verified = GovernanceControl(statement="test", verified=True)
    fake_route = replace(PROVIDER_REGISTRY["sarvam-vision"], retention_terms=verified, encryption_in_transit=verified, encryption_at_rest=verified)
    monkeypatch.setattr(egress_mod, "PROVIDER_REGISTRY", {"sarvam-vision": fake_route}, raising=False)
    # Patch render_page to return a real PIL image so digitise/extract paths are exercised
    try:
        from PIL import Image

        fake_image = Image.new("RGB", (5, 5))
        monkeypatch.setattr(eval_mod, "render_page", lambda p, n: fake_image, raising=False)
        # Also patch the lazy import path if evaluate imports render_page inside function via import statement;
        # monkeypatch the module where it's imported: janasunani.pipeline.stages.ocr_extraction.page_renderer
        import janasunani.pipeline.stages.ocr_extraction.page_renderer as renderer_mod  # type: ignore

        monkeypatch.setattr(renderer_mod, "render_page", lambda p, n: fake_image, raising=False)
    except Exception:
        pass

    # Run without --dry-run so adapter is used; mock will prevent network
    from janasunani.evaluation.sarvam_evaluate import main

    # We need to ensure the adapter mock is used — the evaluate code does
    # `from janasunani.egress.sarvam import SarvamVisionAdapter` inside main,
    # which after our patch will pick up FakeAdapter.
    rc = main(["--input", str(inp), "--out", str(out), "--arm", "extract"])
    assert rc == 0
    data = json.loads((out / "sarvam_scorecard.json").read_text())
    assert data["arm"] == "extract"
    # The sarvam category/summary should have been mapped from fake payload
    # Scorecard will have at least one ticket with sarvam_category
    assert data["n_pages"] == 1


def test_wrapper_script_delegates(tmp_path: Path):
    """scripts/analysis/sarvam_sample.py thin wrapper should still run dry-run."""
    import subprocess

    inp = _make_dummy_input(tmp_path, n=1)
    out = tmp_path / "out_wrap"
    # Invoke wrapper as module
    result = subprocess.run(
        [sys.executable, "scripts/analysis/sarvam_sample.py", "--input", str(inp), "--out", str(out), "--dry-run"],
        capture_output=True,
        text=True,
    )
    # Should exit 0 and produce outputs (wrapper injects --arm digitise)
    assert result.returncode == 0, f"wrapper failed: {result.stderr}\n{result.stdout}"
    assert (out / "sarvam_scorecard.json").is_file()


def test_objects_inside_nested_arrays_are_validated():
    """Codex follow-up on #232: an array of arrays stopped the traversal.

    The first `items` had type "array", not "object", so the walk gave up and
    everything below it bypassed both field validation and the depth cap.
    """
    from janasunani.egress.sarvam import _validate_extract_schema

    schema = {
        "type": "object",
        "properties": {
            "pages": {
                "type": "array",
                "description": "pages, each holding a list of line items",
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"line": {"type": "string"}},  # no description
                    },
                },
            }
        },
    }
    with pytest.raises(ValueError, match=r"pages\[\]\[\].line"):
        _validate_extract_schema(schema)


def test_arrays_count_toward_the_depth_cap():
    """Each array level is one the provider descends too."""
    from janasunani.egress.sarvam import _validate_extract_schema

    items: dict = {"type": "object", "properties": {"leaf": {"type": "string", "description": "x"}}}
    for _ in range(6):
        items = {"type": "array", "items": items}

    schema = {
        "type": "object",
        "properties": {"deep": {"type": "array", "description": "d", "items": items}},
    }
    with pytest.raises(ValueError, match="nests deeper than"):
        _validate_extract_schema(schema)


def test_a_well_formed_array_of_objects_is_accepted():
    from janasunani.egress.sarvam import _validate_extract_schema

    _validate_extract_schema(
        {
            "type": "object",
            "properties": {
                "attachments": {
                    "type": "array",
                    "description": "documents attached",
                    "items": {
                        "type": "object",
                        "properties": {"kind": {"type": "string", "description": "kind"}},
                    },
                }
            },
        }
    )


def _array_chain_schema(array_levels: int) -> dict:
    """A schema whose root field is *array_levels* deep in arrays, ending in a primitive.

    Unlike ``_nested_schema``, the chain never bottoms out in an object, so
    only the depth check reachable from a primitive terminal can catch an
    over-depth schema built this way.
    """
    node: dict = {"type": "string", "description": "leaf"}
    for _ in range(array_levels):
        node = {"type": "array", "description": "list", "items": node}
    return {"type": "object", "properties": {"root": node}}


def test_an_array_chain_exactly_at_the_depth_cap_ending_in_a_primitive_is_accepted():
    """Pin the boundary for the array-only path, same as the object path."""
    from janasunani.egress.sarvam import MAX_EXTRACT_SCHEMA_DEPTH, _validate_extract_schema

    _validate_extract_schema(_array_chain_schema(MAX_EXTRACT_SCHEMA_DEPTH - 1))


def test_extract_schema_guard_catches_an_over_depth_array_chain_ending_in_a_primitive():
    """Codex finding on #232: the loop checks depth before each descent, never after.

    A chain of arrays that bottoms out in a primitive (not an object) never
    takes the ``isinstance(items, dict) and items.get("type") == "object"``
    branch, so before this fix nothing checked the level reached by the
    final descent and an over-depth schema like this one reached the
    provider as an HTTP 400.
    """
    from janasunani.egress.sarvam import MAX_EXTRACT_SCHEMA_DEPTH, _validate_extract_schema

    with pytest.raises(ValueError, match="nests deeper than"):
        _validate_extract_schema(_array_chain_schema(MAX_EXTRACT_SCHEMA_DEPTH))


# --- Table-driven depth accounting ------------------------------------------
#
# Three off-by-ones in the depth accounting have now reached review in a row,
# each "fixed" by adding another check on top of the last. The accounting was
# rewritten instead (see the module comment above `_validate_object` in
# `janasunani/egress/sarvam.py`), and this table is what pins it down: every
# shape below states the level its deepest node reaches, so the boundary is
# asserted, not inferred from a single accept/reject pair.
#
# Level, per that module comment: the root schema object is level 1; every
# object property, every array `items` step (including array-of-array), and
# the object a chain of arrays finally bottoms out in each add one level. A
# primitive field is a leaf and does not itself occupy a level.


def _object_chain(levels: int) -> dict:
    """root + nested objects, deepest level == *levels*."""
    return _nested_schema(levels)


def _array_chain_primitive(levels: int) -> dict:
    """root + nested arrays ending in a primitive, deepest level == *levels*."""
    return _array_chain_schema(levels - 1)


def _array_chain_object(array_levels: int) -> dict:
    """root -> *array_levels* nested arrays -> a terminal well-formed object.

    Deepest level == *array_levels* + 2: one for the root, one per array, and
    one more for the terminal object the innermost array's ``items``
    descends into -- the exact descent Codex's finding on #232 showed was
    not being counted (the walk reached this object with the innermost
    array's level, unchanged).
    """
    node: dict = {
        "type": "object",
        "properties": {"leaf": {"type": "string", "description": "x"}},
    }
    for _ in range(array_levels):
        node = {"type": "array", "description": "list", "items": node}
    return {"type": "object", "properties": {"root": node}}


def _mixed_chain_object_array_object() -> dict:
    """root -> object -> array -> terminal object. Deepest level 4."""
    return {
        "type": "object",
        "properties": {
            "petitioner": {
                "type": "object",
                "description": "who filed it",
                "properties": {
                    "attachments": {
                        "type": "array",
                        "description": "documents",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string", "description": "kind"},
                            },
                        },
                    }
                },
            }
        },
    }


def _mixed_chain_array_object_array_array_primitive() -> dict:
    """root -> array -> object -> array -> array -> primitive. Deepest level 5."""
    return {
        "type": "object",
        "properties": {
            "pages": {
                "type": "array",
                "description": "pages, each an object with a grid of line segments",
                "items": {
                    "type": "object",
                    "properties": {
                        "lines": {
                            "type": "array",
                            "description": "rows of line segments on the page",
                            "items": {
                                "type": "array",
                                "description": "line segments in a row",
                                "items": {"type": "string", "description": "segment text"},
                            },
                        }
                    },
                },
            }
        },
    }


DEPTH_ACCOUNTING_CASES = [
    # (label, schema, deepest_level, accepted)
    ("root + 4 nested objects", _object_chain(4), 4, True),
    ("root + 5 nested objects", _object_chain(5), 5, False),
    ("root + 3 nested arrays -> primitive", _array_chain_primitive(4), 4, True),
    ("root + 4 nested arrays -> primitive", _array_chain_primitive(5), 5, False),
    ("root + 2 nested arrays -> terminal object", _array_chain_object(2), 4, True),
    (
        "root + 3 nested arrays -> terminal object (Codex #232 repro)",
        _array_chain_object(3),
        5,
        False,
    ),
    ("mixed: root -> object -> array -> object", _mixed_chain_object_array_object(), 4, True),
    (
        "mixed: root -> array -> object -> array -> array -> primitive",
        _mixed_chain_array_object_array_array_primitive(),
        5,
        False,
    ),
]


@pytest.mark.parametrize(
    "label, schema, deepest_level, accepted",
    DEPTH_ACCOUNTING_CASES,
    ids=[case[0] for case in DEPTH_ACCOUNTING_CASES],
)
def test_depth_accounting_table(label, schema, deepest_level, accepted):
    """Pin the level each schema shape reaches, not just accept/reject.

    ``deepest_level`` is asserted against ``MAX_EXTRACT_SCHEMA_DEPTH`` here so
    that a future change to the cap makes the boundary this table encodes
    explicit, rather than the table quietly agreeing with whatever the code
    now does -- which is how the previous three off-by-ones went unnoticed.
    """
    from janasunani.egress.sarvam import MAX_EXTRACT_SCHEMA_DEPTH, _validate_extract_schema

    assert (deepest_level <= MAX_EXTRACT_SCHEMA_DEPTH) == accepted, label
    if accepted:
        _validate_extract_schema(schema)
    else:
        with pytest.raises(ValueError, match="nests deeper than"):
            _validate_extract_schema(schema)


def test_codex_232_repro_root_three_arrays_terminal_object_is_rejected():
    """Exact reproduction from the review finding: must be REJECTED at cap 4.

    Before this fix, the array walk reached the terminal object passing
    ``level`` unchanged from the innermost array's level (4), so this
    schema -- whose terminal object is actually the fifth schema level --
    was wrongly accepted; a direct reproduction printed
    ``ACCEPTED root + 3 arrays + terminal object``. Fixed by having
    ``_validate_array`` recurse into ``_validate_object`` at ``level + 1``,
    the same rule it already used for array-of-array.
    """
    from janasunani.egress.sarvam import _validate_extract_schema

    with pytest.raises(ValueError, match="nests deeper than"):
        _validate_extract_schema(_array_chain_object(3))


# --- Field type allowlist ----------------------------------------------------
#
# Codex finding on #232: the field-level check only asked whether ``type`` was
# present (``if not field_type``), not whether it was one of the types Sarvam
# actually supports. A truthy-but-invalid value like ``"nonsense"`` therefore
# passed local validation and would have reached the provider as the exact
# HTTP 400 this guard exists to prevent. This had already been patched three
# times over for depth-accounting bugs in this same function, each patch
# adding one more special case on top of the last, so the fix here is an
# allowlist (``SUPPORTED_EXTRACT_FIELD_TYPES``) rather than a fourth patch: a
# denylist of known-bad values can always miss a new one, an allowlist cannot.


def test_extract_schema_guard_rejects_an_unsupported_field_type():
    """Reproduces the finding's exact example against the pre-fix code.

    Direct reproduction against the unpatched guard: ``_validate_extract_schema``
    on ``{"type": "object", "properties": {"field": {"type": "nonsense",
    "description": "x"}}}`` printed "unknown root field type ACCEPTED" --
    confirming a caller-supplied schema with an invalid type sailed through
    the guard meant to catch exactly this before any bytes leave the box.
    """
    from janasunani.egress.sarvam import _validate_extract_schema

    schema = {
        "type": "object",
        "properties": {"field": {"type": "nonsense", "description": "x"}},
    }
    with pytest.raises(ValueError, match="unsupported type"):
        _validate_extract_schema(schema)


def test_every_supported_extract_field_type_is_accepted():
    """The allowlist's positive half: every type Sarvam's docs list must still pass.

    Types confirmed against https://docs.sarvam.ai/api/api-guides-tutorials/
    document-intelligence/overview: string, number, integer, boolean, object,
    array. An allowlist that silently drifted narrower than what the provider
    actually accepts would be its own outage, so this is asserted rather than
    left implicit in the constant alone.
    """
    from janasunani.egress.sarvam import SUPPORTED_EXTRACT_FIELD_TYPES, _validate_extract_schema

    assert SUPPORTED_EXTRACT_FIELD_TYPES == {
        "string",
        "number",
        "integer",
        "boolean",
        "object",
        "array",
    }
    for field_type in sorted(SUPPORTED_EXTRACT_FIELD_TYPES):
        field: dict = {"type": field_type, "description": f"a {field_type} field"}
        if field_type == "object":
            # An object field must itself carry a non-empty properties map --
            # a separate, already-covered rule, not part of this guard.
            field["properties"] = {"child": {"type": "string", "description": "child field"}}
        _validate_extract_schema({"type": "object", "properties": {"field": field}})


def test_unwrap_extract_result_reads_the_live_result_envelope():
    """GET /doc-ai/v1/job/{id}/results nests the schema fields under `result`.

    Regression for the 2026-08-25 Sambalpur/2024 run: the unwrapper knew
    `results` (plural list) and `data` but not `result` (singular), so it fell
    through to returning the envelope. The caller's
    `payload.get("grievance_category")` then read one level too high, every
    `sarvam_category` recorded as null, and the scorecard reported 0.000
    accuracy for a measurement that had never been taken.
    """
    from janasunani.evaluation.sarvam_evaluate import _unwrap_extract_result

    envelope = {
        "job_id": "01a036cc",
        "type": "extract",
        "status": "completed",
        "usage": {"pages": 1},
        "version": "1",
        "annotations": {"grievance_category": {"confidence": 0.9}},
        "source_map": {},
        "result": {
            "grievance_category": "Social Welfare",
            "summary": "Pension not disbursed.",
            "district": "Sambalpur",
            "grievance_text": "...",
        },
    }

    unwrapped = _unwrap_extract_result(envelope)

    assert unwrapped["grievance_category"] == "Social Welfare"
    assert unwrapped["district"] == "Sambalpur"
    # The envelope's own keys must not leak through as if they were fields.
    assert "annotations" not in unwrapped
    assert "job_id" not in unwrapped


def test_unwrap_extract_result_keeps_the_other_known_shapes():
    from janasunani.evaluation.sarvam_evaluate import _unwrap_extract_result

    assert _unwrap_extract_result({"results": [{"district": "Sambalpur"}]}) == {
        "district": "Sambalpur"
    }
    assert _unwrap_extract_result({"data": {"district": "Sambalpur"}}) == {
        "district": "Sambalpur"
    }
    assert _unwrap_extract_result([{"district": "Sambalpur"}]) == {"district": "Sambalpur"}
    assert _unwrap_extract_result({"district": "Sambalpur"}) == {"district": "Sambalpur"}
    assert _unwrap_extract_result(None) == {}


def test_unwrap_extract_result_prefers_result_over_a_stale_sibling():
    """`result` wins: a response carrying both must not silently pick the other."""
    from janasunani.evaluation.sarvam_evaluate import _unwrap_extract_result

    both = {
        "result": {"grievance_category": "Housing"},
        "data": {"grievance_category": "WRONG"},
    }
    assert _unwrap_extract_result(both)["grievance_category"] == "Housing"


def test_save_records_destination_must_be_inside_the_data_tree(tmp_path):
    """The dump is unredacted citizen text, so the destination is not free-form.

    Codex P1 on #309: an operator passing `--save-records records.jsonl` or a
    path under `docs/` got the file created without complaint, leaving
    unredacted grievance text somewhere git was watching.
    """
    import pytest

    from janasunani.config import DATA_DIR
    from janasunani.evaluation.sarvam_evaluate import _checked_record_destination

    inside = DATA_DIR / "external" / "run" / "records.jsonl"
    assert _checked_record_destination(inside) == inside.resolve()

    for bad in (
        Path("records.jsonl"),
        Path("docs/records.jsonl"),
        DATA_DIR / ".." / "docs" / "records.jsonl",
        tmp_path / "records.jsonl",
    ):
        with pytest.raises(ValueError, match="governed data tree"):
            _checked_record_destination(bad)


def test_save_records_destination_rejects_traversal_after_resolution():
    """`data/../docs/x` must not pass on the strength of its prefix."""
    import pytest

    from janasunani.config import DATA_DIR
    from janasunani.evaluation.sarvam_evaluate import _checked_record_destination

    escaped = DATA_DIR / "external" / ".." / ".." / "docs" / "leak.jsonl"
    with pytest.raises(ValueError, match="governed data tree"):
        _checked_record_destination(escaped)
