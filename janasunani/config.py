"""Project-wide configuration: paths, settings, and logging helpers.

Merges the original janasunani path/logging scaffold with the application
``Settings`` (database, AWS/S3, Janasunani API) migrated from the grievance
backend. The ``directories`` object and the ``settings`` instance are the two
things the migrated code imports.
"""

import os
from pathlib import Path
from typing import Optional

from loguru import logger
from pydantic_settings import BaseSettings, SettingsConfigDict
from tqdm import tqdm

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
OUTPUT_DATA_DIR = DATA_DIR / "output"
OLTP_DATA_DIR = DATA_DIR / "oltp"
DOCUMENTS_DIR = RAW_DATA_DIR / "documents"

OUTPUTS_DIR = ROOT_DIR / "outputs"
FIGURES_DIR = OUTPUTS_DIR / "figures"
REPORTS_DIR = OUTPUTS_DIR / "reports"
TABLES_DIR = OUTPUTS_DIR / "tables"
LOGS_DIR = ROOT_DIR / "logs"
MODELS_DIR = ROOT_DIR / "models"


class Directories:
    """Filesystem locations used by the application.

    Exposes the attribute names the migrated grievance code expects
    (``directories.RAW_DATA``, ``directories.DOCUMENTS``, ...) while reusing the
    janasunani module-level path constants as the single source of truth.
    """

    ROOT_DIR = ROOT_DIR
    DATA = DATA_DIR
    RAW_DATA = RAW_DATA_DIR
    INTERIM = INTERIM_DATA_DIR
    PROCESSED_DATA = PROCESSED_DATA_DIR
    EXTERNAL = EXTERNAL_DATA_DIR
    OUTPUT = OUTPUT_DATA_DIR
    OLTP = OLTP_DATA_DIR
    DOCUMENTS = DOCUMENTS_DIR
    LOGS = LOGS_DIR
    MODELS = MODELS_DIR

    def create(self) -> None:
        """Create the non-sensitive working directories if missing."""
        for directory in [
            self.DATA,
            self.RAW_DATA,
            self.INTERIM,
            self.PROCESSED_DATA,
            self.OUTPUT,
            self.OLTP,
            self.DOCUMENTS,
            self.LOGS,
            self.MODELS,
        ]:
            directory.mkdir(parents=True, exist_ok=True)


directories = Directories()


class Settings(BaseSettings):
    """Application settings, loaded from (in priority order) environment
    variables, the project-root ``.env`` file, then these defaults."""

    ENV: str = os.getenv("ENV", "local")
    DEBUG: bool = os.getenv("DEBUG", "True").lower() in ("true", "1", "yes")

    # Janasunani API (document ingestion source)
    JANASUNANI_API_BASE_URL: str = os.getenv(
        "JANASUNANI_API_BASE_URL", "https://janasunani.odisha.gov.in/api/DataServices"
    )
    JANASUNANI_API_USERNAME: Optional[str] = os.getenv("JANASUNANI_API_USERNAME")
    JANASUNANI_API_PASSWORD: Optional[str] = os.getenv("JANASUNANI_API_PASSWORD")

    # OLTP store (async SQLAlchemy; swappable engine via this URL).
    # Default: local SQLite. Deploy: e.g. postgresql+asyncpg://user:pass@host/db
    OLTP_DB_URL: str = os.getenv(
        "OLTP_DB_URL", f"sqlite+aiosqlite:///{OLTP_DATA_DIR.as_posix()}/janasunani.db"
    )
    DB_PASSWORD: Optional[str] = os.getenv("DB_PASSWORD")

    # MySQL source for migration (cold-start dump restore or live sync)
    MYSQL_URL: Optional[str] = os.getenv("MYSQL_URL")

    # Local document storage (used when ENV == "dev"/"local")
    LOCAL_STORAGE_PATH: str = str(DOCUMENTS_DIR)

    # AWS / S3 — credentials are optional: boto3 uses its default chain (env vars,
    # ~/.aws, or an EC2 instance role on deploy), so leave these unset unless a
    # static key is genuinely needed.
    AWS_ACCESS_KEY_ID: Optional[str] = os.getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY: Optional[str] = os.getenv("AWS_SECRET_ACCESS_KEY")
    AWS_REGION: str = os.getenv("AWS_REGION", "ap-south-1")
    AWS_S3_BUCKET_NAME: str = os.getenv("AWS_S3_BUCKET_NAME", "janasunani-data-main")
    AWS_S3_DOCUMENTS: str = os.getenv("AWS_S3_DOCUMENTS", "janasunani-documents-main")

    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")


settings = Settings()


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
    directories.create()


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


def stop_logging_to_console(
    filename: str | Path = LOGS_DIR / "main.log", mode: str = "a"
) -> None:
    """Remove all handlers and redirect logging to ``filename`` (used to keep
    long-running ingestion logs out of the console)."""
    for handler_id in list(logger._core.handlers.keys()):
        logger.remove(handler_id)
    logger.add(
        filename,
        format="{file}:{function}:{line} {time} {level} {message}",
        level="INFO",
        colorize=True,
        catch=True,
        mode=mode,
    )


def resume_logging_to_console() -> None:
    """Add a console sink that writes through ``tqdm.write`` so log lines don't
    clobber progress bars."""
    logger.add(lambda msg: tqdm.write(msg, end=""), colorize=True)
