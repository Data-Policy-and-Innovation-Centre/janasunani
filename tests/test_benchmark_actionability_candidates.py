from types import SimpleNamespace

from scripts import benchmark_actionability_candidates as benchmark


class _Classifier:
    classes_ = ("actionable", "review_required")

    def predict_proba(self, texts):
        return [[1.0, 0.0] for _ in texts]


def test_report_metadata_is_stable_for_dvc(monkeypatch, tmp_path):
    gold = tmp_path / "gold.jsonl"
    gold.write_text("{}\n", encoding="utf-8")
    records = [
        SimpleNamespace(
            split="validation",
            label="actionable",
            label_source="frontier_adjudicated",
            redacted_text="example",
        ),
        SimpleNamespace(
            split="test",
            label="underspecified",
            label_source="frontier_adjudicated",
            redacted_text="example",
        ),
    ]
    report = {
        "validation": {
            "review_recall": 1.0,
            "review_precision": 1.0,
            "accuracy": 1.0,
        }
    }
    monkeypatch.setattr(benchmark, "load_jsonl", lambda _path: records)
    monkeypatch.setattr(
        benchmark,
        "benchmark_binary_review",
        lambda *_args, **_kwargs: SimpleNamespace(
            classifier=_Classifier(), report=report.copy()
        ),
    )
    monkeypatch.setattr(
        benchmark, "binary_discrimination_metrics", lambda *_args: {}
    )
    monkeypatch.setattr(
        benchmark,
        "sample_design_summary",
        lambda _records: {"limitation": "development sample"},
    )
    monkeypatch.setattr(benchmark.importlib.metadata, "version", lambda _name: "1")

    first = benchmark.build_report(
        gold_path=gold,
        encoder_specs=[],
        c_values=[1.0],
        min_review_precision=0.6,
        max_actionable_review_rate=0.1,
    )
    second = benchmark.build_report(
        gold_path=gold,
        encoder_specs=[],
        c_values=[1.0],
        min_review_precision=0.6,
        max_actionable_review_rate=0.1,
    )

    assert first == second
    assert "created_at" not in first
    assert "platform" not in first["software"]
    assert first["gold"]["path"] == gold.as_posix()
