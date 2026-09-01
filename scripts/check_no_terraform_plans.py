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

So this looks at the bytes -- and at the bytes *git* holds, not the
worktree's. Reading the worktree made the tracked path an untrusted mapping
to content: a symlink, a symlinked ancestor and a hard link each reached
`data/` through it, and each fix was a new way to police the filesystem. A
hard link ends that approach, being an ordinary directory entry no open-time
flag can distinguish. Blobs are also the more correct object to test, since
what matters is whether an archive is *in git*. Every tracked file that begins with the zip local
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


def tracked_entries() -> list[tuple[Path, str]]:
    """``(path, blob_sha)`` for every tracked regular file outside ``data/``.

    The scan reads git objects rather than the worktree. Four separate
    findings on this PR -- a symlink at the final component, a symlink at an
    ancestor, the stat prefilters in front of the open, and a hard link --
    were all the same defect: the worktree is an untrusted mapping from
    tracked path to bytes, and every fix was a new way to police it. A hard
    link ends that approach, because it is an ordinary directory entry that
    no open-time flag can distinguish.

    The blob is also the more correct object to test. What matters is whether
    a plan archive is *in git*, and the worktree copy can differ from it or
    be absent entirely.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-s", "-z", "--", ".", ":(exclude)data/**"],
        check=True,
        capture_output=True,
    ).stdout
    return _parse_ls_files(listing)


def _parse_ls_files(listing: bytes) -> list[tuple[Path, str]]:
    """``(path, sha)`` from ``git ls-files -s -z`` output, regular files only.

    Symlink (120000) and gitlink (160000) entries are dropped: git stores a
    link's text rather than its target's bytes, so a link is never itself the
    archive, and a submodule has no blob here.
    """
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


def _members_of(source: Path | io.BytesIO | io.BufferedReader) -> list[str]:
    """Plan-archive entry names in ``source``, which may be a path or bytes."""
    try:
        with zipfile.ZipFile(source) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError):
        return []

    return sorted({name for name in names if Path(name).name in PLAN_MEMBERS})


def damaged_plan_members(data: bytes) -> list[str]:
    """Plan member names visible in a zip that will not parse.

    A truncated or otherwise damaged plan still carries its secrets: the
    tfstate entry is in the archive whether or not the central directory
    survives. Treating an unparseable zip as clean therefore lets exactly the
    file this check exists to stop through, and truncation is not an exotic
    accident -- an interrupted `terraform plan -out` produces one.

    Zip stores each member's name uncompressed in its local file header, so
    the names are still present as literal bytes even when the archive cannot
    be opened.
    """
    return sorted(
        {name for name in PLAN_MEMBERS if name.encode("utf-8") in data}
    )


def plan_members_of_blob(data: bytes) -> list[str]:
    """Plan member names in a blob, including one that will not parse."""
    if not data.startswith(ZIP_MAGIC):
        return []
    members = _members_of(io.BytesIO(data))
    return members or damaged_plan_members(data)


DVC_POINTER_MAX_BYTES = 100_000
POINTER_PATHSPEC = "data/**/*.dvc"


def tracked_pointer_entries() -> list[tuple[Path, str]]:
    """``(path, sha)`` for tracked ``.dvc`` files under ``data/``."""
    listing = subprocess.run(
        ["git", "ls-files", "-s", "-z", "--", POINTER_PATHSPEC],
        check=True,
        capture_output=True,
    ).stdout
    return _parse_ls_files(listing)


def staged_pointer_entries() -> list[tuple[Path, str]]:
    """The same, restricted to what is staged for the next commit."""
    changed = subprocess.run(
        [
            "git", "diff", "--cached", "--name-only", "-z",
            "--diff-filter=ACMR", "--", POINTER_PATHSPEC,
        ],
        check=True,
        capture_output=True,
    ).stdout
    paths = [name for name in changed.decode("utf-8").split("\0") if name]
    if not paths:
        return []
    listing = subprocess.run(
        ["git", "--literal-pathspecs", "ls-files", "-s", "-z", "--", *paths],
        check=True,
        capture_output=True,
    ).stdout
    return _parse_ls_files(listing)


def blob_size(sha: str) -> int:
    """Size of a blob, without reading it."""
    out = subprocess.run(
        ["git", "cat-file", "-s", sha], check=True, capture_output=True
    ).stdout
    return int(out.decode("utf-8").strip())


def pointer_offenders(entries: list[tuple[Path, str]]) -> list[tuple[Path, list[str]]]:
    """``.dvc`` paths under ``data/`` that are not DVC pointers.

    The exclusion that keeps this scan out of ``data/`` left a hole, because
    the allowlist in ``data-check.yml`` accepts *any* name ending ``.dvc``
    without checking what it is, and ``.gitignore`` un-ignores
    ``data/raw/*.dvc``. A saved plan committed as ``data/raw/saved.dvc``
    therefore passed the raw-data predicate and the content scan alike, and
    carried the account id and SSH ingress CIDRs into history.

    Reading these blobs is a deliberate, narrow exception to the data rule
    and stays inside it in the way that matters. A pointer is a few hundred
    bytes of YAML holding an md5, a size and a path -- not citizen data --
    and it is read precisely to prove that is all it is. Anything at all
    large is refused on its size alone, via ``git cat-file -s``, so no bulk
    file under ``data/`` is ever read.
    """
    offenders: list[tuple[Path, list[str]]] = []
    for path, sha in entries:
        size = blob_size(sha)
        if size > DVC_POINTER_MAX_BYTES:
            offenders.append(
                (path, [f"{size} bytes, far larger than a DVC pointer"])
            )
            continue

        data = blob_bytes(sha)
        if data.startswith(ZIP_MAGIC):
            members = plan_members_of_blob(data)
            offenders.append(
                (path, [f"zip archive containing: {', '.join(members)}"] if members
                 else ["a zip archive, not a pointer"])
            )
        elif b"outs:" not in data:
            offenders.append((path, ["no `outs:` key: not a DVC pointer"]))
    return offenders


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

    return _parse_ls_files(listing)


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
        return report(
            scan_staged() + pointer_offenders(staged_pointer_entries())
        )

    # Reads blobs, never the worktree: see `tracked_entries`. Nothing here
    # opens or stats a path, so a symlink, a symlinked ancestor and a hard
    # link into data/ are all equally irrelevant.
    offenders: list[tuple[Path, list[str]]] = []
    for path, sha in tracked_entries():
        members = plan_members_of_blob(blob_bytes(sha))
        if members:
            offenders.append((path, members))

    return report(offenders + pointer_offenders(tracked_pointer_entries()))


def report(offenders: list[tuple[Path, list[str]]]) -> int:
    if not offenders:
        return 0

    print("The following tracked files must not be in Git:")
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
