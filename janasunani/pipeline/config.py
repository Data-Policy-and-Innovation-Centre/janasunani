from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PipelineConfig:
    input_dir: Path
    db_path: Path
    models_dir: Path
    ocr_engine: str = "pytesseract"
    page_type_model_id: str = "DPIC-Pipeline/vit_type_classifier"
    file_list: Path | None = None
    n_workers: int | None = None
    # --- OCR stage additions ---
    filter_language: str | None = None
    worker_id: int = 0
    num_workers: int = 1
    # --- stage selection ---
    # Which stages to run. None means "run all stages in canonical order".
    # When set, only the named stages run (and only their dependencies get
    # imported), which is what lets a single-backend environment avoid
    # loading other stages' dependency stacks.
    stages: tuple[str, ...] | None = None
    # --- categorizer ---
    # Path to the complaints JSON (ticket_no + grievance source). When set,
    # the categorizer ingests grievances into the documents table before
    # categorizing. If None, the categorizer assumes documents are already
    # populated.
    complaints_json: Path | None = None