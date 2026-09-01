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
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_provenance_sidecars import check_payload  # noqa: E402

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

    # Only the base name is returned, never the archive path it sat under.
    # The directory part is attacker-chosen text and `report()` prints into
    # public CI logs; the base name is one of PLAN_MEMBERS and so is safe.
    return sorted({Path(name).name for name in names if Path(name).name in PLAN_MEMBERS})


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


# Mirrors MAX_BYTES in scripts/check_provenance_sidecars.py. A per-value
# cap alone is not a bound: citizen text split across many recognised
# fields keeps every scalar under the limit while the payload stays large.
ALLOWLISTED_MAX_BYTES = 16 * 1024

# Mirrors the allowlist in the raw-data step of .github/workflows/data-check.yml.
# Every name that step lets through must be validated here, or the exemption
# becomes a way in: the predicate checks the name and nothing else.
ALLOWLIST_PATHSPECS = (
    "data/**/*.dvc",
    "data/*.dvc",
    "data/**/.gitkeep",
    "data/.gitkeep",
    "data/external/**/provenance.json",
    "data/external/**/*.provenance.json",
    # `**/` requires at least one directory below `external`, but the
    # workflow's `(.*/)?` also allows the sidecar to sit directly there.
    "data/external/provenance.json",
    "data/external/*.provenance.json",
)

# The keys a .dvc file may carry. Anything else is unrecognised content, and
# unrecognised content in an allowlisted file is the whole hazard: a pointer
# is exempt from the data rules because of what it is, so anything that is not
# that has to be refused rather than ignored.
# A pointer needs none of DVC's free-text fields, and a per-value length cap
# cannot tell short citizen text from a legitimate description. So `desc`,
# `meta` and `cmd` are not accepted here at all: there is no field in which
# arbitrary prose is allowed, which is a stronger statement than any cap. A
# real pointer needing one fails loudly and a person decides.
DVC_TOP_LEVEL_KEYS = frozenset({"outs", "deps", "wdir", "md5", "frozen"})
DVC_ENTRY_KEYS = frozenset(
    {"md5", "hash", "etag", "checksum", "path", "size", "nfiles", "isexec",
     "remote", "cache", "persist", "push", "files"}
)

# Every accepted value has a shape. Checked by pattern, so a field cannot be
# used as a container for something else.
HEXISH = re.compile(r"\A[0-9a-fA-F]{8,64}(\.dir)?\Z")
TOKEN = re.compile(r"\A[A-Za-z0-9_.-]{1,64}\Z")
INTEGER = re.compile(r"\A[0-9]{1,20}\Z")
BOOLEAN = frozenset({"true", "false", "True", "False"})
MAX_PATH = 255
BLOCK_SCALARS = frozenset({"|", ">", "|-", ">-", "|+", ">+"})

# Mirrors MAX_STRING in scripts/check_provenance_sidecars.py, and for the
# same reason given there: prose does not survive a 200-character scalar
# cap. Recognising a key is not enough when its value is free text.
MAX_SCALAR = 200


def _ls_files_entries(pathspecs: tuple[str, ...]) -> list[tuple[Path, str]]:
    listing = subprocess.run(
        ["git", "ls-files", "-s", "-z", "--", *pathspecs],
        check=True,
        capture_output=True,
    ).stdout
    return _parse_ls_files(listing)


def tracked_allowlisted_entries() -> list[tuple[Path, str]]:
    """``(path, sha)`` for every tracked blob the data allowlist exempts."""
    return _ls_files_entries(ALLOWLIST_PATHSPECS)


