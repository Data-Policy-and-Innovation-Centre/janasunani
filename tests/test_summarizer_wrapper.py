"""Real-code-path tests for the warm Summarizer wrapper.

The HF model is mocked out (via monkeypatching `_load_model`/`_summarize_text`)
so these tests never download or run a real model - they exercise the actual
Summarizer class logic: loading once, threading cached handles, and the
short-input guard.
"""

import pytest

from janasunani.pipeline.stages import summarizer as summarizer_module
from janasunani.pipeline.stages.summarizer import (
    ALLOW_REMOTE_MODELS_ENV_VAR,
    MIN_SUMMARY_LENGTH,
    MODEL_NAME,
    Summarizer,
    _resolve_model_source,
)


def test_summarizer_requires_local_artifact_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("JANASUNANI_MODELS_DIR", str(tmp_path / "missing"))
    monkeypatch.delenv("JANASUNANI_SUMMARIZER_ARTIFACT", raising=False)
    monkeypatch.delenv(ALLOW_REMOTE_MODELS_ENV_VAR, raising=False)

    with pytest.raises(RuntimeError, match="no local summarizer artifact"):
        _resolve_model_source()


def test_summarizer_remote_model_is_explicit_development_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("JANASUNANI_MODELS_DIR", str(tmp_path / "missing"))
    monkeypatch.delenv("JANASUNANI_SUMMARIZER_ARTIFACT", raising=False)
    monkeypatch.setenv(ALLOW_REMOTE_MODELS_ENV_VAR, "1")

    assert _resolve_model_source() == (MODEL_NAME, False)


def test_summarize_loads_model_once_and_threads_cached_handles(monkeypatch):
    load_calls = []

    def fake_load_model():
        load_calls.append(1)
        return "fake-tokenizer", "fake-model", "fake-torch", "fake-device"

    summarize_calls = []

    def fake_summarize_text(text, tokenizer, model, torch, device):
        summarize_calls.append((text, tokenizer, model, torch, device))
        return "the summary"

    monkeypatch.setattr(summarizer_module, "_load_model", fake_load_model)
    monkeypatch.setattr(summarizer_module, "_summarize_text", fake_summarize_text)

    long_text = " ".join(f"word{i}" for i in range(MIN_SUMMARY_LENGTH + 5))

    wrapper = Summarizer()
    assert load_calls == [1]  # model loaded exactly once, in __init__

    result = wrapper.summarize(long_text)

    assert result == "the summary"
    assert summarize_calls == [
        (long_text, "fake-tokenizer", "fake-model", "fake-torch", "fake-device")
    ]

    # A second call reuses the same cached handles without reloading.
    wrapper.summarize(long_text)
    assert load_calls == [1]
    assert len(summarize_calls) == 2


def test_summarize_short_input_guard_skips_model_call(monkeypatch):
    load_calls = []

    def fake_load_model():
        load_calls.append(1)
        return "fake-tokenizer", "fake-model", "fake-torch", "fake-device"

    def fake_summarize_text(*args, **kwargs):
        raise AssertionError("_summarize_text should not be called for short input")

    monkeypatch.setattr(summarizer_module, "_load_model", fake_load_model)
    monkeypatch.setattr(summarizer_module, "_summarize_text", fake_summarize_text)

    wrapper = Summarizer()
    short_text = "too   short\nto summarize"

    result = wrapper.summarize(short_text)

    # Flattened (whitespace-collapsed) version of the short input is returned.
    assert result == "too short to summarize"
