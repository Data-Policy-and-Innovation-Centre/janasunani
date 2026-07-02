import os
from dataclasses import dataclass

from janasunani.pipeline.config import PipelineConfig
from janasunani.pipeline.db import connect


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
        print("summarizer: nothing to do")
        return

    tokenizer, model, torch, device = _load_model()
    total_completed = 0
    print(
        "summarizer: "
        f"model={MODEL_NAME} device={device} "
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
                print(
                    f"[{total_completed}] summarized {row.doc_id} "
                    f"pages={row.page_count} types={row.page_types}"
                )
        rows = _fetch_documents_to_summarize(config, DEFAULT_BATCH_SIZE)

    print(f"summarizer done: summarized={total_completed}")


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


def _load_model():
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    device = "cuda" if torch.cuda.is_available() else "cpu"
    hf_token = os.getenv("HF_TOKEN")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, token=hf_token)
    model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_NAME, token=hf_token).to(device)
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
