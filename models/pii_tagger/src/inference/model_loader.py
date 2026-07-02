"""Module to load the trained PII Tagger model."""
 
import json
from pathlib import Path
 
import torch
from transformers import AutoTokenizer, PreTrainedModel, PreTrainedTokenizerBase
 
from PII_tagger.src.models.pii_model import build_transformer_crf
 
 
def load_pii_model(
    output_dir: str = "../outputs",
    model_name: str = "xlm-roberta-base",
) -> tuple[PreTrainedModel, PreTrainedTokenizerBase, dict[str, int]]:
    """Load a trained Transformer + CRF PII model.
 
    Args:
        output_dir (str): Directory containing model files
                          (tokenizer/, tag2id.json, pii_crf_model.pt).
        model_name (str): HuggingFace model name used during training.
 
    Returns:
        model: PyTorch model loaded with weights.
        tokenizer: HuggingFace tokenizer.
        tag2id (dict): Label → ID mapping.
    """
    tag2id_path = Path(output_dir) / "tag2id.json"
    with tag2id_path.open() as f:
        tag2id = json.load(f)
 
    tokenizer_path = Path(output_dir) / "tokenizer"
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)
 
    model = build_transformer_crf(
        model_name=model_name,
        num_labels=len(tag2id),
        class_weights=None,
    )
 
    model_path = Path(output_dir) / "pii_crf_model.pt"
    state = torch.load(model_path, map_location="cpu")
    model.load_state_dict(state)
    model.eval()
 
    return model, tokenizer, tag2id