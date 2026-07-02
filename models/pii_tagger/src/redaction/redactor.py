"""Single-file PII redaction utilities for text files."""
 
import logging
import textwrap
from pathlib import Path
 
import torch
from transformers import PreTrainedModel, PreTrainedTokenizerBase
 
from PII_tagger.src.redaction.text_utils import (
    normalize_text,
    preprocess_words,
    redact_words,
)
 
logger = logging.getLogger(__name__)
 
 
def tag_text(
    text: str,
    model: PreTrainedModel,
    tokenizer: PreTrainedTokenizerBase,
    id2tag: dict[int, str],
    max_len: int = 128,
    debug: bool = False,
) -> list[tuple[str, str]]:
    """Run CRF tagging on a single text block.
 
    Args:
        text: Text to tag
        model: Trained PII model
        tokenizer: Model tokenizer
        id2tag: ID to label mapping
        max_len: Maximum token length
        debug: Whether to log debug information
 
    Returns:
        List of (word, tag) tuples
    """
    device = next(model.parameters()).device
 
    text = normalize_text(text)
    words = preprocess_words(text)
 
    if not words:
        return []
 
    enc = tokenizer(
        words,
        is_split_into_words=True,
        return_tensors="pt",
        max_length=max_len,
        truncation=True,
        padding="max_length",
    )
 
    input_ids = enc["input_ids"].to(device)
    attention_mask = enc["attention_mask"].to(device)
    word_ids = enc.word_ids()
 
    model.eval()
    with torch.no_grad():
        decoded = model(input_ids, attention_mask)[0]
 
    preds = []
    prev_word_id = None
    sub_i = 0
 
    for wid in word_ids:
        if wid is None:
            sub_i += 1
            continue
        if wid != prev_word_id:
            preds.append(id2tag[decoded[sub_i]])
        prev_word_id = wid
        sub_i += 1
 
    if debug:
        logger.debug(f"WORDS: {words}")
        logger.debug(f"PREDICTIONS: {preds}")
 
    return list(zip(words, preds))
 
 
def redact_directory(
    input_dir: str,
    output_dir: str,
    model: PreTrainedModel,
    tokenizer: PreTrainedTokenizerBase,
    tag2id: dict,
    max_len: int = 128,
    wrap_width: int = 95,
    debug: bool = False,
) -> dict[str, list[str]]:
    """Run PII redaction over every .txt file in a directory.
 
    Preserves original line breaks and wraps long lines for readability.
 
    Args:
        input_dir: Directory containing .txt files to redact
        output_dir: Directory where redacted files will be saved
        model: Trained PII model
        tokenizer: Model tokenizer
        tag2id: Label to ID mapping
        max_len: Maximum token length
        wrap_width: Width for text wrapping
        debug: Whether to log debug information
 
    Returns:
        Dictionary mapping filenames to lists of redacted lines
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    id2tag = {v: k for k, v in tag2id.items()}
    page_outputs = {}
 
    for fname in sorted(Path(input_dir).iterdir()):
        # FIX: Path.iterdir() returns Path objects; use .suffix instead of .endswith()
        if fname.suffix != ".txt":
            continue
 
        input_path = Path(input_dir) / fname.name
        output_path = Path(output_dir) / fname.name
 
        logger.info(f"Processing file: {fname.name}")
 
        redacted_lines = []
 
        with input_path.open() as f:
            lines = f.readlines()
 
        for line in lines:
            stripped = line.strip()
 
            if stripped == "":
                redacted_lines.append("")
                continue
 
            tagged = tag_text(
                stripped,
                model=model,
                tokenizer=tokenizer,
                id2tag=id2tag,
                max_len=max_len,
                debug=debug,
            )
 
            redacted_sentence = redact_words(tagged)
            wrapped = textwrap.wrap(redacted_sentence, width=wrap_width)
            redacted_lines.extend(wrapped)
 
        with output_path.open("w") as f:
            for rl in redacted_lines:
                f.write(rl + "\n")
 
        page_outputs[fname.name] = redacted_lines
        logger.info(f"  ✓ Saved redacted file: {fname.name}")
 
    return page_outputs