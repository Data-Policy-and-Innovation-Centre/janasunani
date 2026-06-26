"""Project-wide paths and logging helpers."""

from pathlib import Path

from loguru import logger

ROOT_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parent

SCRIPTS_DIR = ROOT_DIR / "scripts"
NOTEBOOKS_DIR = ROOT_DIR / "notebooks"
TESTS_DIR = ROOT_DIR / "tests"

DATA_DIR = ROOT_DIR / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
INTERIM_DATA_DIR = DATA_DIR / "interim"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
EXTERNAL_DATA_DIR = DATA_DIR / "external"

OUTPUTS_DIR = ROOT_DIR / "outputs"
FIGURES_DIR = OUTPUTS_DIR / "figures"
REPORTS_DIR = OUTPUTS_DIR / "reports"
TABLES_DIR = OUTPUTS_DIR / "tables"
LOGS_DIR = ROOT_DIR / "logs"


def ensure_project_directories() -> None:
    """Create standard non-sensitive project directories if they are missing."""
    for directory in [
        SCRIPTS_DIR,
        NOTEBOOKS_DIR,
        TESTS_DIR,
        OUTPUTS_DIR,
        FIGURES_DIR,
        REPORTS_DIR,
        TABLES_DIR,
        LOGS_DIR,
    ]:
        directory.mkdir(parents=True, exist_ok=True)


def configure_file_logging(
    filename: str | Path = LOGS_DIR / "main.log",
    mode: str = "a",
) -> None:
    """Route log messages to a file instead of the default console sink."""
    logger.remove()
    logger.add(
        filename,
        format="{file}:{function}:{line} {time} {level} {message}",
        level="INFO",
        catch=True,
        mode=mode,
    )
