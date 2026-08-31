#!/usr/bin/env python3
"""Reject tracked Terraform plan archives, whatever they are called.

A saved plan is a zip that contains the state it was planned against. Two of
them were committed on `infra/dsi-reference-bucket` and carried the AWS
account id, five instance ids, the SSH ingress CIDRs and the operator's public
key into a pushed branch. The `.gitignore` and CI patterns that exist to stop
exactly that all describe state *by filename* (`terraform.tfstate`,
`terraform.tfvars`, `.env`, `*.pem`), and a plan is named like a plan, so
nothing objected.

The first fix added `.*\\.tfplan` to those patterns, which is the same mistake
one step later: `-out` takes an arbitrary filename and Terraform's own
documentation uses `terraform plan -out=tfplan`, with no suffix at all. A
suffix list can only ever cover the names someone thought of.

So this looks at the bytes. Every tracked file that begins with the zip local
header is opened and its entry names are checked for the members a plan
archive carries. That is name-independent: `tfplan`, `plan.out`, `foo.bin` and
a plan with no extension are all caught, and a .pptx or .xlsx (also zips) is
not, because it has no `tfstate` member.

Runs in two places. In CI it scans every tracked file. With ``--staged`` it
scans the blobs staged for the next commit instead, which is what
``.githooks/pre-commit`` calls: a content scan that only runs in Actions sees
the plan after the push that already disclosed the state, so the same test has
to be available before the commit is made. The staged mode reads the index
rather than the worktree, because those differ once a file is staged and then
edited, and the index is what gets committed.

Deliberately dependency-free — stdlib only, so the check runs before any
`uv sync` and cannot be skipped by an environment problem.
"""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ZIP_MAGIC = b"PK\x03\x04"

# Members the Terraform plan format writes. `tfstate` is the state the plan
# was made against and `tfstate-prev` the one before it; either is the
# disclosure. `tfplan` is the plan proper, which also carries resource
# attributes. Matched on the entry's base name because the layout has changed
# across Terraform versions.
PLAN_MEMBERS = frozenset({"tfstate", "tfstate-prev", "tfplan"})


