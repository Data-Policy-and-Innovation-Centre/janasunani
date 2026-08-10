"""DeepSeek-OCR backend.

Loads a pinned local DeepSeek-OCR artifact and runs inference
via `model.infer()`. Requires CUDA — fails fast at model load time
if a GPU isn't available, with a clear message pointing to pytesseract
as the CPU alternative.

The model itself is multi-gigabyte and takes a noticeable time to load. This
module never auto-loads it, and a mutable public Hugging Face ID is accepted
only with the explicit development-only remote-model opt-in.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from PIL import Image
from janasunani.tracking.artifacts import remote_models_allowed, resolve_artifact

# Default model ID. Override by passing a different name to load_model().
DEFAULT_MODEL_NAME = "deepseek-ai/DeepSeek-OCR"

# Prompt template that asks for free-form OCR. The model is multilingual;
# explicitly naming the expected languages helps in mixed-script documents.
DEFAULT_PROMPT = "<image>\nFree OCR. Oriya or English."


def _resolve_model_source(model_name: str | None = None) -> tuple[str, bool]:
    if model_name is not None:
        candidate = Path(model_name)
        if candidate.exists():
            return str(candidate), True
        if remote_models_allowed():
            return model_name, False
        raise RuntimeError(
            "remote DeepSeek model IDs require JANASUNANI_ALLOW_REMOTE_MODELS=1"
        )
    artifact = resolve_artifact("deepseek_ocr")
    if artifact is not None:
        return str(artifact), True
    if remote_models_allowed():
        return DEFAULT_MODEL_NAME, False
    raise RuntimeError(
        "no local DeepSeek OCR artifact resolved; materialize the approved release "
        "or set JANASUNANI_ALLOW_REMOTE_MODELS=1 for development only"
    )


def load_model(model_name: str | None = None) -> tuple[Any, Any]:
    """Load the DeepSeek-OCR tokenizer and model. Returns (tokenizer, model).

    Raises a clear error if CUDA isn't available, since this model is
    GPU-only in practice (the original code calls .cuda() unconditionally).
    """
    source, local_only = _resolve_model_source(model_name)

    # Import torch/transformers lazily so importing this module doesn't
    # fail on systems without those deps installed.
    import torch
    from transformers import AutoModel, AutoTokenizer, LlamaTokenizer

    if not torch.cuda.is_available():
        raise RuntimeError(
            "DeepSeek-OCR requires a CUDA-capable GPU. "
            "Use --ocr-engine pytesseract for CPU-only inference."
        )

    # The original code preferred LlamaTokenizer and fell back to AutoTokenizer.
    # Keep that order.
    try:
        tokenizer = LlamaTokenizer.from_pretrained(
            source, trust_remote_code=True, local_files_only=local_only
        )
    except Exception:
        tokenizer = AutoTokenizer.from_pretrained(
            source,
            trust_remote_code=True,
            use_fast=False,
            local_files_only=local_only,
        )

    model = AutoModel.from_pretrained(
        source,
        attn_implementation="eager",
        trust_remote_code=True,
        use_safetensors=True,
        torch_dtype=torch.bfloat16,
        local_files_only=local_only,
    )
    model = model.to("cuda").eval()

    # Pad / eos token housekeeping — silences a runtime warning and avoids
    # generation hanging waiting for an eos that was never set.
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id

    if getattr(model, "generation_config", None) is not None:
        model.generation_config.pad_token_id = tokenizer.pad_token_id
        if model.generation_config.eos_token_id is None:
            model.generation_config.eos_token_id = tokenizer.eos_token_id
        # Settings copied from the original gen_tokenizer()
        model.generation_config.repetition_penalty = 1.2
        model.generation_config.no_repeat_ngram_size = 3
        model.generation_config.temperature = 0.7

    return tokenizer, model


def extract_text(
    image: Image.Image,
    tokenizer: Any,
    model: Any,
    prompt: str = DEFAULT_PROMPT,
) -> str:
    """Run DeepSeek-OCR on a single PIL image.

    The model's infer() helper takes a file path rather than a PIL object,
    so we round-trip through a temp PNG. This is the same pattern the
    original code uses.
    """
    tmp = NamedTemporaryFile(suffix=".png", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()

    try:
        image.save(tmp_path, format="PNG")
        text = model.infer(
            tokenizer,
            prompt=prompt,
            image_file=str(tmp_path),
            output_path=str(tmp_path.parent),
            base_size=1024,
            image_size=640,
            crop_mode=True,
            test_compress=False,
            save_results=False,
            eval_mode=True,
        )
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    return text or ""
