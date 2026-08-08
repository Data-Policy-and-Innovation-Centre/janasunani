from janasunani.pipeline.config import PipelineConfig
from janasunani.pipeline.db import initialize_database
from loguru import logger

# The canonical order stages run in. Used when config.stages is None
# (i.e. "run everything"). Six stages only — no 7th spam/triage stage
# gating yet (that is Unit 14 / Phase 14); keep this tuple as the
# source of truth for CLI choices and for the E2E rehearsal.
STAGE_ORDER = [
    "format_classifier",
    "ocr_extraction",
    "pii_tagger",
    "page_type_classifier",
    "summarizer",
    "categorizer",
]


def run_pipeline(config: PipelineConfig) -> None:
    initialize_database(config.db_path)

    stages_to_run = config.stages if config.stages else STAGE_ORDER

    # Validate stage names up front so a typo fails fast with a clear message
    unknown = [s for s in stages_to_run if s not in STAGE_ORDER]
    if unknown:
        raise ValueError(
            f"unknown stage(s): {unknown}. valid stages: {STAGE_ORDER}"
        )

    # Run in canonical order regardless of the order given on the CLI, so
    # dependencies between stages (e.g. OCR needs format_classifier's rows)
    # are respected.
    for stage_name in STAGE_ORDER:
        if stage_name not in stages_to_run:
            continue

        # Imports are deliberately INSIDE the loop, not at module top level.
        # This means an environment only needs the dependencies of the
        # stages it actually runs. Running just ocr_extraction never imports
        # the format classifier's pandas/sklearn/xgboost stack, and vice
        # versa. This is what makes per-backend environments viable.
        if stage_name == "format_classifier":
            from janasunani.pipeline.stages.format_classifier import run_format_classifier
            run_format_classifier(config)
        elif stage_name == "ocr_extraction":
            from janasunani.pipeline.stages.ocr_extraction import run_ocr_extraction
            run_ocr_extraction(config)
        elif stage_name == "pii_tagger":
            from janasunani.pipeline.stages.pii_tagger import run_pii_tagger
            run_pii_tagger(config)
        elif stage_name == "page_type_classifier":
            from janasunani.pipeline.stages.page_type_classifier import run_page_type_classifier
            run_page_type_classifier(config)
        elif stage_name == "summarizer":
            from janasunani.pipeline.stages.summarizer import run_summarizer
            run_summarizer(config)
        elif stage_name == "categorizer":
            from janasunani.pipeline.stages.categorizer import run_categorizer
            run_categorizer(config)

    logger.success(f"Pipeline complete: {config.db_path}")
