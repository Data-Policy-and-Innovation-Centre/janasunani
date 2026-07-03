import argparse
from pathlib import Path

from janasunani.pipeline.config import PipelineConfig
from janasunani.pipeline.db import initialize_database
from janasunani.pipeline.pipeline import STAGE_ORDER, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the document processing pipeline.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_db = subparsers.add_parser("init-db", help="Create the SQLite database schema.")
    init_db.add_argument("--db", type=Path, default=Path("data/output/pipeline.sqlite"))

    run = subparsers.add_parser("run", help="Run the full document pipeline.")
    run.add_argument("--input", type=Path, default=Path("data/input"))
    run.add_argument("--db", type=Path, default=Path("data/output/pipeline.sqlite"))
    run.add_argument("--models", type=Path, default=Path("models"))
    run.add_argument(
        "--file-list",
        type=Path,
        default=None,
        help=(
            "Text file with one input path per line. Relative paths are resolved "
            "against --input. Useful for resuming partial runs or processing a "
            "curated subset."
        ),
    )
    run.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of worker processes for parallel stages. Default: auto. Pass 1 to force serial.",
    )
    run.add_argument(
        "--ocr-engine",
        choices=["pytesseract", "deepseek"],
        default="pytesseract",
    )
    run.add_argument(
        "--page-type-model",
        default=None,
        help=(
            "HF model repo or local path for the page type classifier. "
            "Default: the DVC-mirrored copy under <models>/page_type_classifier/"
            "vit_type_classifier when present, else the upstream HF repo."
        ),
    )
    # --- OCR stage additions ---
    run.add_argument(
        "--filter-language",
        type=str,
        default=None,
        help=(
            "Only OCR pages whose detected language matches this value "
            "(e.g. 'English', 'Odia', 'English, Odia'). Default: no filter."
        ),
    )
    run.add_argument(
        "--worker-id",
        type=int,
        default=0,
        help=(
            "0-indexed worker id for cross-machine sharding (e.g. SLURM arrays). "
            "Use with --num-workers. Default: 0."
        ),
    )
    run.add_argument(
        "--num-workers",
        type=int,
        default=1,
        help=(
            "Total workers across all machines for cross-machine sharding. "
            "Each worker processes a round-robin slice of the work. Default: 1."
        ),
    )
    # --- stage selection ---
    run.add_argument(
        "--stages",
        nargs="+",
        choices=STAGE_ORDER,
        default=None,
        help=(
            "Which stages to run (default: all). Stages always run in their "
            "canonical order regardless of the order given here. Running a "
            "subset means only those stages' dependencies are imported — useful "
            "when a stage (e.g. deepseek OCR) needs a different environment than "
            "the others. Example: --stages ocr_extraction"
        ),
    )
    # --- categorizer ---
    run.add_argument(
        "--complaints-json",
        type=Path,
        default=None,
        help=(
            "Path to the complaints JSON (ticket_no + grievance source). "
            "When set, the categorizer ingests grievances into the documents "
            "table before categorizing."
        ),
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "init-db":
        initialize_database(args.db)
        print(f"Initialized database: {args.db}")
        return 0

    if args.command == "run":
        if args.num_workers < 1:
            raise SystemExit("--num-workers must be >= 1")
        if not (0 <= args.worker_id < args.num_workers):
            raise SystemExit(
                f"--worker-id ({args.worker_id}) must be in [0, --num-workers={args.num_workers})"
            )

        config = PipelineConfig(
            input_dir=args.input,
            db_path=args.db,
            models_dir=args.models,
            ocr_engine=args.ocr_engine,
            page_type_model_id=args.page_type_model,
            file_list=args.file_list,
            n_workers=args.workers,
            filter_language=args.filter_language,
            worker_id=args.worker_id,
            num_workers=args.num_workers,
            stages=tuple(args.stages) if args.stages else None,
            complaints_json=args.complaints_json,
        )
        run_pipeline(config)
        return 0

    return 1
