import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from janasunani.evaluation.actionability import ActionabilityRecord
from janasunani.evaluation.embedding_actionability import (
    LocalEncoderSpec,
    TransformersMeanPoolEncoder,
    benchmark_frozen_encoder,
    binary_discrimination_metrics,
    resolve_cached_huggingface_snapshot,
    resolve_local_model_dir,
)
from scripts.benchmark_actionability_candidates import (
    _assert_aggregate_only,
    _parse_named_path,
)


class FakeEncoder:
    @property
    def provenance(self):
        return {
            "name": "fake-multilingual",
            "role": "multilingual_candidate",
            "local_path": "/local/fake",
            "revision": "deadbeef",
            "artifact_fingerprint": "sha256:fake",
            "frozen": True,
            "local_files_only": True,
        }

    def encode(self, texts):
        return np.asarray(
            [
                [
                    float("review" in text),
                    float("actionable" in text),
                    (sum(map(ord, text)) % 17) / 17,
                ]
                for text in texts
            ]
        )


def _records():
    rows = []
    for split, n in (("train", 16), ("validation", 8), ("test", 8)):
        for index in range(n):
            actionable = index % 2 == 0
            label = "actionable" if actionable else "underspecified"
            rows.append(
                ActionabilityRecord(
                    item_id=f"{split}-{index}",
                    redacted_text=(
                        f"actionable water request {index}"
                        if actionable
                        else f"review missing details {index}"
                    ),
                    label=label,
                    group_id=f"{split}-{index}",
                    language="Odia" if index % 3 == 0 else "English",
                    split=split,
                    label_source="frontier_adjudicated",
                )
            )
    return rows


def test_frozen_encoder_benchmark_is_aggregate_and_validation_selected():
    records = _records()
    benchmark = benchmark_frozen_encoder(
        records,
        FakeEncoder(),
        c_values=(0.1, 1.0),
        min_review_precision=0.0,
        max_actionable_review_rate=1.0,
    )

    report = benchmark.report
    assert report["encoder"]["frozen"] is True
    assert report["encoder"]["local_files_only"] is True
    assert report["selected_c"] in {0.1, 1.0}
    assert report["split_counts"] == {"train": 16, "validation": 8, "test": 8}
    assert len(report["test"]["accuracy_ci"]) == 2
    assert set(report["test_by_language"]) == {"English", "Odia"}
    serialized = json.dumps(report)
    assert "actionable water request" not in serialized
    assert "review missing details" not in serialized

    flipped_test = [
        ActionabilityRecord(
            **{
                **row.__dict__,
                "label": (
                    "underspecified"
                    if row.split == "test" and row.label == "actionable"
                    else (
                        "actionable"
                        if row.split == "test"
                        else row.label
                    )
                ),
            }
        )
        for row in records
    ]
    changed = benchmark_frozen_encoder(
        flipped_test,
        FakeEncoder(),
        c_values=(0.1, 1.0),
        min_review_precision=0.0,
        max_actionable_review_rate=1.0,
    )
    assert changed.report["selected_c"] == report["selected_c"]
    assert changed.review_threshold == report["review_threshold"]
    assert changed.report["validation"] == report["validation"]


def test_missing_local_model_fails_without_download(tmp_path):
    missing = tmp_path / "missing-model"
    with pytest.raises(FileNotFoundError, match="will not download"):
        resolve_local_model_dir(missing)

    with pytest.raises(FileNotFoundError, match="will not download"):
        resolve_cached_huggingface_snapshot(
            "google/muril-base-cased", cache_dir=tmp_path
        )


def test_encoder_output_must_be_aligned_and_finite():
    class BrokenEncoder(FakeEncoder):
        def encode(self, texts):
            return np.full((len(texts) - 1, 2), np.nan)

    with pytest.raises(ValueError, match="invalid train matrix shape"):
        benchmark_frozen_encoder(_records(), BrokenEncoder(), c_values=(1.0,))


def test_discrimination_metrics_validate_alignment():
    test_records = [record for record in _records() if record.split == "test"]
    metrics = binary_discrimination_metrics(
        [0.1 if record.label == "actionable" else 0.9 for record in test_records],
        test_records,
    )
    assert metrics == {
        "roc_auc": 1.0,
        "average_precision": 1.0,
        "brier_score": pytest.approx(0.01),
    }
    with pytest.raises(ValueError, match="aligned non-empty"):
        binary_discrimination_metrics([], test_records)


def test_candidate_parser_and_aggregate_guard(tmp_path):
    name, path = _parse_named_path(f"muril={tmp_path}")
    assert name == "muril"
    assert path == tmp_path
    with pytest.raises(Exception, match="NAME=LOCAL_PATH"):
        _parse_named_path("muril")
    with pytest.raises(ValueError, match="forbidden text keys"):
        _assert_aggregate_only({"nested": {"redacted_text": "secret"}})


def test_encoder_spec_records_role_without_loading_model(tmp_path):
    spec = LocalEncoderSpec(
        name="muril",
        model_path=Path(tmp_path),
        role="multilingual_candidate",
    )
    assert spec.role == "multilingual_candidate"


def test_encoder_provenance_keeps_portable_configured_path(monkeypatch):
    import janasunani.evaluation.embedding_actionability as embedding

    configured_path = Path("models/categorizer")
    resolved_path = Path("/private/worktree/models/categorizer")
    monkeypatch.setattr(
        embedding, "resolve_local_model_dir", lambda _path: resolved_path
    )
    monkeypatch.setattr(
        embedding, "directory_fingerprint", lambda _path: ("sha256:test", 1)
    )

    torch = ModuleType("torch")
    torch.device = lambda value: value
    transformers = ModuleType("transformers")

    class _Model:
        config = SimpleNamespace(model_type="bert", architectures=["BertModel"])

        def to(self, _device):
            return self

        def eval(self):
            return None

        def parameters(self):
            return []

    transformers.AutoTokenizer = SimpleNamespace(
        from_pretrained=lambda *_args, **_kwargs: object()
    )
    transformers.AutoModel = SimpleNamespace(
        from_pretrained=lambda *_args, **_kwargs: _Model()
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "transformers", transformers)

    encoder = TransformersMeanPoolEncoder(
        LocalEncoderSpec(
            name="muril",
            model_path=configured_path,
            role="multilingual_candidate",
        )
    )

    assert encoder.provenance["local_path"] == "models/categorizer"
