"""Tests for scripts/check_no_terraform_plans.py.

Codex P1 on #321: the first version of this guard matched `.*\\.tfplan` in the
workflow regex, which is the same failure one step removed — `-out` takes an
arbitrary filename and `terraform plan -out=tfplan` produces a plan with no
suffix at all. The cases that matter here are the ones a name-based check
cannot see.
"""

from __future__ import annotations

import os
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


# ---------------------------------------------------------------------------
# Codex P1 on #321, second round: the pre-commit hook matched filenames only,
# so a plan saved under an arbitrary name was committed and pushed — which is
# the disclosure — and only then rejected by CI. These cover the staged scan
# that closes that window.
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "README.md").write_text("seed\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "seed")
    return repo


def _run_staged(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--staged"],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def test_staged_scan_catches_a_plan_under_an_unignored_name(tmp_path):
    # The exact case in the finding: `deploy/terraform/saved` matches no
    # suffix pattern and no .gitignore rule, so only a content scan sees it.
    repo = _repo(tmp_path)
    (repo / "deploy" / "terraform").mkdir(parents=True)
    _plan_archive(repo / "deploy" / "terraform" / "saved")
    _git(repo, "add", "deploy/terraform/saved")

    result = _run_staged(repo)

    assert result.returncode == 1
    assert "deploy/terraform/saved" in result.stdout
    assert "tfstate" in result.stdout


def test_staged_scan_reads_the_index_not_the_worktree(tmp_path):
    # A pre-commit check must judge what is being committed. Stage the plan,
    # then overwrite the worktree copy with innocuous text: the commit still
    # carries the archive, so the check must still fail.
    repo = _repo(tmp_path)
    _plan_archive(repo / "artifact.bin")
    _git(repo, "add", "artifact.bin")
    (repo / "artifact.bin").write_text("harmless now\n")

    result = _run_staged(repo)

    assert result.returncode == 1
    assert "artifact.bin" in result.stdout


def test_staged_scan_passes_on_an_ordinary_zip(tmp_path):
    repo = _repo(tmp_path)
    deck = repo / "deck.pptx"
    with zipfile.ZipFile(deck, "w") as archive:
        archive.writestr("ppt/presentation.xml", "<p/>")
    _git(repo, "add", "deck.pptx")

    assert _run_staged(repo).returncode == 0


def test_staged_scan_passes_when_nothing_is_staged(tmp_path):
    assert _run_staged(_repo(tmp_path)).returncode == 0


def test_staged_scan_does_not_follow_a_symlink(tmp_path):
    # Same data-policy reason as the worktree scan: git stores the link text,
    # so a symlink is never itself the archive, and following one would read
    # through the data/ exclusion.
    repo = _repo(tmp_path)
    outside = tmp_path / "outside_plan"
    _plan_archive(outside)
    (repo / "link").symlink_to(outside)
    _git(repo, "add", "link")

    assert _run_staged(repo).returncode == 0


def test_pre_commit_hook_invokes_the_staged_scan():
    hook = (Path(__file__).resolve().parents[1] / ".githooks" / "pre-commit").read_text()
    invocation = [
        line
        for line in hook.splitlines()
        if "check_no_terraform_plans.py" in line and not line.lstrip().startswith("#")
    ]
    assert len(invocation) == 1, invocation
    assert "--staged" in invocation[0]
    # stdlib-only by design, so the hook works before any `uv sync`.
    assert invocation[0].lstrip().startswith("python3 ")


# ---------------------------------------------------------------------------
# Codex P2 on #321: staged *filenames* were passed to `git ls-files` as
# pathspecs, so a name that is pathspec magic was reinterpreted rather than
# selected. Both directions were reproduced before the fix.
# ---------------------------------------------------------------------------


def test_a_plan_named_like_pathspec_magic_cannot_exclude_itself(tmp_path):
    # Reproduced on ab5dcee: this exact archive passed the staged scan,
    # because `:(exclude,glob)**` was read as a pathspec that excluded
    # everything rather than as the filename it is.
    repo = _repo(tmp_path)
    _plan_archive(repo / ":(exclude,glob)**")
    _git(repo, "--literal-pathspecs", "add", ":(exclude,glob)**")

    result = _run_staged(repo)

    assert result.returncode == 1
    assert "tfstate" in result.stdout


def test_a_staged_filename_cannot_re_include_data(tmp_path):
    # The other direction, and the one with a data-policy consequence: a
    # staged file whose name is `:(glob)**` must not widen the scan back over
    # data/, which the check must never open.
    repo = _repo(tmp_path)
    (repo / "data").mkdir()
    _plan_archive(repo / "data" / "secret")
    _git(repo, "add", "-f", "data/secret")
    _git(repo, "commit", "-qm", "data")

    (repo / ":(glob)**").write_text("harmless\n")
    _git(repo, "--literal-pathspecs", "add", ":(glob)**")

    result = _run_staged(repo)

    assert result.returncode == 0, result.stdout
    assert "data/secret" not in result.stdout


def test_staged_scan_uses_literal_pathspecs():
    source = SCRIPT.read_text()
    assert "--literal-pathspecs" in source


# ---------------------------------------------------------------------------
# Codex P2 on #321: `is_symlink()` describes only the final component, so an
# ancestor replaced by a link was followed into data/.
# ---------------------------------------------------------------------------


def test_a_symlinked_ancestor_is_not_followed_into_data(tmp_path, monkeypatch):
    # Reproduced before the fix: the index still lists `public/payload`, the
    # worktree has `public -> data`, so `public/payload` is not itself a
    # symlink and opening it read `data/payload` through the
    # `:(exclude)data/**` pathspec.
    repo = _repo(tmp_path)
    (repo / "data").mkdir()
    (repo / "public").mkdir()
    _plan_archive(repo / "data" / "payload")
    (repo / "public" / "payload").write_text("placeholder\n")
    _git(repo, "add", "-f", "data/payload", "public/payload")
    _git(repo, "commit", "-qm", "seed2")

    (repo / "public" / "payload").unlink()
    (repo / "public").rmdir()
    (repo / "public").symlink_to(repo / "data")

    monkeypatch.chdir(repo)
    # Precondition: the case only bites because this is False.
    assert Path("public/payload").is_symlink() is False
    assert main() == 0


def test_open_nofollow_refuses_a_link_at_the_final_component(tmp_path):
    from scripts.check_no_terraform_plans import _open_nofollow

    target = _plan_archive(tmp_path / "real")
    link = tmp_path / "link"
    link.symlink_to(target)

    assert _open_nofollow(link) is None
    assert plan_members(link) == []
    # The archive is still found at its own path.
    assert plan_members(target) == ["tfplan", "tfstate"]


def test_open_nofollow_refuses_a_link_at_an_ancestor(tmp_path):
    from scripts.check_no_terraform_plans import _open_nofollow

    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    _plan_archive(real_dir / "payload")
    (tmp_path / "via_link").symlink_to(real_dir)

    assert _open_nofollow(tmp_path / "via_link" / "payload") is None
    assert plan_members(tmp_path / "via_link" / "payload") == []


def test_staged_scan_never_touches_the_filesystem_for_content(tmp_path):
    # The staged mode reads blobs with `git cat-file`, so symlinked ancestors
    # cannot arise there at all: deleting the worktree copy entirely leaves
    # the staged archive still detectable.
    repo = _repo(tmp_path)
    _plan_archive(repo / "artifact.bin")
    _git(repo, "add", "artifact.bin")
    (repo / "artifact.bin").unlink()

    result = _run_staged(repo)

    assert result.returncode == 1
    assert "artifact.bin" in result.stdout


# ---------------------------------------------------------------------------
# Codex P2 on #321, follow-up: the cheap prefilters still statted through a
# symlinked ancestor. AGENTS.md forbids inspecting metadata under data/, not
# only reading it, so the prefilters were removed rather than reordered.
# ---------------------------------------------------------------------------


def test_no_metadata_is_read_through_a_symlinked_ancestor(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "data").mkdir()
    (repo / "public").mkdir()
    _plan_archive(repo / "data" / "payload")
    (repo / "public" / "payload").write_text("placeholder\n")
    _git(repo, "add", "-f", "data/payload", "public/payload")
    _git(repo, "commit", "-qm", "seed2")
    (repo / "public" / "payload").unlink()
    (repo / "public").rmdir()
    (repo / "public").symlink_to(repo / "data")

    touched: list[str] = []
    real_stat, real_lstat = os.stat, os.lstat

    def record(fn):
        def wrapper(path, *a, **kw):
            if isinstance(path, (str, Path)) and "public" in str(path):
                touched.append(str(path))
            return fn(path, *a, **kw)

        return wrapper

    monkeypatch.setattr(os, "stat", record(real_stat))
    monkeypatch.setattr(os, "lstat", record(real_lstat))
    monkeypatch.chdir(repo)

    assert main() == 0
    assert touched == [], touched


def test_a_directory_entry_is_not_mistaken_for_an_archive(tmp_path, monkeypatch):
    # The removed `is_file()` prefilter used to skip these; opening a
    # directory now fails on the read instead, which must not raise.
    repo = _repo(tmp_path)
    (repo / "adir").mkdir()
    (repo / "adir" / "keep").write_text("x\n")
    _git(repo, "add", "adir/keep")
    _git(repo, "commit", "-qm", "dir")

    monkeypatch.chdir(repo)
    assert main() == 0
    assert plan_members(Path("adir")) == []


def test_a_broken_symlink_does_not_crash_the_scan(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "dangling").symlink_to(repo / "nothing-here")
    _git(repo, "add", "dangling")
    _git(repo, "commit", "-qm", "dangling")

    monkeypatch.chdir(repo)
    assert main() == 0
