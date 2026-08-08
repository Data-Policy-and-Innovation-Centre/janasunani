"""Every public Makefile target is listed in `make help`."""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from janasunani.config import ROOT_DIR

MAKEFILE_PATH = ROOT_DIR / "Makefile"

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")

# Targets that exist for Make internals or pattern rules but are not operator
# commands — they should not appear in `make help`.
_HELP_EXCLUDED_TARGETS = frozenset(
    {
        "help",
        "_check_git_clean",
    }
)


def _public_makefile_targets() -> frozenset[str]:
    text = MAKEFILE_PATH.read_text()
    found = re.findall(r"^([a-z][-a-z0-9]*):", text, re.MULTILINE)
    return frozenset(found) - _HELP_EXCLUDED_TARGETS


def _help_listed_targets(help_text: str) -> frozenset[str]:
    return frozenset(re.findall(r"^\s+make ([a-z][-a-z0-9]*)\s+", help_text, re.MULTILINE))


def test_help_lists_every_public_target():
    result = subprocess.run(
        ["make", "-C", str(ROOT_DIR), "-f", str(MAKEFILE_PATH), "help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    public = _public_makefile_targets()
    listed = _help_listed_targets(result.stdout)
    missing = sorted(public - listed)
    assert not missing, f"make help is missing: {', '.join(missing)}"
