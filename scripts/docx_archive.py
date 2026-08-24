"""Utilities for producing reproducible DOCX archives."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile
import zipfile


CANONICAL_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def canonicalize_docx_archive(path: Path) -> None:
    """Rewrite a DOCX with stable ZIP metadata while preserving member bytes."""

    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.canonical.",
        suffix=".docx",
        dir=path.parent,
        delete=False,
    ) as handle:
        rebuilt = Path(handle.name)

    try:
        with (
            zipfile.ZipFile(path, "r") as source,
            zipfile.ZipFile(
                rebuilt,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as destination,
        ):
            for member in source.infolist():
                canonical = zipfile.ZipInfo(
                    filename=member.filename,
                    date_time=CANONICAL_ZIP_TIMESTAMP,
                )
                canonical.compress_type = (
                    zipfile.ZIP_STORED if member.is_dir() else zipfile.ZIP_DEFLATED
                )
                canonical.create_system = 3
                canonical.external_attr = (
                    (0o40755 if member.is_dir() else 0o100644) << 16
                )
                destination.writestr(
                    canonical,
                    source.read(member.filename),
                    compress_type=canonical.compress_type,
                    compresslevel=9,
                )
        os.replace(rebuilt, path)
    except Exception:
        rebuilt.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Normalize DOCX ZIP metadata for reproducible byte hashes."
    )
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)
    for path in args.paths:
        canonicalize_docx_archive(path)
        print(f"canonicalized {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
