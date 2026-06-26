"""Document processing pipeline: stage-based OCR / PII redaction / page-type
classification / summarization / categorization over complaint documents,
writing page- and document-level rows to ``data/output/pipeline.sqlite``.

Stages are imported lazily (see ``pipeline.py``) so a given environment only
needs the dependencies of the stages it actually runs."""
