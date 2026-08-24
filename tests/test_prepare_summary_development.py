import json

from scripts.prepare_summary_development import (
    Candidate,
    _read_candidates,
    prepare_review,
    select_candidates,
)


def candidate(index: int, *, category: str, split: str = "test", words: int = 30):
    return Candidate(
        item_id=f"item-{index}",
        group_id=f"group-{index}",
        redacted_text=" ".join(f"word{part}" for part in range(words)),
        category=category,
        split=split,
        language="unknown_not_adjudicated",
        source_kind="historical_typed_redacted",
    )


def test_selection_is_deterministic_group_disjoint_and_category_enriched():
    rows = [candidate(index, category=f"category-{index % 4}") for index in range(40)]
    first = select_candidates(rows, sample_size=12, is_english=lambda text: True)
    second = select_candidates(list(reversed(rows)), sample_size=12, is_english=lambda text: True)

    assert [(row.item_id, cohort) for row, cohort in first] == [
        (row.item_id, cohort) for row, cohort in second
    ]
    assert len({row.group_id for row, _ in first}) == 12
    assert {row.category for row, _ in first} == {f"category-{index}" for index in range(4)}


def test_input_loader_is_strict(tmp_path):
    path = tmp_path / "dataset.jsonl"
    path.write_text(json.dumps({"item_id": "x", "redacted_text": "safe"}) + "\n")

    try:
        _read_candidates(path)
    except ValueError as exc:
        assert "schema" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("invalid input was accepted")


def test_prepare_review_keeps_narratives_private_and_provenance_aggregate(tmp_path):
    rows = [candidate(index, category=f"category-{index % 2}") for index in range(8)]
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        "".join(json.dumps(row.__dict__) + "\n" for row in rows), encoding="utf-8"
    )
    model_path = tmp_path / "model"
    model_path.mkdir()
    (model_path / "model.safetensors").write_bytes(b"weights")

    class FakeSummarizer:
        def __init__(self, path):
            assert path == model_path

        def summarize(self, text):
            return "private candidate"

    review = tmp_path / "private" / "review.jsonl"
    provenance = tmp_path / "tracked" / "provenance.json"
    payload = prepare_review(
        dataset=dataset,
        private_review=review,
        provenance=provenance,
        model_path=model_path,
        split="test",
        sample_size=6,
        summarizer_factory=FakeSummarizer,
        is_english=lambda text: True,
        runtime_environment={
            "device": "test-cpu",
            "python": "test-python",
            "torch": "test-torch",
            "transformers": "test-transformers",
        },
    )

    assert "private candidate" in review.read_text()
    rendered_provenance = provenance.read_text()
    assert "private candidate" not in rendered_provenance
    assert "word0" not in rendered_provenance
    assert payload["publication_ready"] is False
    assert review.stat().st_mode & 0o777 == 0o600