def staged_allowlisted_entries() -> list[tuple[Path, str]]:
    """The same, restricted to what is staged for the next commit."""
    changed = subprocess.run(
        [
            "git", "diff", "--cached", "--name-only", "-z",
            "--diff-filter=ACMR", "--", *ALLOWLIST_PATHSPECS,
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


def _scalar_problem(key: str, value: str) -> str | None:
    """Why a recognised field's *value* is not acceptable, if it is not.

    Validating the key and leaving the value free defeats the point: `cmd`,
    `desc` and `meta` are recognised DVC fields, and citizen text placed in
    one of them sits in a file the data rules exempt. Reported by key and
    length only -- never by content, since this runs in CI whose logs are
    public.
    """
    if any(ord(ch) < 32 for ch in value):
        return f"{key!r} contains control characters"

    if key in {"md5", "etag", "checksum"}:
        return None if HEXISH.match(value) else f"{key!r} is not a checksum"
    if key == "hash":
        return None if TOKEN.match(value) else f"{key!r} is not a hash name"
    if key in {"size", "nfiles"}:
        return None if INTEGER.match(value) else f"{key!r} is not an integer"
    if key in {"isexec", "cache", "persist", "push", "frozen"}:
        return None if value in BOOLEAN else f"{key!r} is not a boolean"
    if key in {"path", "wdir", "remote"}:
        if len(value) > MAX_PATH:
            return f"{key!r} is {len(value)} characters, over the {MAX_PATH} cap"
        return None

    if len(value) > MAX_SCALAR:
        return f"{key!r} is {len(value)} characters, over the {MAX_SCALAR} cap"
    return None


def dvc_pointer_problem(text: str) -> str | None:
    """Why ``text`` is not a DVC pointer, or ``None`` if it is one.

    Parsed structurally rather than searched, and closed rather than lenient:
    every line must be recognised. A substring test for ``outs:`` passes any
    prose containing those bytes, and merely finding a well-formed ``outs``
    entry is not enough either -- a pointer that also carries ``raw: |`` and
    an indented block of citizen text is still a file smuggled into an
    allowlisted name. So unknown top-level keys, unknown entry keys, block
    scalars and any indented line outside a list are all refused.

    Deliberately hand-rolled: this module is stdlib-only so the hook runs
    before any `uv sync`, and PyYAML is not available to it. Being strict
    about shape is the right trade for a guard that must run everywhere.

    No diagnostic here quotes the file's contents. A rejected line is named
    by number, and a rejected key by number too whenever the key itself is
    unrecognised and therefore arbitrary text. Only names already matched
    against the allowlists are printed. This runs in CI, whose logs are
    public: reporting the thing you are refusing to publish defeats the gate.
    """
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "empty"

    items: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    list_key: str | None = None

    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        indented = line[:1].isspace()

        if not indented and not stripped.startswith("-"):
            key, sep, value = stripped.partition(":")
            if not sep:
                return f"line {number}: not a YAML mapping"
            key = key.strip()
            if key not in DVC_TOP_LEVEL_KEYS:
                return f"line {number}: unrecognised top-level key"
            if value.strip() in BLOCK_SCALARS:
                return f"block scalar not allowed at {key!r}"
            problem = _scalar_problem(key, value.strip())
            if problem:
                return problem
            list_key = key if key in {"outs", "deps"} and not value.strip() else None
            current = None
            continue

        if list_key is None:
            return f"line {number}: unexpected content"

        if stripped.startswith("- "):
            current = {}
            if list_key == "outs":
                items.append(current)
            stripped = stripped[2:]
        elif stripped.startswith("-"):
            return f"line {number}: malformed list item"

        if current is None:
            return f"line {number}: unexpected content"

        key, sep, value = stripped.partition(":")
        if not sep:
            return f"line {number}: not a key"
        key = key.strip()
        if key not in DVC_ENTRY_KEYS:
            return f"line {number}: unrecognised key in an `outs:` entry"
        if value.strip() in BLOCK_SCALARS:
            return f"block scalar not allowed at {key!r}"
        problem = _scalar_problem(key, value.strip())
        if problem:
            return problem
        current[key] = value.strip()

    if not items:
        return "no `outs:` entries"
    for item in items:
        if "path" not in item:
            return "an `outs:` entry has no `path`"
        if not {"md5", "hash", "etag", "checksum"} & set(item):
            return "an `outs:` entry has no hash"
    return None


def allowlisted_offenders(
    entries: list[tuple[Path, str]],
) -> list[tuple[Path, list[str]]]:
    """Allowlisted ``data/`` blobs that are not what their name claims.

    Excluding ``data/`` from the content scan is a data-policy requirement,
    but it means the raw-data allowlist is the only thing standing between a
    tracked file and Git. That predicate checks the *name*: `.gitkeep`, any
    `*.dvc`, and the provenance sidecars. So a saved plan committed as
    ``data/raw/saved.dvc`` or ``data/raw/.gitkeep`` passed every check.

    Reading these blobs is a deliberate, narrow exception to the data rule
    and stays inside it in the way that matters. Each is meant to be a marker,
    a few hundred bytes of pointer YAML, or a provenance sidecar -- not
    citizen data -- and each is read precisely to prove that is all it is.
    Size is taken from ``git cat-file -s`` first and anything large is refused
    unread, so no bulk file under ``data/`` is ever opened.
    """
    offenders: list[tuple[Path, list[str]]] = []
    for path, sha in entries:
        size = blob_size(sha)
        name = path.name

        if name == ".gitkeep":
            if size:
                offenders.append((path, [f"a .gitkeep marker must be empty; {size} bytes"]))
            continue

        if size > ALLOWLISTED_MAX_BYTES:
            offenders.append((path, [f"{size} bytes, far larger than {name} should be"]))
            continue

        data = blob_bytes(sha)
        if data.startswith(ZIP_MAGIC):
            members = plan_members_of_blob(data)
            offenders.append(
                (path, [f"zip archive containing: {', '.join(members)}"] if members
                 else ["a zip archive, not a pointer"])
            )
            continue

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            offenders.append((path, ["not text: cannot be a pointer or sidecar"]))
            continue

        if name.endswith(".dvc"):
            problem = dvc_pointer_problem(text)
            if problem:
                offenders.append((path, [f"not a DVC pointer: {problem}"]))
        else:  # provenance sidecar
            try:
                payload = json.loads(text)
            except ValueError:
                # The exception text quotes the document; this prints to
                # public CI logs, so only the fact is reported.
                offenders.append((path, ["not valid JSON"]))
                continue
            problems = check_payload(payload)
            if problems:
                offenders.append((path, problems))
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
            scan_staged() + allowlisted_offenders(staged_allowlisted_entries())
        )

    # Reads blobs, never the worktree: see `tracked_entries`. Nothing here
    # opens or stats a path, so a symlink, a symlinked ancestor and a hard
    # link into data/ are all equally irrelevant.
    offenders: list[tuple[Path, list[str]]] = []
    for path, sha in tracked_entries():
        members = plan_members_of_blob(blob_bytes(sha))
        if members:
            offenders.append((path, members))

    return report(offenders + allowlisted_offenders(tracked_allowlisted_entries()))


def report(offenders: list[tuple[Path, list[str]]]) -> int:
    if not offenders:
        return 0

    print("The following tracked files must not be in Git:")
    for path, reasons in offenders:
        print(f"  {path}  ({', '.join(reasons)})")
    print()

    # Say why, or the next person deletes the file and saves it elsewhere.
    # The two rationales are different, so print the one that applies.
    looks_like_plan = any(
        reason in PLAN_MEMBERS or "zip archive" in reason
        for _path, reasons in offenders
        for reason in reasons
    )
    if looks_like_plan:
        print(
            "A saved plan is a zip containing the state it was planned "
            "against, so it carries the account id, instance and subnet ids, "
            "SSH ingress CIDRs and the operator public key. Terraform state "
            "stays local (docs/DEPLOY.md). Detected by content, not by "
            "filename, because `-out` takes any name — `terraform plan "
            "-out=tfplan` has no suffix to match on."
        )
    if any(str(path).startswith("data/") for path, _reasons in offenders):
        print(
            "Files under data/ are exempt from the data rules only for what "
            "they are: an empty marker, a DVC pointer, or a provenance "
            "sidecar. Anything else there is refused. Rejected content is "
            "withheld on purpose: these logs are public."
        )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
