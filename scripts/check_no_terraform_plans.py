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

Deliberately dependency-free — stdlib only, so the check runs before any
`uv sync` and cannot be skipped by an environment problem.
"""

from __future__ import annotations

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
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        capture_output=True,
    ).stdout
    return [Path(name) for name in out.decode("utf-8").split("\0") if name]


def plan_members(path: Path) -> list[str]:
    """Entry names identifying ``path`` as a Terraform plan archive, if any."""
    try:
        with path.open("rb") as handle:
            if handle.read(4) != ZIP_MAGIC:
                return []
    except OSError:
        return []

    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError):
        # Starts like a zip and will not open as one. Not a plan we can
        # confirm, and not something this check should fail the build over —
        # the suffix patterns in the workflow still cover the obvious names.
        return []

    return sorted(
        {name for name in names if Path(name).name in PLAN_MEMBERS}
    )


def main() -> int:
    offenders: list[tuple[Path, list[str]]] = []
    for path in tracked_files():
        if not path.is_file():  # submodule entries, broken symlinks
            continue
        members = plan_members(path)
        if members:
            offenders.append((path, members))

    if not offenders:
        return 0

    print("The following tracked files are Terraform plan archives:")
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
    sys.exit(main())
