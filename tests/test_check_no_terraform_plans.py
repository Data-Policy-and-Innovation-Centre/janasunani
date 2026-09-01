"""Tests for scripts/check_no_terraform_plans.py.

Codex P1 on #321: the first version of this guard matched `.*\\.tfplan` in the
workflow regex, which is the same failure one step removed — `-out` takes an
arbitrary filename and `terraform plan -out=tfplan` produces a plan with no
suffix at all. The cases that matter here are the ones a name-based check
cannot see.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.check_no_terraform_plans import (  # noqa: E402
    _members_of,
    main,
    allowlisted_offenders,
    dvc_pointer_problem,
    tracked_allowlisted_entries,
    plan_members_of_blob,
    tracked_entries,
)


def plan_members(path: Path) -> list[str]:
    """Archive-detection helper: the scanner reads blobs, tests read files.

    The production scan never touches the filesystem (see `tracked_entries`),
    so the byte-level detection is exercised here by handing it the file's
    bytes directly.
    """
    return plan_members_of_blob(path.read_bytes())

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_no_terraform_plans.py"


def _plan_archive(path: Path, members: tuple[str, ...] = ("tfstate", "tfplan")) -> Path:
    """A zip shaped like a Terraform saved plan."""
    with zipfile.ZipFile(path, "w") as archive:
        for name in members:
            archive.writestr(name, '{"serial": 1, "outputs": {}}')
    return path


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
    # Reported by base name only: the directory part is attacker-chosen and
    # the message reaches public CI logs.
    assert plan_members(plan) == ["tfstate"]


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
    # Against a real repository rather than a patched `tracked_files`: the
    # scan reads git objects now, so the index is what it must be given.
    repo = _repo(tmp_path)
    _plan_archive(repo / "tfplan")
    _git(repo, "add", "tfplan")
    _git(repo, "commit", "-qm", "plan")
    monkeypatch.chdir(repo)

    assert main() == 1
    out = capsys.readouterr().out
    assert "tfplan" in out
    assert "tfstate" in out
    # The message has to say why, or the next person deletes the file and
    # saves the plan under another name.
    assert "state stays local" in out


def test_passes_when_nothing_is_tracked_as_a_plan(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    (repo / "main.tf").write_text("# nothing\n")
    _git(repo, "add", "main.tf")
    _git(repo, "commit", "-qm", "tf")
    monkeypatch.chdir(repo)

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


def test_tracked_entries_never_returns_anything_under_data(tmp_path, monkeypatch):
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
    files = [path for path, _sha in tracked_entries()]

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
    # The link is tracked and outside data/, so the pathspec still selects it
    # -- but it is dropped as mode 120000, and in any case the scan reads the
    # blob, which holds the link *text* rather than the target's bytes.
    assert Path("public-link") not in [path for path, _sha in tracked_entries()]

    # No finding, and nothing under data/ read.
    assert main() == 0


def test_tracked_entries_reads_the_git_index(monkeypatch):
    monkeypatch.chdir(SCRIPT.parents[1])
    files = [path for path, _sha in tracked_entries()]
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
    # Directories never appear in the scan at all now: `git ls-files -s`
    # lists blobs, so there is no entry for `adir` to misread.
    repo = _repo(tmp_path)
    (repo / "adir").mkdir()
    (repo / "adir" / "keep").write_text("x\n")
    _git(repo, "add", "adir/keep")
    _git(repo, "commit", "-qm", "dir")

    monkeypatch.chdir(repo)
    assert main() == 0
    assert Path("adir") not in [path for path, _sha in tracked_entries()]


def test_a_broken_symlink_does_not_crash_the_scan(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "dangling").symlink_to(repo / "nothing-here")
    _git(repo, "add", "dangling")
    _git(repo, "commit", "-qm", "dangling")

    monkeypatch.chdir(repo)
    assert main() == 0


# ---------------------------------------------------------------------------
# Codex P2 on #321, final round: a hard link is an ordinary directory entry,
# so no open-time flag distinguishes it. Reading git blobs rather than the
# worktree retires that whole class, and closes the damaged-archive gap.
# ---------------------------------------------------------------------------


def test_a_hard_link_into_data_is_not_read(tmp_path, monkeypatch):
    # Reproduced on 6fb981e: `public/payload` hard-linked to a plan under
    # data/ was opened and reported, because O_NOFOLLOW cannot see a hard
    # link. The blob for public/payload is its own committed content.
    repo = _repo(tmp_path)
    (repo / "data").mkdir()
    (repo / "public").mkdir()
    _plan_archive(repo / "data" / "secret")
    (repo / "public" / "payload").write_text("placeholder\n")
    _git(repo, "add", "-f", "data/secret", "public/payload")
    _git(repo, "commit", "-qm", "seed2")

    (repo / "public" / "payload").unlink()
    os.link(repo / "data" / "secret", repo / "public" / "payload")

    monkeypatch.chdir(repo)
    assert Path("public/payload").is_symlink() is False  # precondition
    assert main() == 0


def test_a_damaged_plan_is_still_caught(tmp_path):
    # A truncated plan keeps its secrets: the tfstate entry is in the archive
    # whether or not the central directory survives, and an interrupted
    # `terraform plan -out` produces exactly this.
    plan = _plan_archive(tmp_path / "plan", ("tfstate",))
    intact = plan.read_bytes()
    assert plan_members_of_blob(intact) == ["tfstate"]

    damaged = intact[:-40]
    # The archive no longer parses as a zip...
    assert _members_of(io.BytesIO(damaged)) == []
    # ...but is still recognised, and still refused.
    assert plan_members_of_blob(damaged) == ["tfstate"]


def test_a_damaged_plan_is_refused_by_the_staged_scan(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan_archive(repo / "artifact", ("tfstate",))
    plan.write_bytes(plan.read_bytes()[:-40])
    _git(repo, "add", "artifact")

    result = _run_staged(repo)

    assert result.returncode == 1
    assert "artifact" in result.stdout


def test_an_ordinary_damaged_zip_is_not_called_a_plan(tmp_path):
    deck = tmp_path / "deck.pptx"
    with zipfile.ZipFile(deck, "w") as archive:
        archive.writestr("ppt/presentation.xml", "<p/>")
    damaged = deck.read_bytes()[:-20]

    assert plan_members_of_blob(damaged) == []


def test_pre_commit_matches_the_workflow_filename_patterns():
    # Defence in depth alongside the content scan, and the gap Codex noted:
    # CI's filename regex included `.tfplan` and the hook's did not.
    root = Path(__file__).resolve().parents[1]
    hook = (root / ".githooks" / "pre-commit").read_text()
    workflow = (root / ".github" / "workflows" / "data-check.yml").read_text()

    for pattern in ("terraform\\.tfstate", "terraform\\.tfvars", "\\.tfplan", "\\.pem"):
        assert pattern in hook, pattern
        assert pattern in workflow, pattern


# ---------------------------------------------------------------------------
# Codex P1 on #321: excluding data/ from the content scan left a hole, because
# the raw-data allowlist accepts any name ending `.dvc` without checking what
# it is, and .gitignore un-ignores data/raw/*.dvc.
# ---------------------------------------------------------------------------


def _pointer(path: Path) -> Path:
    path.write_text("outs:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: thing\n")
    return path


def test_a_plan_committed_as_a_dvc_pointer_is_caught(tmp_path, monkeypatch):
    # Reproduced on 5d266b1: this file passed the workflow's raw-data
    # predicate and the content scan alike, carrying account id and SSH
    # ingress CIDRs into history.
    repo = _repo(tmp_path)
    (repo / "data" / "raw").mkdir(parents=True)
    _plan_archive(repo / "data" / "raw" / "saved.dvc", ("tfstate",))
    _git(repo, "add", "-f", "data/raw/saved.dvc")
    _git(repo, "commit", "-qm", "pointer")

    monkeypatch.chdir(repo)
    assert main() == 1


def test_the_same_file_is_refused_before_the_commit(tmp_path):
    repo = _repo(tmp_path)
    (repo / "data" / "raw").mkdir(parents=True)
    _plan_archive(repo / "data" / "raw" / "saved.dvc", ("tfstate",))
    _git(repo, "add", "-f", "data/raw/saved.dvc")

    result = _run_staged(repo)

    assert result.returncode == 1
    assert "saved.dvc" in result.stdout


def test_a_genuine_pointer_passes(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "data" / "raw").mkdir(parents=True)
    _pointer(repo / "data" / "raw" / "thing.dvc")
    _git(repo, "add", "-f", "data/raw/thing.dvc")
    _git(repo, "commit", "-qm", "pointer")

    monkeypatch.chdir(repo)
    assert main() == 0
    assert allowlisted_offenders(tracked_allowlisted_entries()) == []


def test_a_file_too_large_to_be_a_pointer_is_refused_unread(tmp_path, monkeypatch):
    # Refused on `git cat-file -s` alone, so no bulk file under data/ is read.
    repo = _repo(tmp_path)
    (repo / "data" / "raw").mkdir(parents=True)
    (repo / "data" / "raw" / "big.dvc").write_bytes(b"outs:\n" + b"x" * 200_000)
    _git(repo, "add", "-f", "data/raw/big.dvc")
    _git(repo, "commit", "-qm", "big")

    monkeypatch.chdir(repo)
    offenders = allowlisted_offenders(tracked_allowlisted_entries())
    assert [str(p) for p, _ in offenders] == ["data/raw/big.dvc"]
    assert "far larger than" in offenders[0][1][0]


def test_a_dvc_name_holding_something_else_entirely_is_refused(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "data" / "raw").mkdir(parents=True)
    (repo / "data" / "raw" / "notes.dvc").write_text("just some text\n")
    _git(repo, "add", "-f", "data/raw/notes.dvc")
    _git(repo, "commit", "-qm", "notes")

    monkeypatch.chdir(repo)
    assert main() == 1


# ---------------------------------------------------------------------------
# Codex P1 on #321, follow-up: the allowlist exempts more than `.dvc`, and a
# substring test for `outs:` is not a validation.
# ---------------------------------------------------------------------------


def test_a_plan_committed_as_a_gitkeep_marker_is_caught(tmp_path, monkeypatch):
    # `.gitkeep` is allowlisted by the same predicate, and the first version
    # of this fix added back only `.dvc`.
    repo = _repo(tmp_path)
    (repo / "data" / "raw").mkdir(parents=True)
    _plan_archive(repo / "data" / "raw" / ".gitkeep", ("tfstate",))
    _git(repo, "add", "-f", "data/raw/.gitkeep")
    _git(repo, "commit", "-qm", "marker")

    monkeypatch.chdir(repo)
    assert main() == 1


def test_an_empty_gitkeep_still_passes(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "data" / "raw").mkdir(parents=True)
    (repo / "data" / "raw" / ".gitkeep").write_text("")
    _git(repo, "add", "-f", "data/raw/.gitkeep")
    _git(repo, "commit", "-qm", "marker")

    monkeypatch.chdir(repo)
    assert main() == 0


def test_prose_containing_outs_is_not_a_pointer(tmp_path, monkeypatch):
    # The substring test this replaces accepted exactly this file.
    repo = _repo(tmp_path)
    (repo / "data" / "raw").mkdir(parents=True)
    (repo / "data" / "raw" / "leak.dvc").write_text(
        "some prose mentioning outs: in passing\nand more text\n"
    )
    _git(repo, "add", "-f", "data/raw/leak.dvc")
    _git(repo, "commit", "-qm", "leak")

    monkeypatch.chdir(repo)
    assert main() == 1


@pytest.mark.parametrize(
    "text, expected_ok",
    [
        ("outs:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: thing\n", True),
        ("outs:\n- hash: md5\n  md5: d41d8cd98f00b204e9800998ecf8427e\n  path: thing\n", True),
        ("wdir: .\nouts:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: thing\n", True),
        ("prose with outs: inside\n", False),
        ("outs:\n", False),
        ("outs:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n", False),          # no path
        ("outs:\n- path: thing\n", False),        # no hash
        ("", False),
    ],
)
def test_dvc_pointer_problem_parses_structure(text, expected_ok):
    assert (dvc_pointer_problem(text) is None) is expected_ok


def test_a_corrupt_provenance_sidecar_is_caught(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "data" / "external" / "thing").mkdir(parents=True)
    (repo / "data" / "external" / "thing" / "provenance.json").write_text("not json{")
    _git(repo, "add", "-f", "data/external/thing/provenance.json")
    _git(repo, "commit", "-qm", "sidecar")

    monkeypatch.chdir(repo)
    assert main() == 1


def test_a_sidecar_is_judged_by_the_owning_schema_not_by_json_syntax(tmp_path, monkeypatch):
    # `{"source": "x"}` is syntactically fine and schema-invalid. The check
    # delegates to check_provenance_sidecars.check_payload rather than
    # keeping a second, laxer notion of validity that would drift from it.
    repo = _repo(tmp_path)
    (repo / "data" / "external" / "thing").mkdir(parents=True)
    (repo / "data" / "external" / "thing" / "provenance.json").write_text('{"source": "x"}')
    _git(repo, "add", "-f", "data/external/thing/provenance.json")
    _git(repo, "commit", "-qm", "sidecar")

    monkeypatch.chdir(repo)
    assert main() == 1
    # Real sidecars, which do satisfy the schema, are covered by
    # test_the_real_repository_pointers_still_validate.


def test_the_allowlist_pathspecs_cover_the_workflow_predicate():
    # If the workflow starts exempting a new name, this check must validate
    # it too, or the exemption becomes a way in.
    from scripts.check_no_terraform_plans import ALLOWLIST_PATHSPECS

    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "data-check.yml"
    ).read_text()
    predicate = [line for line in workflow.splitlines() if "gitkeep" in line]
    assert predicate, "raw-data predicate not found"
    for token in (".gitkeep", ".dvc", "provenance"):
        assert any(token in spec for spec in ALLOWLIST_PATHSPECS), token
        assert token in predicate[0], token


# ---------------------------------------------------------------------------
# Codex P1 on #321, follow-up: `**/` needs a directory below `external`, and
# a pointer that parses is not the same as a pointer that carries nothing else.
# ---------------------------------------------------------------------------


def test_a_sidecar_directly_under_external_is_scanned(tmp_path, monkeypatch):
    # `data/external/**/provenance.json` requires a subdirectory; the
    # workflow's `(.*/)?` does not.
    repo = _repo(tmp_path)
    (repo / "data" / "external").mkdir(parents=True)
    _plan_archive(repo / "data" / "external" / "provenance.json", ("tfstate",))
    _git(repo, "add", "-f", "data/external/provenance.json")
    _git(repo, "commit", "-qm", "sidecar")

    monkeypatch.chdir(repo)
    assert main() == 1


def test_a_prefixed_sidecar_directly_under_external_is_scanned(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "data" / "external").mkdir(parents=True)
    _plan_archive(repo / "data" / "external" / "thing.provenance.json", ("tfstate",))
    _git(repo, "add", "-f", "data/external/thing.provenance.json")
    _git(repo, "commit", "-qm", "sidecar")

    monkeypatch.chdir(repo)
    assert main() == 1


def test_a_pointer_carrying_a_smuggled_block_is_refused(tmp_path, monkeypatch):
    # The entry is well formed; the file is still a container for something
    # the allowlist was never meant to exempt.
    repo = _repo(tmp_path)
    (repo / "data" / "raw").mkdir(parents=True)
    (repo / "data" / "raw" / "leak.dvc").write_text(
        "outs:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: thing\nraw: |\n  Ram Kumar, 9876543210, Sambalpur\n"
    )
    _git(repo, "add", "-f", "data/raw/leak.dvc")
    _git(repo, "commit", "-qm", "leak")

    monkeypatch.chdir(repo)
    assert main() == 1


@pytest.mark.parametrize(
    "text, expected_ok",
    [
        # Accepted: the shapes DVC actually writes.
        ("outs:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: thing\n", True),
        ("wdir: .\nouts:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: thing\n  size: 12\n", True),
        ("md5: d41d8cd98f00b204e9800998ecf8427e\nouts:\n"
         "- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: p\n"
         "deps:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: q\n", True),
        # Refused: unrecognised content, in each place it can hide.
        ("outs:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: thing\nraw: |\n  secret\n", False),
        ("outs:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: thing\n  leaked: citizen\n", False),
        ("outs: |\n  secret\n", False),
        ("outs:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: thing\nnotes: hello\n", False),
    ],
)
def test_dvc_pointer_problem_is_closed_not_lenient(text, expected_ok):
    assert (dvc_pointer_problem(text) is None) is expected_ok


def test_the_real_repository_pointers_still_validate():
    # The strictness above must not reject DVC's own output.
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=SCRIPT.parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout


# ---------------------------------------------------------------------------
# Codex P1 on #321, follow-up: recognising a key says nothing about its value,
# and JSON syntax says nothing about a sidecar's schema.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected_ok",
    [
        ("outs:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: thing\n", True),
        # Free-text fields are not accepted at all. A length cap cannot tell
        # short citizen text from a legitimate description, so there is no
        # field here in which prose is allowed.
        ("outs:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: thing\ncmd: dvc repro\n", False),
        ("outs:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: thing\ndesc: a note\n", False),
        ("outs:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: thing\n  desc: a note\n", False),
        # Values that are accepted have a shape.
        ("outs:\n- md5: not-a-checksum\n  path: thing\n", False),
        ("outs:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: thing\n  size: twelve\n", False),
        ("outs:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: thing\n  isexec: maybe\n", False),
        ("outs:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: " + "p" * 256 + "\n", False),
    ],
)
def test_recognised_fields_are_typed_not_merely_named(text, expected_ok):
    assert (dvc_pointer_problem(text) is None) is expected_ok


def test_a_rejected_value_is_reported_by_shape_never_by_content():
    # This runs in CI, whose logs are public: refusing to publish something
    # and then printing it defeats the gate.
    secret = "Ram Kumar 9876543210 Sambalpur " * 10
    problem = dvc_pointer_problem(f"outs:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: {secret}\n")

    assert problem is not None
    assert "Ram Kumar" not in problem
    assert "9876543210" not in problem
    assert "characters" in problem


def test_pre_commit_covers_sidecars_through_the_staged_scan():
    hook = (
        Path(__file__).resolve().parents[1] / ".githooks" / "pre-commit"
    ).read_text()
    # One Python call, which validates every allowlisted blob including
    # sidecars. The earlier shell pipeline could not carry a filename
    # containing a newline and failed by checking nothing.
    assert "check_no_terraform_plans.py" in hook
    assert "--staged" in hook
    assert "cat-file blob" not in hook
    assert "tr '\\0'" not in hook


def test_text_split_across_many_fields_is_still_bounded(tmp_path, monkeypatch):
    # Every scalar stays under the per-value cap while the payload does not.
    repo = _repo(tmp_path)
    (repo / "data" / "raw").mkdir(parents=True)
    lines = ["outs:", "- md5: d41d8cd98f00b204e9800998ecf8427e", "  path: thing"]
    lines += [f"  desc: Ram Kumar 9876543210 Sambalpur entry {i}" for i in range(600)]
    (repo / "data" / "raw" / "big.dvc").write_text("\n".join(lines) + "\n")
    _git(repo, "add", "-f", "data/raw/big.dvc")
    _git(repo, "commit", "-qm", "big")

    monkeypatch.chdir(repo)
    assert main() == 1


@pytest.mark.parametrize(
    "text",
    [
        "Ram Kumar 9876543210: Sambalpur\n",
        "outs:\n- md5: a\n  path: p\nRam Kumar 9876543210: x\n",
        "outs:\n- md5: a\n  path: p\n  Ram Kumar 9876543210: x\n",
        "Ram Kumar 9876543210 Sambalpur\n",
        "outs:\n- md5: a\n  path: p\n-Ram Kumar 9876543210\n",
    ],
)
def test_no_diagnostic_ever_quotes_the_file(text):
    # CI logs are public. Refusing to publish something and then printing it
    # in the rejection defeats the gate.
    problem = dvc_pointer_problem(text)

    assert problem is not None
    assert "Ram Kumar" not in problem
    assert "9876543210" not in problem
    assert "Sambalpur" not in problem


def test_rejections_still_say_where_and_why():
    # Withholding content must not make the message useless to the author.
    problem = dvc_pointer_problem(
        "outs:\n- md5: d41d8cd98f00b204e9800998ecf8427e\n  path: p\nnotes: hello\n"
    )

    assert problem is not None
    assert "line 4" in problem
    assert "top-level key" in problem


# ---------------------------------------------------------------------------
# Codex P1 on #321, final: the hook word-split its argument list, and an
# archive member's directory reached the log.
# ---------------------------------------------------------------------------


def test_a_zip_member_directory_never_reaches_the_message(tmp_path):
    plan = _plan_archive(
        tmp_path / "p", ("Ram Kumar 9876543210 Sambalpur/tfstate",)
    )

    members = plan_members(plan)

    assert members == ["tfstate"]
    assert not any("Ram Kumar" in member for member in members)


def test_a_sidecar_path_with_a_space_is_still_checked(tmp_path):
    # Argument handling, exercised against a real repository with the hook
    # installed rather than asserted on the script text.
    repo = _repo(tmp_path)
    hooks = repo / ".githooks"
    hooks.mkdir()
    root = Path(__file__).resolve().parents[1]
    (hooks / "pre-commit").write_text((root / ".githooks" / "pre-commit").read_text())
    (hooks / "pre-commit").chmod(0o755)
    (repo / "scripts").mkdir()
    for name in ("check_provenance_sidecars.py", "check_no_terraform_plans.py"):
        (repo / "scripts" / name).write_text((root / "scripts" / name).read_text())
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "hooks")
    _git(repo, "config", "core.hooksPath", ".githooks")

    (repo / "data" / "external").mkdir(parents=True)
    bad = repo / "data" / "external" / "my sidecar.provenance.json"
    bad.write_text('{"complaint": "citizen text"}')
    _git(repo, "add", "-f", "data/external/my sidecar.provenance.json")

    result = subprocess.run(
        ["git", "commit", "-m", "sidecar"], cwd=repo, capture_output=True, text=True
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert "my sidecar.provenance.json" in (result.stdout + result.stderr)


