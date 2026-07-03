"""Logging utilities for PII Tagger."""

import logging
import sys
from pathlib import Path


def setup_logging(output_dir: Path, log_filename: str = "app.log") -> Path:
    """Configure logging to file and stdout.

    Args:
        output_dir: Directory where log file will be saved
        log_filename: Name of the log file (default: "app.log")

    Returns:
        Path to the created log file
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    log_file = output_dir / log_filename

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_file, mode="w"),
            logging.StreamHandler(sys.stdout),
        ],
    )

    logger = logging.getLogger(__name__)
    logger.info(f"Logging initialized. Output: {log_file}")

    return log_file