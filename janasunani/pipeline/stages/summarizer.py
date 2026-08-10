import os
from dataclasses import dataclass
from pathlib import Path

from janasunani.pipeline.config import PipelineConfig
from janasunani.pipeline.db import connect
from janasunani.tracking.artifacts import (
    ALLOW_REMOTE_MODELS_ENV_VAR,
    remote_models_allowed,
)
from loguru import logger


MODEL_NAME = "facebook/bart-large-cnn"
TARGET_PAGE_TYPE_CLASS = 1
DEFAULT_BATCH_SIZE = 8
MAX_INPUT_LENGTH = 1024
MAX_SUMMARY_LENGTH = 100
MIN_SUMMARY_LENGTH = 20


@dataclass(frozen=True)
class DocumentToSummarize:
    doc_id: str
    page_count: int
    page_types: str
    combined_text: str


def run_summarizer(config: PipelineConfig) -> None:
    """Summarize document-level text into documents.summary."""
    _ensure_document_rows(config)
    rows = _fetch_documents_to_summarize(config, DEFAULT_BATCH_SIZE)

    if not rows:
        logger.info("summarizer: nothing to do")
        return

    tokenizer, model, torch, device = _load_model(models_dir=config.models_dir)
    total_completed = 0
    logger.info(
        "summarizer: "
        f"model=local-release-or-dvc device={device} "
        f"target_page_type_class={TARGET_PAGE_TYPE_CLASS} "
        f"batch_size={DEFAULT_BATCH_SIZE}"
    )

    while rows:
        with connect(config.db_path) as connection:
            for row in rows:
                summary = _summarize_text(row.combined_text, tokenizer, model, torch, device)
                connection.execute(
                    """
                    UPDATE documents
                       SET summary = ?
                     WHERE doc_id = ?
                    """,
                    (summary, row.doc_id),
                )
                connection.commit()
                total_completed += 1
                logger.info(
                    f"[{total_completed}] summarized {row.doc_id} "
                    f"pages={row.page_count} types={row.page_types}"
                )
        rows = _fetch_documents_to_summarize(config, DEFAULT_BATCH_SIZE)

    logger.success(f"summarizer done: summarized={total_completed}")


def _ensure_document_rows(config: PipelineConfig) -> None:
    with connect(config.db_path) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO documents (doc_id, ticket_number)
            SELECT doc_id, MIN(ticket_number)
            FROM pages
            GROUP BY doc_id
            """
        )


def _fetch_documents_to_summarize(
    config: PipelineConfig,
    limit: int,
) -> list[DocumentToSummarize]:
    with connect(config.db_path) as connection:
        rows = connection.execute(
            """
            SELECT
                p.doc_id,
                p.page_number,
                p.page_type,
                p.redacted_text
            FROM pages AS p
            JOIN documents AS d ON d.doc_id = p.doc_id
            WHERE p.page_type_class = ?
              AND p.redacted_text IS NOT NULL
              AND trim(p.redacted_text) != ''
              AND (d.summary IS NULL OR trim(d.summary) = '')
            ORDER BY p.doc_id, p.page_number
            """,
            (TARGET_PAGE_TYPE_CLASS,),
        ).fetchall()

    documents: dict[str, dict[str, object]] = {}
    for row in rows:
        doc = documents.setdefault(
            row["doc_id"],
            {"page_types": [], "texts": []},
        )
        page_type = row["page_type"] or "unknown"
        page_types = doc["page_types"]
        if isinstance(page_types, list) and page_type not in page_types:
            page_types.append(page_type)
        texts = doc["texts"]
        if isinstance(texts, list):
            texts.append(str(row["redacted_text"]))

    output: list[DocumentToSummarize] = []
    for doc_id, doc in documents.items():
        if len(output) >= limit:
            break
        texts = doc["texts"]
        page_types = doc["page_types"]
        if not isinstance(texts, list) or not texts:
            continue
        output.append(
            DocumentToSummarize(
                doc_id=doc_id,
                page_count=len(texts),
                page_types=", ".join(page_types) if isinstance(page_types, list) else "",
                combined_text="\n\n".join(texts),
            )
        )
    return output


def _remote_models_allowed() -> bool:
    return remote_models_allowed()


def _resolve_model_source(*, models_dir=None) -> tuple[str, bool]:
    """Return a local artifact by default; remote HF is explicit dev mode."""

    from janasunani.tracking.artifacts import resolve_artifact

    artifact = resolve_artifact(
        "summarizer", models_dir=Path(models_dir) if models_dir is not None else None
    )
    if artifact is not None:
        return str(artifact), True
    if _remote_models_allowed():
        return MODEL_NAME, False
    raise RuntimeError(
        "no local summarizer artifact resolved; materialize the approved release "
        f"or set {ALLOW_REMOTE_MODELS_ENV_VAR}=1 for development only"
    )


def _load_model(model_name_or_path=None, *, models_dir=None):
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if model_name_or_path is None:
        source, local_only = _resolve_model_source(models_dir=models_dir)
    else:
        source = str(model_name_or_path)
        local_only = Path(source).exists()
        if not local_only and not _remote_models_allowed():
            raise RuntimeError(
                "remote summarizer model IDs require explicit development opt-in"
            )
    hf_token = None if local_only else os.getenv("HF_TOKEN")
    tokenizer = AutoTokenizer.from_pretrained(
        source, token=hf_token, local_files_only=local_only
    )
    model = AutoModelForSeq2SeqLM.from_pretrained(
        source, token=hf_token, local_files_only=local_only
    ).to(device)
    model.eval()
    return tokenizer, model, torch, device


def _summarize_text(text: str, tokenizer, model, torch, device: str) -> str:
    inputs = tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        max_length=MAX_INPUT_LENGTH,
    ).to(device)

    with torch.no_grad():
        summary_ids = model.generate(
            inputs["input_ids"],
            max_length=MAX_SUMMARY_LENGTH,
            min_length=MIN_SUMMARY_LENGTH,
            num_beams=4,
        )
    return tokenizer.decode(summary_ids[0], skip_special_tokens=True)


class Summarizer:
    """Single-call, warm wrapper around the summarizer model.

    Loads the model once in __init__ (unlike run_summarizer, which loads it
    per batch run) and reuses the cached tokenizer/model/device handles across
    calls to summarize(). Intended for in-process callers (e.g. a warm
    inference processor) that summarize one document at a time.
    """

    def __init__(self, model_name_or_path=None) -> None:
        if model_name_or_path is None:
            loaded = _load_model()
        else:
            loaded = _load_model(model_name_or_path)
        self._tokenizer, self._model, self._torch, self._device = loaded

    def summarize(self, text: str) -> str:
        """Summarize a single piece of text using the warm model handles."""
        flattened = " ".join(text.split())
        if len(flattened.split()) < MIN_SUMMARY_LENGTH:
            # Too short to meaningfully summarize (shorter than the model's
            # own minimum summary length) - return the flattened text as-is
            # instead of calling .generate().
            return flattened
        return _summarize_text(text, self._tokenizer, self._model, self._torch, self._device)
