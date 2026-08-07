from dataclasses import dataclass
from pathlib import Path


def validate_sarvam_sharding(
    *, ocr_engine: str, sarvam_enabled: bool, num_workers: int
) -> None:
    """Reject enabled hosted Sarvam runs whose limiter cannot be global.

    Cross-machine workers are separate processes, while the adapter's rolling
    10-RPM limiter is process-local. Disabled Sarvam is safe to shard because
    it makes no remote calls and uses the maintained local fallback instead.
    """
    if ocr_engine == "sarvam" and sarvam_enabled and num_workers != 1:
        raise ValueError(
            "enabled Sarvam OCR requires num_workers=1 because its "
            "10-RPM limiter is process-local"
        )


@dataclass(frozen=True)
class PipelineConfig:
    input_dir: Path
    db_path: Path
    models_dir: Path
    ocr_engine: str = "pytesseract"
    # Hosted Sarvam is an authorized-external route and stays off unless a
    # deliberate run enables it.  When ``ocr_engine == "sarvam"`` but this is
    # false, the OCR stage uses its maintained pytesseract counterpart.
    sarvam_enabled: bool = False
    # None -> resolved by the page-type stage: the DVC-mirrored copy under
    # models_dir/page_type_classifier/vit_type_classifier when present,
    # falling back to the (orphaned-org) HF repo. Set explicitly to override.
    page_type_model_id: str | None = None
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
