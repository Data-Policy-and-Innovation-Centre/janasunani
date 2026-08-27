"""Tests for scripts/check_no_terraform_plans.py.

Codex P1 on #321: the first version of this guard matched `.*\\.tfplan` in the
workflow regex, which is the same failure one step removed — `-out` takes an
arbitrary filename and `terraform plan -out=tfplan` produces a plan with no
suffix at all. The cases that matter here are the ones a name-based check
cannot see.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.check_no_terraform_plans import (  # noqa: E402
    main,
    plan_members,
    tracked_files,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_no_terraform_plans.py"


def _plan_archive(path: Path, members: tuple[str, ...] = ("tfstate", "tfplan")) -> Path:
    """A zip shaped like a Terraform saved plan."""
    with zipfile.ZipFile(path, "w") as archive:
        for name in members:
            archive.writestr(name, '{"serial": 1, "outputs": {}}')
    return path


def test_a_plan_with_no_suffix_is_caught(tmp_path):
    """`terraform plan -out=tfplan` is Terraform's own documented example."""
    plan = _plan_archive(tmp_path / "tfplan")
    assert plan_members(plan) == ["tfplan", "tfstate"]


@pytest.mark.parametrize(
    "name", ["tfplan", "plan.out", "bucket.tfplan", "saved", "artifact.bin"]
)
def test_the_verdict_does_not_depend_on_the_filename(tmp_path, name):
    assert plan_members(_plan_archive(tmp_path / name, ("tfstate",))) == ["tfstate"]


def test_a_plan_written_under_a_directory_prefix_is_caught(tmp_path):
    # Layout has moved across Terraform versions, so members are matched on
    # their base name rather than the full entry path.
    plan = _plan_archive(tmp_path / "p", ("plan/tfstate",))
    assert plan_members(plan) == ["plan/tfstate"]


def test_an_ordinary_zip_is_not_a_plan(tmp_path):
    ordinary = tmp_path / "deck.pptx"
    with zipfile.ZipFile(ordinary, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<p:presentation/>")
    assert plan_members(ordinary) == []


def test_non_zip_files_are_cheap_and_clean(tmp_path):
    text = tmp_path / "main.tf"
    text.write_text('resource "aws_s3_bucket" "x" {}\n')
    assert plan_members(text) == []

    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    assert plan_members(empty) == []


def test_a_truncated_zip_does_not_crash_the_check(tmp_path):
    # Starts with the zip magic and will not open. It must not raise: an
    # unreadable file is not evidence of a plan, and the suffix patterns in
    # the workflow still cover the obvious names.
    broken = tmp_path / "half.zip"
    broken.write_bytes(b"PK\x03\x04" + b"\x00" * 16)
    assert plan_members(broken) == []


def test_reports_the_offender_and_its_members(tmp_path, monkeypatch, capsys):
    plan = _plan_archive(tmp_path / "tfplan")
    monkeypatch.setattr(
        "scripts.check_no_terraform_plans.tracked_files", lambda: [plan]
    )

    assert main() == 1
    out = capsys.readouterr().out
    assert "tfplan" in out
    assert "tfstate" in out
    # The message has to say why, or the next person deletes the file and
    # saves the plan under another name.
    assert "state stays local" in out


def test_passes_when_nothing_is_tracked_as_a_plan(tmp_path, monkeypatch, capsys):
    ordinary = tmp_path / "main.tf"
    ordinary.write_text("# nothing\n")
    monkeypatch.setattr(
        "scripts.check_no_terraform_plans.tracked_files", lambda: [ordinary]
    )

    assert main() == 0
    assert capsys.readouterr().out == ""


def test_this_repository_currently_passes():
    """The check the workflow runs, run against the real index."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=SCRIPT.parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout


def test_tracked_files_reads_the_git_index(monkeypatch):
    monkeypatch.chdir(SCRIPT.parents[1])
    files = tracked_files()
    assert Path("pyproject.toml") in files
    assert Path(".github/workflows/data-check.yml") in files


def test_the_regex_alone_would_have_missed_it(tmp_path):
    """Why the content check exists, pinned as a test rather than a comment.

    The workflow's suffix pattern is the one from the first fix. It matches
    `bucket.tfplan` and not `tfplan`, which is exactly the gap.
    """
    import re

    pattern = re.compile(
        r"(^|/)(terraform\.tfstate(\..*)?|terraform\.tfvars|.*\.auto\.tfvars"
        r"|.*\.tfplan|\.env|.*\.pem|id_rsa|id_ed25519)$"
    )

    assert pattern.search("deploy/terraform/bucket.tfplan")
    assert not pattern.search("deploy/terraform/tfplan")
    assert not pattern.search("deploy/terraform/plan.out")

    # Both of the names the regex lets through are caught by content.
    for name in ("tfplan", "plan.out"):
        assert plan_members(_plan_archive(tmp_path / name, ("tfstate",)))
