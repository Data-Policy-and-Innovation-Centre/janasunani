"""Parse a ticket number from a document's path.

The ticket is encoded in the file's path *relative to the documents/ root*.
For nested documents the directory structure carries part of the ticket:

    OR107/E/2021/00324_complaint_20250916_102200.pdf  ->  OR107/E/2021/00324

For flat documents the whole ticket is in the filename:

    CMO2022115810_complaint_20250915_144909.jpeg      ->  CMO2022115810

The rule (uniform across all files):
  ticket = the relative path, with the trailing "_complaint_<timestamp>.<ext>"
  stripped from the final path segment, and all preceding directory segments
  kept and joined with "/".

CRITICAL: this only produces correct tickets if the stored relative path
*starts at the documents/ root*. That means the format classifier must be
run with --input pointing exactly at the documents/ directory, so that the
directory segments above the ticket (e.g. ".../raw_data/documents/") are
stripped and the segments below it (e.g. "OR107/E/2021/") are preserved.
If --input points deeper than documents/, the directory part of nested
tickets is lost and this function returns only the filename-derived part.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

# Matches the "_complaint_<timestamp>" suffix on the final filename segment.
# Example: "00324_complaint_20250916_102200" -> keeps "00324".
# The timestamp portion is permissive (digits and underscores) so minor
# format drift doesn't break parsing; anything from "_complaint" onward is
# stripped.
_SUFFIX_RE = re.compile(r"_complaint_.*$")


def ticket_from_relpath(full_path: str) -> str | None:
    """Derive the ticket number from a document's relative path.

    Args:
        full_path: Path relative to the documents/ root, as stored in
            pages.full_path (e.g. "OR107/E/2021/00324_complaint_....pdf"
            or "CMO2022115810_complaint_....jpeg").

    Returns:
        The ticket number (e.g. "OR107/E/2021/00324" or "CMO2022115810"),
        or None if the path doesn't contain a parseable "_complaint_" marker.
    """
    if not full_path:
        return None

    # Normalize to forward slashes regardless of OS the path was created on.
    p = PurePosixPath(full_path.replace("\\", "/"))

    # Drop the file extension from the final segment.
    last_stem = p.name
    dot = last_stem.rfind(".")
    if dot != -1:
        last_stem = last_stem[:dot]

    # Strip the _complaint_<timestamp> suffix from the final segment.
    if "_complaint_" not in last_stem:
        # Doesn't match the expected naming; can't reliably parse a ticket.
        return None
    ticket_last = _SUFFIX_RE.sub("", last_stem)

    # Reassemble: preceding directory segments + parsed last segment, joined
    # with "/". For a flat file there are no preceding dirs, so we just get
    # the parsed last segment.
    dir_parts = list(p.parent.parts)
    # PurePosixPath(".").parts is () so a bare filename yields no dir parts.
    if dir_parts:
        return "/".join([*dir_parts, ticket_last])
    return ticket_last
