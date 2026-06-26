"""Cold-start loader: restore a raw ``mysqldump`` into a (throwaway) MySQL, then
hand off to the shared :func:`janasunani.migration.from_mysql.run_migration`.

The dump (``data/raw/Dump20250730.sql``) is a table-only dump of
``sociomatics_ticket`` (MySQL 5.7, utf8mb4). We restore it into a real MySQL
because a faithful utf8mb4 restore of a multi-GB dump is exactly what ``mysql``
is built for — and it lets both the cold-start and live-sync paths converge on
one validated insert routine.

Typical use with a local MySQL 5.7 container:

    docker run -d --name mysql57 -e MYSQL_ROOT_PASSWORD=pass -p 3306:3306 mysql:5.7
    uv run janasunani-migrate-dump \\
        --dump data/raw/Dump20250730.sql \\
        --mysql-url mysql+pymysql://root:pass@127.0.0.1:3306/

Requires the ``mysql`` client on PATH (host client, or run this inside/against
the MySQL container).
"""

import argparse
import asyncio
import os
import subprocess
from pathlib import Path
from typing import Optional

import pymysql
from loguru import logger
from sqlalchemy.engine import make_url

from janasunani.config import settings
from janasunani.migration.from_mysql import run_migration

DEFAULT_DB_NAME = "sociomatics_ticket"


def _create_database(admin_url: str, db_name: str) -> None:
    """Create ``db_name`` (utf8mb4) on the server if it does not exist."""
    url = make_url(admin_url)
    conn = pymysql.connect(
        host=url.host or "127.0.0.1",
        port=url.port or 3306,
        user=url.username,
        password=url.password or "",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
        logger.info(f"Ensured database `{db_name}` exists")
    finally:
        conn.close()


def restore_dump(dump_path: Path, admin_url: str, db_name: str) -> None:
    """Create ``db_name`` and stream ``dump_path`` into it via the ``mysql`` client."""
    dump_path = Path(dump_path)
    if not dump_path.exists():
        raise FileNotFoundError(dump_path)

    _create_database(admin_url, db_name)

    url = make_url(admin_url)
    cmd = [
        "mysql",
        f"--host={url.host or '127.0.0.1'}",
        f"--port={url.port or 3306}",
        f"--user={url.username}",
        db_name,
    ]
    env = dict(os.environ)
    if url.password:
        env["MYSQL_PWD"] = url.password  # avoids putting the password in argv

    logger.info(f"Restoring {dump_path} ({dump_path.stat().st_size / 1e9:.1f} GB) into `{db_name}`")
    with open(dump_path, "rb") as fh:
        proc = subprocess.run(cmd, stdin=fh, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"mysql restore failed (exit {proc.returncode})")
    logger.success(f"Restored dump into `{db_name}`")


async def load_from_dump(
    dump_path: Path,
    admin_url: str,
    db_name: str = DEFAULT_DB_NAME,
    target_db_url: Optional[str] = None,
    skip_restore: bool = False,
) -> None:
    """Restore the dump (unless ``skip_restore``) and migrate it into the target DB."""
    if not skip_restore:
        restore_dump(dump_path, admin_url, db_name)
    else:
        logger.info("--skip-restore: assuming the dump is already loaded into MySQL")

    source_url = make_url(admin_url).set(database=db_name)
    await run_migration(
        mysql_url=source_url.render_as_string(hide_password=False),
        target_db_url=target_db_url or settings.DB_URL,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Load a raw mysqldump into the grievance DB.")
    parser.add_argument(
        "--dump",
        type=Path,
        default=Path("data/raw/Dump20250730.sql"),
        help="Path to the .sql dump file.",
    )
    parser.add_argument(
        "--mysql-url",
        required=True,
        help="Admin MySQL URL WITHOUT a database, e.g. "
        "mysql+pymysql://root:pass@127.0.0.1:3306/",
    )
    parser.add_argument("--db-name", default=DEFAULT_DB_NAME)
    parser.add_argument(
        "--target-db-url",
        default=None,
        help="Target DB URL (default: settings.DB_URL, the local grievance.db).",
    )
    parser.add_argument(
        "--skip-restore",
        action="store_true",
        help="Skip the mysql restore and migrate from an already-loaded database.",
    )
    args = parser.parse_args()

    asyncio.run(
        load_from_dump(
            dump_path=args.dump,
            admin_url=args.mysql_url,
            db_name=args.db_name,
            target_db_url=args.target_db_url,
            skip_restore=args.skip_restore,
        )
    )


if __name__ == "__main__":
    main()
