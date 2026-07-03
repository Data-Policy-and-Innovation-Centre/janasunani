"""Batch PII redaction utilities for DataFrames and databases."""

import logging

import pandas as pd
import torch
from transformers import PreTrainedModel, PreTrainedTokenizerBase

from PII_tagger.src.redaction.text_utils import (
    normalize_text,
    preprocess_words,
    redact_words,
)

logger = logging.getLogger(__name__)


def batch_tag_texts(
    texts: list[str],
    model: PreTrainedModel,
    tokenizer: PreTrainedTokenizerBase,
    id2tag: dict[int, str],
    max_len: int = 128,
    device: torch.device = None,
) -> list[list[tuple[str, str]]]:
    """Batch CRF tagging for many text blocks at once.

    Args:
        texts: List of text strings to tag
        model: Trained PII model
        tokenizer: Model tokenizer
        id2tag: ID to label mapping
        max_len: Maximum token length
        device: Torch device (if None, uses model's device)

    Returns:
        List of [(word, tag), ...] for each input text
    """
    if device is None:
        device = next(model.parameters()).device

    normed = [normalize_text(t) for t in texts]
    token_lists = [preprocess_words(t) for t in normed]

    enc = tokenizer(
        token_lists,
        is_split_into_words=True,
        return_tensors="pt",
        max_length=max_len,
        truncation=True,
        padding="max_length",
    ).to(device)

    model.eval()
    with torch.no_grad():
        output = model(enc["input_ids"], enc["attention_mask"])

    if isinstance(output, tuple):
        decoded_batch = output[0]
    else:
        decoded_batch = output

    all_results = []
    for i, words in enumerate(token_lists):
        preds = []
        prev_wid = None
        sub_i = 0
        word_ids = enc.word_ids(i)

        for wid in word_ids:
            if wid is None:
                sub_i += 1
                continue
            if wid != prev_wid:
                pred_id = int(decoded_batch[i][sub_i])
                preds.append(id2tag[pred_id])
            prev_wid = wid
            sub_i += 1

        all_results.append(list(zip(words, preds)))

    return all_results


def redact_dataframe_column_fast(
    extracted_text_df: pd.DataFrame,
    text_col: str,
    model: PreTrainedModel,
    tokenizer: PreTrainedTokenizerBase,
    tag2id: dict,
    batch_size: int = 32,
    wrap_width: int = 95,
    max_len: int = 128,
    new_col: str = "redacted_text",
) -> pd.DataFrame:
    """Batch redact PII in a DataFrame column.

    Args:
        extracted_text_df: DataFrame containing extracted text data
        text_col: Name of column containing text to redact
        model: Trained PII model
        tokenizer: Model tokenizer
        tag2id: Label to ID mapping
        batch_size: Number of texts to process at once
        wrap_width: Text wrapping width (unused, kept for compatibility)
        max_len: Maximum token length
        new_col: Name of new column for redacted text

    Returns:
        DataFrame with new redacted column added
    """
    device = next(model.parameters()).device
    id2tag = {v: k for k, v in tag2id.items()}

    texts = extracted_text_df[text_col].fillna("").astype(str).tolist()
    n = len(texts)

    null_count = extracted_text_df[text_col].isna().sum()
    if null_count > 0:
        logger.warning(
            f"Found {null_count} null values in '{text_col}' column - treating as empty strings"
        )

    logger.info(f"Starting redaction on {n} rows (batch_size={batch_size})")

    results = []
    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        batch = texts[start:end]
        logger.info(f"Processing batch {start}-{end-1}")

        tagged_batch = batch_tag_texts(
            batch,
            model=model,
            tokenizer=tokenizer,
            id2tag=id2tag,
            max_len=max_len,
            device=device,
        )

        for tagged in tagged_batch:
            red = redact_words(tagged)
            results.append(red)

    extracted_text_df[new_col] = results
    logger.info("Redaction complete")

    return extracted_text_df