def tracked_files() -> list[Path]:
    """Tracked paths, excluding ``data/``.

    The exclusion is a data-policy requirement, not an optimisation. AGENTS.md
    forbids listing or reading anything under ``data/`` without explicit
    per-path permission, and this check opens every file it is handed. Nothing
    is lost by skipping it: the workflow step immediately before this one
    already rejects any tracked file under ``data/`` that is not a ``.dvc``
    pointer, a ``.gitkeep`` or a provenance sidecar, so a plan archive hidden
    there fails the build one step earlier and never reaches this scan.
    """
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", ".", ":(exclude)data/**"],
        check=True,
        capture_output=True,
    ).stdout
    return [Path(name) for name in out.decode("utf-8").split("\0") if name]


def _open_nofollow(path: Path) -> int | None:
    """Open ``path`` for reading, refusing a symlink at *any* component.

    ``Path.is_symlink()`` only describes the final component, so it does not
    see an ancestor that has been replaced by a link: with the index still
    listing ``public/payload`` and the worktree holding ``public -> data``,
    ``public/payload`` is not a symlink and opening it reads ``data/payload``
    straight through the ``:(exclude)data/**`` pathspec.

    Walking the components with ``O_NOFOLLOW`` closes that, and closes it
    without a race: each directory is opened relative to the previous one, so
    there is no window between deciding a path is safe and opening it.
    """
    parts = path.parts
    if not parts:
        return None
    if path.is_absolute():
        start, parts = parts[0], parts[1:]
    else:
        start = "."
    if not parts:
        return None

    try:
        dir_fd = os.open(start, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return None

    try:
        for component in parts[:-1]:
            try:
                nxt = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=dir_fd,
                )
            except OSError:
                return None
            os.close(dir_fd)
            dir_fd = nxt
        try:
            return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
        except OSError:
            return None
    finally:
        os.close(dir_fd)


def _members_of(source: Path | io.BytesIO | io.BufferedReader) -> list[str]:
    """Plan-archive entry names in ``source``, which may be a path or bytes."""
    try:
        with zipfile.ZipFile(source) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError):
        # Starts like a zip and will not open as one. Not a plan we can
        # confirm, and not something this check should fail the build over —
        # the suffix patterns in the workflow still cover the obvious names.
        return []

    return sorted({name for name in names if Path(name).name in PLAN_MEMBERS})


def plan_members(path: Path) -> list[str]:
    """Entry names identifying ``path`` as a Terraform plan archive, if any.

    Opened through :func:`_open_nofollow`, so a symlink anywhere in the path
    yields no members rather than a read of whatever it points at.
    """
    fd = _open_nofollow(path)
    if fd is None:
        return []

    try:
        with os.fdopen(fd, "rb") as handle:
            if handle.read(4) != ZIP_MAGIC:
                return []
            handle.seek(0)
            return _members_of(handle)
    except OSError:
        # Directories and unreadable entries land here; neither is an archive.
        return []


def plan_members_of_blob(data: bytes) -> list[str]:
    """Same test as :func:`plan_members`, against bytes read from the index."""
    if not data.startswith(ZIP_MAGIC):
        return []
    return _members_of(io.BytesIO(data))


def staged_entries() -> list[tuple[Path, str]]:
    """``(path, blob_sha)`` for each regular file staged for commit.

    The worktree scan is the wrong source for a pre-commit check: what gets
    committed is the *index*, and the two differ whenever a file is staged and
    then edited. So this reads the staged blob.

    Symlink (120000) and gitlink (160000) entries are dropped for the reason
    given in :func:`main` — a symlink is never itself the archive, and reading
    through one would defeat the ``data/`` exclusion.
    """
    changed = subprocess.run(
        [
            "git", "diff", "--cached", "--name-only", "-z",
            "--diff-filter=ACMR", "--", ".", ":(exclude)data/**",
        ],
        check=True,
        capture_output=True,
    ).stdout
    paths = [name for name in changed.decode("utf-8").split("\0") if name]
    if not paths:
        return []

    # `--literal-pathspecs` because these are filenames, not pathspecs we
    # wrote. Without it a staged file whose *name* is pathspec magic is
    # reinterpreted rather than selected, and it fails both ways: a plan named
    # `:(exclude,glob)**` excludes itself and passes the scan, while a file
    # named `:(glob)**` re-includes paths this check must never open,
    # `data/` among them. The exclusion above is ours and keeps its magic;
    # it runs in a separate call for exactly that reason.
    listing = subprocess.run(
        ["git", "--literal-pathspecs", "ls-files", "-s", "-z", "--", *paths],
        check=True,
        capture_output=True,
    ).stdout

    entries: list[tuple[Path, str]] = []
    for record in listing.decode("utf-8").split("\0"):
        if not record:
            continue
        meta, _, name = record.partition("\t")
        fields = meta.split()
        if len(fields) < 2:
            continue
        mode, sha = fields[0], fields[1]
        if mode in {"120000", "160000"}:
            continue
        entries.append((Path(name), sha))
    return entries


def blob_bytes(sha: str) -> bytes:
    """Contents of a staged blob."""
    return subprocess.run(
        ["git", "cat-file", "blob", sha],
        check=True,
        capture_output=True,
    ).stdout


def scan_staged() -> list[tuple[Path, list[str]]]:
    """Offenders among the blobs staged for the next commit."""
    offenders: list[tuple[Path, list[str]]] = []
    for path, sha in staged_entries():
        members = plan_members_of_blob(blob_bytes(sha))
        if members:
            offenders.append((path, members))
    return offenders


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--staged",
        action="store_true",
        help=(
            "Scan the blobs staged for commit instead of the tracked "
            "worktree. Used by .githooks/pre-commit, so a plan saved under "
            "an arbitrary name is refused before it is pushed rather than "
            "after CI has seen it."
        ),
    )
    # ``argv or []`` rather than ``None``: a bare ``main()`` is the
    # in-process worktree scan, and must not read pytest's or any other
    # caller's sys.argv. The CLI entry point below passes argv itself.
    args = parser.parse_args(argv or [])

    if args.staged:
        offenders = scan_staged()
        return report(offenders)

    offenders: list[tuple[Path, list[str]]] = []
    for path in tracked_files():
        # No `is_symlink()`/`is_file()` prefilter here, deliberately. Both
        # stat through a symlinked ancestor -- `lstat("public/payload")` and
        # `stat("public/payload")` on the `public -> data` case -- and
        # AGENTS.md forbids inspecting metadata under data/, not merely
        # reading it. `_open_nofollow` is the only gate, and it never stats:
        # it opens each component with O_NOFOLLOW and fails closed. The cases
        # the prefilters used to cover still resolve correctly -- a directory
        # or submodule entry opens but is not a zip, and a broken or
        # symlinked path fails to open at all.
        members = plan_members(path)
        if members:
            offenders.append((path, members))

    return report(offenders)


def report(offenders: list[tuple[Path, list[str]]]) -> int:
    if not offenders:
        return 0

    print("The following files are Terraform plan archives:")
    for path, members in offenders:
        print(f"  {path}  (contains: {', '.join(members)})")
    print()
    print(
        "A saved plan is a zip containing the state it was planned against, "
        "so it carries the account id, instance and subnet ids, SSH ingress "
        "CIDRs and the operator public key. Terraform state stays local "
        "(docs/DEPLOY.md). Detected by content, not by filename, because "
        "`-out` takes any name — `terraform plan -out=tfplan` has no suffix "
        "to match on."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
