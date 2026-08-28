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


def test_tracked_files_never_returns_anything_under_data(tmp_path, monkeypatch):
    """Codex P1 on #321: the scan opens every path it is handed.

    AGENTS.md forbids reading anything under `data/` without explicit
    per-path permission, and the repository tracks .dvc pointers and
    provenance sidecars there. Nothing is lost by excluding it: the workflow
    step before this one already rejects any tracked file under `data/` that
    is not one of those, so a plan archive hidden there fails a step earlier.

    In a throwaway repository, never the real one. The first version of this
    test proved non-vacuousness by running `git ls-files -- data/` against
    the checkout, which enumerated every protected path and reproduced the
    exact violation the fix had just removed. A synthetic repo gives the
    stronger guarantee -- files under `data/` provably exist and are
    provably excluded -- while touching nothing real.
    """
    repo = tmp_path / "repo"
    (repo / "data" / "external").mkdir(parents=True)
    (repo / "deploy").mkdir()
    (repo / "data" / "secret.parquet").write_bytes(b"pretend citizen data")
    (repo / "data" / "external" / "thing.json.dvc").write_text("outs: []\n")
    (repo / "deploy" / "main.tf").write_text("# infra\n")
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    # -f, because a global core.excludesFile that ignores data/ would leave
    # the synthetic protected files untracked while the commit still
    # succeeded on the others -- and then this test would pass even with the
    # scanner's exclusion removed, which is the vacuousness it exists to rule
    # out.
    for args in (["init", "-q"], ["add", "-Af"],
                 ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    # Asserted against this throwaway repo's index, never the real one.
    indexed = subprocess.run(
        ["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.split()
    assert "data/secret.parquet" in indexed
    assert "data/external/thing.json.dvc" in indexed

    monkeypatch.chdir(repo)
    files = tracked_files()

    offenders = [f for f in files if str(f).startswith("data/")]
    assert offenders == [], f"scan would open protected paths: {offenders}"
    # And the rest of the tree is still scanned.
    assert Path("pyproject.toml") in files
    assert Path("deploy/main.tf") in files


def test_a_symlink_into_data_is_not_followed(tmp_path, monkeypatch):
    """Codex P1 on #321: the pathspec excludes paths, not link targets.

    `is_file()` follows a symlink, so a tracked link *outside* data/ whose
    target is inside it was read straight through the exclusion — and would
    have been reported by name and members if the target were a plan.
    """
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    _plan_archive(repo / "data" / "hidden-plan")
    (repo / "public-link").symlink_to(Path("data") / "hidden-plan")
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    for args in (["init", "-q"], ["add", "-Af"],
                 ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    monkeypatch.chdir(repo)
    # The link itself is tracked and outside data/, so the pathspec returns it.
    assert Path("public-link") in tracked_files()

    # It must not be followed: no finding, and nothing under data/ opened.
    assert main() == 0


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
