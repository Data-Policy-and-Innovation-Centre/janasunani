"""Real-code-path checks for the Makefile's `.env`-sourced settings (#60 and
its sequels, #103, #104, #114): that OLTP_DB_URL survives adversarial
characters through every documented precedence tier (command line,
shell-exported environment, `.env`), that a parse-time `uv` failure fails
loudly rather than silently keeping the throwaway demo default, that `.env`
still supplies the other Make settings README.md documents (BOX_REMOTE,
BOX_PROJECT_ROOT, INCOMING_REMOTE, EXHIBITS_REMOTE), that `setup` (the
install/repair target) stays runnable without `uv` already installed, and
that the parse-time `uv run` call cannot hang waiting on network.

Shells out to the actual `make` binary against the real Makefile -- not a
copy, not a reimplementation of its logic -- with `-C` pointed at an isolated
`tmp_path` so a developer's own real `.env` at the repo root can never leak
into (or be mistaken for) the command-line/environment-tier assertions here.
`-n` (dry run) is used everywhere it is safe to; the one exception is `db`'s
guard, which is exercised for real because dry run only ever *prints* the
recipe text and cannot tell us which branch of `if [ ... ]` a real shell
would take -- and a real run here is safe: the guard's whole job is to exit
before touching Docker when OLTP_DB_URL is not the throwaway demo default,
which is exactly the case under test.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from janasunani.config import ROOT_DIR

MAKEFILE_PATH = ROOT_DIR / "Makefile"

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")

# Exercises every character class this bug's four rounds each fixed one at a
# time: `$` (Make variable-reference syntax), `#` (Make/shell comment
# syntax), `'` (shell quote), and a trailing " # ..." that *looks* like a
# dotenv inline comment. That last one must survive intact for the
# command-line/environment tiers (see test_command_line_value_is_not_treated_
# as_dotenv_text below) -- only python-dotenv's parse of an actual `.env`
# file treats it as a real comment and strips it
# (test_dotenv_value_survives_intact_and_its_real_comment_is_stripped).
ADVERSARIAL_DSN = (
    "postgresql+asyncpg://postgres:pa$word#hash'quote@127.0.0.1:5544/janasunani"
    " # not a real comment"
)


def _make(*args: str, cwd, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["make", "-C", str(cwd), "-f", str(MAKEFILE_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _sh_quote(value: str) -> str:
    """Python port of the Makefile's `sh_quote`: wrap in single quotes, with
    each embedded `'` replaced by `'\\''` (close the quote, an escaped
    literal quote outside it, reopen the quote). ADVERSARIAL_DSN contains a
    `'`, so its *quoted* form is not a contiguous copy of the original string
    -- sh_quote splits it at that point by design -- and assertions below
    must compare against this, not the raw value.
    """
    return "'" + value.replace("'", "'\\''") + "'"


@pytest.fixture
def isolated_dir(tmp_path):
    """No `.env` here, ever. Isolates the command-line/environment-tier
    assertions below from whatever `.env` (if any) exists at the real repo
    root -- command-line values already beat `.env` unconditionally (Make
    locks in command-line variables before reading any of the makefile), but
    the shell-exported-environment tier does not: `.env` > shell export is
    the documented precedence, so a real `.env` with its own OLTP_DB_URL
    would silently invalidate that specific assertion if this ran against
    ROOT_DIR directly.
    """
    return tmp_path


@pytest.mark.parametrize("target", ["preflight", "api", "up"])
def test_command_line_override_survives_intact(isolated_dir, target):
    result = _make(
        "-n", f"OLTP_DB_URL={ADVERSARIAL_DSN}", target, cwd=isolated_dir
    )
    assert result.returncode == 0, result.stderr
    assert _sh_quote(ADVERSARIAL_DSN) in result.stdout, result.stdout


def test_shell_exported_environment_value_survives_intact(isolated_dir):
    env = dict(os.environ)
    env["OLTP_DB_URL"] = ADVERSARIAL_DSN
    result = _make("-n", "preflight", cwd=isolated_dir, env=env)
    assert result.returncode == 0, result.stderr
    assert _sh_quote(ADVERSARIAL_DSN) in result.stdout, result.stdout


def test_dotenv_value_survives_intact_and_its_real_comment_is_stripped(isolated_dir):
    """The third tier (#60's original bug, fixed in 17bad32 by shelling out
    to python-dotenv): unlike the command-line/environment tiers above, a
    genuine `.env` file's ` # ...` *is* a real dotenv inline comment and must
    be stripped -- Settings (janasunani/config.py) would strip it too, and
    the whole point of parsing via python-dotenv instead of hand-rolled sed
    is that Make and Settings agree on where the value ends."""
    dsn_without_comment = (
        "postgresql+asyncpg://postgres:pa$word#hash'quote@127.0.0.1:5544/janasunani"
    )
    (isolated_dir / ".env").write_text(f"OLTP_DB_URL={dsn_without_comment} # local\n")
    result = _make("-n", "preflight", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert _sh_quote(dsn_without_comment) in result.stdout, result.stdout
    assert "local" not in result.stdout


def test_preflight_recipe_reconstructs_the_exact_value_at_runtime(isolated_dir):
    """Stronger than the text-matching tests above: replaces `uv` with a shim
    that writes $OLTP_DB_URL back out, then runs the *real* (non-dry-run)
    preflight recipe. Proves the shell that actually executes the recipe
    reconstructs ADVERSARIAL_DSN exactly -- not just that `-n` prints text
    that looks right -- without touching the real uv, network, or Docker.

    Shadowing `uv` needs a command-line USER_BIN override, not just a PATH
    prefix on the subprocess env: the Makefile's own
    `export PATH := $(USER_BIN):$(PATH)` (USER_BIN defaults to
    ~/.local/bin, where uv is commonly installed) puts USER_BIN *ahead* of
    whatever PATH this test starts with, so a real uv there would still win
    over a merely-prepended fake one.
    """
    fake_bin = isolated_dir / "fakebin"
    fake_bin.mkdir()
    shim = fake_bin / "uv"
    output_file = isolated_dir / "fake_uv_saw.txt"
    shim.write_text(
        "#!/bin/sh\n"
        f'printf %s "$OLTP_DB_URL" > "{output_file}"\n'
    )
    shim.chmod(0o755)

    result = _make(
        f"OLTP_DB_URL={ADVERSARIAL_DSN}",
        f"USER_BIN={fake_bin}",
        "preflight",
        cwd=isolated_dir,
    )
    assert result.returncode == 0, result.stderr
    assert output_file.read_text() == ADVERSARIAL_DSN


def test_command_line_value_is_not_treated_as_dotenv_text(isolated_dir):
    """The trailing ` # not a real comment` in ADVERSARIAL_DSN is only ever
    stripped by python-dotenv's parse of an actual `.env` file (see
    Makefile's OLTP_DB_URL_RAW block) -- a command-line value never goes
    through that parser, so it must come through whole, comment-looking
    suffix and all. This is the inverse of test_makefile's `.env` coverage:
    that one proves the comment *is* stripped from `.env`; this one proves
    it is *not* stripped from a literal command-line string."""
    result = _make("-n", f"OLTP_DB_URL={ADVERSARIAL_DSN}", "preflight", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert "# not a real comment" in result.stdout


def test_db_guard_skips_provisioning_for_a_command_line_dsn_with_special_characters(
    isolated_dir,
):
    """Real (non-dry-run) execution: the guard's job is to compare
    OLTP_DB_URL against the throwaway demo default and exit before touching
    Docker when they differ -- exactly this case, since ADVERSARIAL_DSN is
    not the demo default. Proves the *comparison itself* sees the intact
    value at runtime, not just that `-n` prints it correctly."""
    result = _make("OLTP_DB_URL=" + ADVERSARIAL_DSN, "db", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert "skipping provisioning" in result.stdout
    assert "Creating throwaway Postgres" not in result.stdout


def test_dotenv_extraction_finds_uv_installed_only_under_user_bin(isolated_dir):
    """#103: the parse-time `uv run python ...` call used to look `uv` up on
    whatever PATH the invoking shell already had -- before the later
    `export PATH := $(USER_BIN):$(PATH)` line, which only reaches recipe
    subprocesses, not this earlier $(shell ...) call. Reproduces the
    documented install shape (uv under ~/.local/bin) by removing exactly
    that directory from PATH (keeping the rest of the real environment
    intact, unlike the fully-stripped env below -- `uv run` needs more than
    a bare PATH to resolve this repo's project/dependencies from an
    unrelated tmp_path) and pointing USER_BIN at it instead."""
    real_uv = shutil.which("uv")
    if real_uv is None:
        pytest.skip("uv not installed on this machine")
    user_bin = str(Path(real_uv).parent)
    (isolated_dir / ".env").write_text(
        "OLTP_DB_URL=postgresql+asyncpg://postgres:fromenv@127.0.0.1:5544/janasunani\n"
    )
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(
        p for p in env.get("PATH", "").split(os.pathsep) if p != user_bin
    )
    assert shutil.which("uv", path=env["PATH"]) is None, "test setup: uv must not resolve via PATH"
    result = _make(
        "-n", f"USER_BIN={user_bin}", "preflight", cwd=isolated_dir, env=env
    )
    assert result.returncode == 0, result.stderr
    assert "fromenv" in result.stdout, result.stdout


def test_missing_uv_fails_loudly_instead_of_silently_using_the_demo_default(isolated_dir):
    """#103's more dangerous half: when `uv` cannot be resolved at all (not
    on PATH, not under USER_BIN), the extraction must not silently leave
    OLTP_DB_URL at the throwaway demo default -- `.env` names a real
    database here, and silently ignoring that is exactly the outcome `make
    OLTP_DB_URL=... db`'s guard exists to prevent, reached by a different
    route (a broken uv install instead of a forgotten override)."""
    (isolated_dir / ".env").write_text(
        "OLTP_DB_URL=postgresql+asyncpg://postgres:fromenv@127.0.0.1:5544/janasunani\n"
    )
    env = {"HOME": os.environ.get("HOME", ""), "PATH": "/usr/bin:/bin"}
    result = _make(
        "-n", "USER_BIN=/nonexistent-dir-for-this-test", "preflight",
        cwd=isolated_dir, env=env,
    )
    assert result.returncode != 0
    assert "could not be parsed" in result.stderr
    demo_default = "postgresql+asyncpg://postgres:demo@127.0.0.1:5544/janasunani"
    assert demo_default not in result.stdout, (
        "must fail loudly, not silently resolve to the throwaway demo default"
    )


# --- #114: the parse-time `uv` dependency must not make `setup` (the
# install/repair target) unbootstrappable, and must not let a transient
# network hiccup during environment sync escalate into a multi-minute hang
# that then hard-aborts every target ----------------------------------------


def test_setup_bypasses_dotenv_extraction_when_uv_is_unresolvable(isolated_dir):
    """Before this fix, the parse-time dotenv extraction ran before any
    target was selected, so a checkout with a root `.env` but no `uv`
    installed made even `make setup` fail with 'uv: not found' -- while
    `scripts/setup.sh`'s install_uv() is the documented mechanism that
    installs it. That is a bootstrap cycle: the repair target can't run
    because the environment is unrepaired.

    `setup`'s own recipe never touches OLTP_DB_URL or the Box keys, so the
    Makefile now skips the whole `ifneq (,$(wildcard .env))` extraction
    whenever `setup` is among `$(MAKECMDGOALS)`. Proven here with `uv`
    genuinely unresolvable (PATH stripped to /usr/bin:/bin, USER_BIN pointed
    at a nonexistent directory) and a `.env` present -- before the fix this
    combination hit the hard $(error) before printing anything.

    `MAKE=true` replaces the real recursive `$(MAKE) install-hooks` call at
    the end of `setup`'s recipe: GNU Make always executes a recipe line that
    references the MAKE variable for real, even under `-n` (documented
    behavior, verified directly against this Makefile) -- so a plain `-n`
    here would actually re-invoke `install-hooks`, a target this test does
    not want to exercise (it needs a real git checkout and the real `uv`
    install `scripts/setup.sh` performs before it runs, neither of which
    belongs in this unit test, and neither of which #114 is about: the
    bypass is for `setup` only, not `install-hooks`). Substituting `true`
    keeps that line real-and-run -- so the dry run still faithfully reflects
    Make's actual behavior -- while making it a no-op.
    """
    (isolated_dir / ".env").write_text(
        "OLTP_DB_URL=postgresql+asyncpg://postgres:fromenv@127.0.0.1:5544/janasunani\n"
    )
    env = {"HOME": os.environ.get("HOME", ""), "PATH": "/usr/bin:/bin"}
    result = _make(
        "-n", "USER_BIN=/nonexistent-dir-for-this-test", "MAKE=true", "setup",
        cwd=isolated_dir, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scripts/setup.sh" in result.stdout, result.stdout
    assert "could not be parsed" not in result.stderr
    assert "could not be parsed" not in result.stdout


def test_setup_bypass_does_not_broaden_to_other_targets(isolated_dir):
    """Control for the test above: the `setup` bypass is scoped to `setup`
    specifically, not a blanket skip -- any other target under the same
    unresolvable-`uv` conditions still hits #103's hard $(error), unchanged
    (same assertion as test_missing_uv_fails_loudly_..., with a different
    target to show it is not `preflight`-specific)."""
    (isolated_dir / ".env").write_text(
        "OLTP_DB_URL=postgresql+asyncpg://postgres:fromenv@127.0.0.1:5544/janasunani\n"
    )
    env = {"HOME": os.environ.get("HOME", ""), "PATH": "/usr/bin:/bin"}
    result = _make(
        "-n", "USER_BIN=/nonexistent-dir-for-this-test", "status",
        cwd=isolated_dir, env=env,
    )
    assert result.returncode != 0
    assert "could not be parsed" in result.stderr


# --- Codex on #138, finding 2: the `setup` bypass above (needed so `setup`
# can bootstrap `uv` in the first place) must not silently drop a .env-only
# BOX_REMOTE -- `setup`'s own recipe still passes BOX_REMOTE to
# scripts/setup.sh's ensure_box_remote, which selects/creates the rclone
# remote from it. Exact repro from the review: a checkout with
# `.env` containing `BOX_REMOTE=mybox` and no command-line override used to
# make `make -n setup` print `BOX_REMOTE="box" bash scripts/setup.sh` (the
# built-in default, silently ignoring .env) while `make box-paths` in the
# same checkout correctly reported `BOX_REMOTE=mybox` -- two different
# answers for the same setting depending on which target asked.
#
# The first fix here just hard-failed `setup` whenever .env mentioned
# BOX_REMOTE at all -- but that put a *new* failure on the exact target
# #114 exists to keep runnable ("the repair target can't run because the
# environment is unrepaired"), and made the operator open .env, read the
# value, and retype it before `setup` would run at all, even for the
# common, perfectly safe case. The tightened fix passes a .env-only
# BOX_REMOTE straight through when it matches a narrow, conservative
# bare-identifier pattern (`^[A-Za-z0-9_-]+:?$` -- no whitespace, quotes,
# `$`, `#`, or backslash) via a bare `grep`/`sed`, not a second dotenv
# parser: for a value in that shape, there is nothing left for dotenv's
# quoting/escaping/comment rules to interpret differently, so there is
# nothing for a from-scratch reader to get wrong or drift on. Only a value
# outside that shape (quoted, spaced, commented, or more than one
# `BOX_REMOTE=` line) still hits the loud $(error) -- that is exactly the
# territory where dotenv's real rules matter and guessing would repeat
# #60's mistake.


def test_setup_passes_through_a_safe_dotenv_box_remote(isolated_dir):
    """Exact repro from the follow-up: `.env` sets a plain, unquoted
    BOX_REMOTE and there is no command-line override -- `uv` is fully
    unresolvable here (unlike the fully-available case the first version of
    this test used), proving the pass-through needs no uv/python-dotenv at
    all, matching #114's whole point. `setup` must use the .env value, not
    silently fall back to the `box` default."""
    (isolated_dir / ".env").write_text("BOX_REMOTE=mybox\n")
    env = {"HOME": os.environ.get("HOME", ""), "PATH": "/usr/bin:/bin"}
    result = _make(
        "-n", "USER_BIN=/nonexistent-dir-for-this-test", "MAKE=true", "setup",
        cwd=isolated_dir, env=env,
    )
    assert result.returncode == 0, result.stderr
    assert 'BOX_REMOTE="mybox"' in result.stdout, result.stdout


def test_setup_passes_through_a_dotenv_box_remote_with_hyphen_underscore_and_digit(
    isolated_dir,
):
    """The safe pattern covers the realistic character set for an rclone
    remote name, not just plain letters."""
    (isolated_dir / ".env").write_text("BOX_REMOTE=my-box_2\n")
    env = {"HOME": os.environ.get("HOME", ""), "PATH": "/usr/bin:/bin"}
    result = _make(
        "-n", "USER_BIN=/nonexistent-dir-for-this-test", "MAKE=true", "setup",
        cwd=isolated_dir, env=env,
    )
    assert result.returncode == 0, result.stderr
    assert 'BOX_REMOTE="my-box_2"' in result.stdout, result.stdout


def test_setup_fails_loudly_for_a_dotenv_box_remote_outside_the_safe_pattern(
    isolated_dir,
):
    """A value the safe pattern does not cover (here: single-quoted, the
    legacy-ish shape an operator might still write) must not be guessed at
    -- dotenv's real quote-stripping rules would matter here, and this path
    cannot run them (no uv yet), so it refuses instead of misreading it."""
    (isolated_dir / ".env").write_text("BOX_REMOTE='mybox'\n")
    result = _make("-n", "setup", cwd=isolated_dir)
    assert result.returncode != 0
    assert "BOX_REMOTE" in result.stderr
    assert 'BOX_REMOTE="box"' not in result.stdout, (
        "must not silently fall back to the default remote name"
    )
    assert 'BOX_REMOTE="' not in result.stdout, (
        "must not silently guess at the quoted value either"
    )


def test_setup_fails_loudly_for_a_dotenv_box_remote_containing_a_space(isolated_dir):
    """Same negative case, a different way outside the safe pattern:
    whitespace in the value."""
    (isolated_dir / ".env").write_text("BOX_REMOTE=my box\n")
    result = _make("-n", "setup", cwd=isolated_dir)
    assert result.returncode != 0
    assert "BOX_REMOTE" in result.stderr


def test_setup_fails_loudly_for_multiple_dotenv_box_remote_lines(isolated_dir):
    """.env assigning BOX_REMOTE more than once is ambiguous regardless of
    whether either individual value would otherwise be safe -- picking
    either one silently would be a guess, so this still refuses."""
    (isolated_dir / ".env").write_text("BOX_REMOTE=one\nBOX_REMOTE=two\n")
    result = _make("-n", "setup", cwd=isolated_dir)
    assert result.returncode != 0
    assert "BOX_REMOTE" in result.stderr
    assert "more than once" in result.stderr


def test_setup_with_command_line_box_remote_still_wins_over_dotenv(
    isolated_dir,
):
    """Control: the guard only fires when BOX_REMOTE's origin is the plain
    `?=` default (`file`) with a .env present -- README's documented
    workaround (`make setup BOX_REMOTE=<remote-name>`) sets BOX_REMOTE's
    origin to `command line`, which must still win even when .env's own
    value is one the safe pattern would have rejected. `MAKE=true` swaps
    out the recursive `$(MAKE) install-hooks` call the same way
    test_setup_bypasses_dotenv_extraction_when_uv_is_unresolvable does, for
    the same reason (a real -n here would otherwise actually re-invoke
    install-hooks)."""
    (isolated_dir / ".env").write_text("BOX_REMOTE='fromdotenv'\n")
    result = _make(
        "-n", "setup", "BOX_REMOTE=fromcli", "MAKE=true", cwd=isolated_dir
    )
    assert result.returncode == 0, result.stderr
    assert 'BOX_REMOTE="fromcli"' in result.stdout, result.stdout


def test_setup_without_dotenv_box_remote_is_unaffected_by_the_guard(isolated_dir):
    """Control: no .env at all (or one that never mentions BOX_REMOTE) must
    not trip the new guard -- `setup` keeps working the way #114 fixed it
    to, using the built-in `box` default."""
    result = _make("-n", "setup", "MAKE=true", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert 'BOX_REMOTE="box"' in result.stdout, result.stdout


def test_parse_time_call_fails_fast_instead_of_hanging_when_sync_needs_network(
    isolated_dir,
):
    """#114's second, independently-confirmed failure mode: a bare `uv run`
    (no `--no-sync`) resyncs the environment against
    pyproject.toml/uv.lock on every invocation -- including fetching
    metadata for any dependency not already cached -- before running the
    `-c` script that just wants `python-dotenv`. A transient network failure
    during *that* resync (reproduced directly against this repo's own
    pyproject.toml, which pins a direct-URL spaCy model wheel: `uv run
    pytest` timed out after ~2 minutes fetching it) turned this parse-time
    call into a multi-minute hang, which the `ifeq`/$(error) guards below
    then escalate to a hard abort of *every* target -- not just the one
    that actually needed OLTP_DB_URL.

    Reproduced here deterministically, without relying on real network
    flakiness: a throwaway uv project (`isolated_dir` gets its own
    `pyproject.toml`) with no synced `.venv` needs `python-dotenv` fetched
    to satisfy the parse-time import. `--offline` (added to dotenv_get's
    `uv run` alongside `--no-sync`) turns that into an immediate local
    failure instead of a network attempt, so the whole `make` invocation
    returns in well under the bound below -- proving the call cannot hang
    on network regardless of what triggers the sync attempt. `--no-sync`
    alone is not sufficient to prove here: outside of a uv project (every
    other test's `isolated_dir`) it is a silent no-op (`uv` just runs
    whatever `python` is on PATH), so this test's own `pyproject.toml` is
    what makes `--offline` the operative guard, not an incidental detail.
    """
    if shutil.which("uv") is None:
        pytest.skip("uv not installed on this machine")
    (isolated_dir / "pyproject.toml").write_text(
        "[project]\n"
        'name = "probe"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.11"\n'
        'dependencies = ["python-dotenv"]\n'
        "\n"
        "[tool.uv]\n"
        "package = false\n"
    )
    (isolated_dir / ".env").write_text(
        "OLTP_DB_URL=postgresql+asyncpg://postgres:fromenv@127.0.0.1:5544/janasunani\n"
    )

    start = time.monotonic()
    result = _make("-n", "status", cwd=isolated_dir)
    elapsed = time.monotonic() - start

    assert elapsed < 20, (
        f"parse-time uv call took {elapsed:.1f}s -- should fail fast and "
        "offline, not hang waiting on a sync that needs network"
    )
    assert result.returncode != 0
    assert "could not be parsed" in result.stderr


def test_db_guard_resolves_a_matching_default_identically(isolated_dir):
    """Control for the test above: an OLTP_DB_URL that *does* equal the
    throwaway demo default must still resolve identically to it through the
    same OLTP_DB_URL_RAW path, so the guard's `[ ... != ... ]` sees two equal
    strings -- checked directly in the substituted recipe text. `-n` only
    ever prints text linearly (both branches of the recipe appear regardless
    of which a real shell would take), so it cannot show *which* branch
    fires without actually running `db` -- which would provision a real
    throwaway Postgres via Docker for this matching case, too heavy for a
    unit test. Checking the two compared strings are identical is the
    side-effect-free equivalent: it is exactly what determines the shell's
    `!=` result."""
    demo_url = "postgresql+asyncpg://postgres:demo@127.0.0.1:5544/janasunani"
    result = _make("-n", f"OLTP_DB_URL={demo_url}", "db", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    guard_line = next(
        line for line in result.stdout.splitlines() if line.strip().startswith("if [")
    )
    assert guard_line.count(f"'{demo_url}'") == 2, guard_line


# --- #104: .env must still supply BOX_REMOTE/BOX_PROJECT_ROOT/INCOMING_REMOTE/
# EXHIBITS_REMOTE -----------------------------------------------------------
#
# README.md's "Box paths and data ops" tells collaborators to persist these
# four in .env. 17bad32 replaced `-include .env` with a parser that imported
# only OLTP_DB_URL, silently dropping the rest -- `make ingest`/`make
# deliver` then read from or published to the built-in Box paths with no
# indication the operator's own .env was ignored.


def test_dotenv_supplied_box_remote_reaches_box_paths(isolated_dir):
    """A .env-only BOX_REMOTE must actually reach the recipe that consumes
    it (`box-paths`, which just echoes the resolved Make variables --
    exactly what `ingest`/`publish-raw`/`deliver` build their rclone
    invocations from), not merely exist as a Make variable somewhere."""
    (isolated_dir / ".env").write_text("BOX_REMOTE=my-custom-remote\n")
    result = _make("box-paths", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert "BOX_REMOTE=my-custom-remote" in result.stdout, result.stdout


def test_dotenv_supplied_box_project_root_propagates_to_derived_remotes(isolated_dir):
    """BOX_PROJECT_ROOT itself contains spaces by default (its own `?=`:
    "2. Projects/21. Governance/") -- a real value operators set, not just a
    hypothetical adversarial case. INCOMING_REMOTE/EXHIBITS_REMOTE derive
    from it via their own `?=` (recursively expanded, so they pick up
    whatever BOX_PROJECT_ROOT resolves to at reference time), so a .env
    override must flow through to both without being set directly."""
    (isolated_dir / ".env").write_text("BOX_PROJECT_ROOT=DPIC/janasunani\n")
    result = _make("box-paths", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert "BOX_PROJECT_ROOT=DPIC/janasunani" in result.stdout
    assert "DPIC/janasunani/Data/Raw/" in result.stdout
    assert "DPIC/janasunani/Analysis/Exhibits/" in result.stdout


def test_dotenv_supplied_incoming_and_exhibits_remote_override_directly(isolated_dir):
    """README.md: "If a derived path doesn't match your Box layout, override
    the full endpoint (INCOMING_REMOTE / EXHIBITS_REMOTE) in .env" -- these
    must be settable independently of BOX_REMOTE/BOX_PROJECT_ROOT.

    Written with the bare form README.md documents (no manual shell quoting
    around the path) -- NOT the old hand-quoted `customremote:'Custom/Path/'`
    form this test used to write, which Codex on #138 (finding 1) flagged:
    #118's sh_quote now wraps the whole value as one shell word, so that old
    form would reach rclone with the apostrophes taken literally instead of
    consumed by the shell, silently misdirecting `ingest`/`deliver`. The
    Makefile now rejects that old form outright (see
    test_incoming_remote_with_legacy_hand_quoting_is_rejected_loudly and
    test_exhibits_remote_with_legacy_hand_quoting_is_rejected_loudly below)
    rather than reinterpreting it, so this test's job is now to prove the
    *documented* (bare) form still round-trips, not the retired one."""
    (isolated_dir / ".env").write_text(
        "INCOMING_REMOTE=customremote:Custom/Path/\n"
        "EXHIBITS_REMOTE=customremote:Other/Path/\n"
    )
    result = _make("box-paths", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert "INCOMING_REMOTE=customremote:Custom/Path/" in result.stdout
    assert "EXHIBITS_REMOTE=customremote:Other/Path/" in result.stdout
    # BOX_REMOTE/BOX_PROJECT_ROOT untouched by .env here -- still the `?=` defaults.
    assert "BOX_REMOTE=box" in result.stdout


def test_command_line_still_wins_over_dotenv_for_box_remote(isolated_dir):
    """Same precedence guarantee as OLTP_DB_URL, now proven for the restored
    keys too: command line > .env > shell export > default."""
    (isolated_dir / ".env").write_text("BOX_REMOTE=from-dotenv\n")
    result = _make("box-paths", "BOX_REMOTE=from-cmdline", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert "BOX_REMOTE=from-cmdline" in result.stdout
    assert "from-dotenv" not in result.stdout


def test_oltp_db_url_still_survives_adversarial_characters_alongside_box_remote(
    isolated_dir,
):
    """Regression guard for the #104 refactor: extending the shared
    dotenv_get machinery to four more keys must not reintroduce #60's bug
    class for OLTP_DB_URL, which still needs the $/#/'/comment handling the
    other four don't."""
    (isolated_dir / ".env").write_text(
        f"OLTP_DB_URL={ADVERSARIAL_DSN}\nBOX_REMOTE=myremote\n"
    )
    result = _make("-n", "preflight", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    dsn_without_comment = ADVERSARIAL_DSN.split(" # ")[0]
    assert _sh_quote(dsn_without_comment) in result.stdout, result.stdout


# --- #115: the DOTENV_OK: success sentinel must not corrupt a .env value
# that itself contains that literal substring, and the fix (an out-of-band
# exit-status file, not a text prefix stripped back out of the value) must
# not corrupt a value containing spaces either -------------------------------
#
# Regression case: BOX_PROJECT_ROOT=Archive/DOTENV_OK:2026 used to resolve
# to Archive/2026 because the old $(subst DOTENV_OK:,,$(raw)) extraction was
# global/unanchored -- it deleted every occurrence of the sentinel text, not
# just the one dotenv_get itself had prepended.

SENTINEL_LOOKALIKE = "Archive/DOTENV_OK:2026"


def test_dotenv_value_containing_sentinel_substring_survives_intact_box_project_root(
    isolated_dir,
):
    """The exact #115 regression case, verified end-to-end through
    box-paths -- including the two remotes BOX_PROJECT_ROOT derives, since
    those are what `ingest`/`deliver` actually build their rclone
    invocations from."""
    (isolated_dir / ".env").write_text(f"BOX_PROJECT_ROOT={SENTINEL_LOOKALIKE}\n")
    result = _make("box-paths", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert f"BOX_PROJECT_ROOT={SENTINEL_LOOKALIKE}" in result.stdout, result.stdout
    assert f"{SENTINEL_LOOKALIKE}/Data/Raw/" in result.stdout
    assert f"{SENTINEL_LOOKALIKE}/Analysis/Exhibits/" in result.stdout


def test_dotenv_value_containing_sentinel_substring_survives_for_other_box_keys(
    isolated_dir,
):
    """Representative coverage across the other keys sharing dotenv_get's
    extraction: BOX_REMOTE, INCOMING_REMOTE, EXHIBITS_REMOTE.

    INCOMING_REMOTE/EXHIBITS_REMOTE use the bare (documented) form here, not
    the old hand-quoted `customremote:'DOTENV_OK:...'` shape this test used
    to write -- that shape is now rejected outright (finding 1 on #138; see
    test_incoming_remote_with_legacy_hand_quoting_is_rejected_loudly below),
    so it would no longer reach box-paths at all."""
    (isolated_dir / ".env").write_text(
        "BOX_REMOTE=my-DOTENV_OK:-remote\n"
        "INCOMING_REMOTE=customremote:DOTENV_OK:/Incoming/\n"
        "EXHIBITS_REMOTE=customremote:DOTENV_OK:/Exhibits/\n"
    )
    result = _make("box-paths", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert "BOX_REMOTE=my-DOTENV_OK:-remote" in result.stdout, result.stdout
    assert "INCOMING_REMOTE=customremote:DOTENV_OK:/Incoming/" in result.stdout
    assert "EXHIBITS_REMOTE=customremote:DOTENV_OK:/Exhibits/" in result.stdout


def test_dotenv_value_containing_sentinel_substring_survives_for_oltp_db_url(
    isolated_dir,
):
    """Same coverage for OLTP_DB_URL, verified the same way the existing
    adversarial-character tests are: through preflight's -n recipe text and
    sh_quote, since OLTP_DB_URL (unlike the Box keys) goes through
    OLTP_DB_URL_RAW/sh_quote before reaching a recipe."""
    dsn = "postgresql+asyncpg://postgres:pwDOTENV_OK:end@127.0.0.1:5544/janasunani"
    (isolated_dir / ".env").write_text(f"OLTP_DB_URL={dsn}\n")
    result = _make("-n", "preflight", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert _sh_quote(dsn) in result.stdout, result.stdout


def test_dotenv_value_with_spaces_is_not_corrupted_by_the_status_extraction(
    isolated_dir,
):
    """The naive anchored-strip fixes #115 considered and rejected
    ($(patsubst DOTENV_OK:%,%,...) / $(filter DOTENV_OK:%,...)) both
    word-split on whitespace and would have collapsed or rewritten a
    multi-word value's internal spacing. This value has no DOTENV_OK: text
    at all, isolating the whitespace-safety property from the
    sentinel-collision property covered above."""
    (isolated_dir / ".env").write_text("BOX_PROJECT_ROOT=My Custom  Folder\n")
    result = _make("box-paths", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert "BOX_PROJECT_ROOT=My Custom  Folder" in result.stdout, result.stdout


def test_dotenv_value_with_both_spaces_and_the_sentinel_substring(isolated_dir):
    """Combines both adversarial properties #115 called out explicitly:
    spaces *and* an embedded DOTENV_OK: substring in the same value."""
    value = "My DOTENV_OK: Folder 2026"
    (isolated_dir / ".env").write_text(f"BOX_PROJECT_ROOT={value}\n")
    result = _make("box-paths", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert f"BOX_PROJECT_ROOT={value}" in result.stdout, result.stdout


def test_key_absent_vs_parse_failure_distinction_still_holds_after_115(isolated_dir):
    """#115 replaced the DOTENV_OK: stdout-prefix mechanism with an
    out-of-band exit-status file (_DOTENV_STATUS / dotenv_status). Re-proves
    the #103 invariant the new mechanism must still satisfy: a key simply
    absent from a parseable .env keeps the built-in default (status 0, no
    error), while a genuine parse failure (uv unresolvable) still hits the
    hard $(error) -- never silently keeping the default in either
    direction."""
    (isolated_dir / ".env").write_text("SOME_UNRELATED_KEY=1\n")
    result = _make("box-paths", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert "BOX_REMOTE=box" in result.stdout  # untouched ?= default

    env = {"HOME": os.environ.get("HOME", ""), "PATH": "/usr/bin:/bin"}
    result = _make(
        "-n", "USER_BIN=/nonexistent-dir-for-this-test", "box-paths",
        cwd=isolated_dir, env=env,
    )
    assert result.returncode != 0
    assert "could not be parsed" in result.stderr


# --- #118: INCOMING_REMOTE/EXHIBITS_REMOTE need quoting at the recipe
# boundary -----------------------------------------------------------------
#
# #104 restored these two from .env, but the recipes interpolated them bare
# (`rclone copy $(INCOMING_REMOTE) $(RAW_LOCAL) --progress`). A full-endpoint
# override written with normal dotenv outer quotes has those quotes stripped
# by python-dotenv -- correctly, that is what dotenv quoting means -- and the
# resulting space-bearing value then reached an unquoted recipe position,
# where the shell word-split it: `box:2. Projects/...` became several
# arguments and `make ingest` read from (or `make deliver` published to) the
# wrong place instead of failing loudly. BOX_PROJECT_ROOT's own default is
# "2. Projects/21. Governance/" -- spaces are the documented normal case
# here, not an exotic one.
#
# These tests run the real (non-dry-run) recipe with a stub `rclone` on PATH
# that records its argv one-per-line, so a passing assertion proves the
# actual argument boundary the shell constructs -- not just that `-n`'s
# printed text looks right (which passing single quotes through unchanged
# would already satisfy even if a later shell word-split them some other
# way).


def _stub_rclone(bin_dir: Path, argv_file: Path) -> None:
    """Installs a fake `rclone` on `bin_dir` that records its own argv, one
    argument per line, to `argv_file` -- instead of doing anything to a real
    Box remote. `printf '%s\\n' "$@"` recycles the format once per positional
    argument, so this is a direct readout of how many shell words the
    Makefile's recipe produced, not a parse of the recipe's source text."""
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "rclone"
    stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{argv_file}"\n')
    stub.chmod(0o755)


def _rclone_argv(isolated_dir: Path, *make_args: str) -> list[str]:
    """Runs `make` for real (no `-n`) against `isolated_dir` with a stub
    `rclone` prepended to PATH, and returns the argv that stub recorded."""
    bin_dir = isolated_dir / "fakebin"
    argv_file = isolated_dir / "rclone_argv.txt"
    _stub_rclone(bin_dir, argv_file)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    result = _make(*make_args, cwd=isolated_dir, env=env)
    assert result.returncode == 0, result.stderr
    return argv_file.read_text().splitlines()


def test_default_ingest_endpoint_matches_pre_118_behavior(isolated_dir):
    """Control: a plain, default-configured `make ingest` (no .env, no
    overrides) must produce exactly the same rclone endpoint argument #118
    does -- 'box:2. Projects/21. Governance//Data/Raw/' as a single word
    (the double slash is BOX_PROJECT_ROOT's own trailing '/' plus the
    '/Data/Raw/' suffix, an existing quirk this fix does not touch). Before
    #118 the default default achieved this via a literal single-quote baked
    into INCOMING_REMOTE's `?=` value; #118 moves that quoting to the recipe
    boundary instead (sh_quote), and this is the proof the visible behavior
    for the common, unconfigured case did not move."""
    argv = _rclone_argv(isolated_dir, "ingest")
    assert argv == [
        "copy",
        "box:2. Projects/21. Governance//Data/Raw/",
        "data/raw/",
        "--progress",
    ], argv


def test_default_deliver_endpoint_matches_pre_118_behavior(isolated_dir):
    """Same control as above, for `deliver`/EXHIBITS_REMOTE."""
    argv = _rclone_argv(isolated_dir, "deliver")
    assert argv == [
        "copy",
        "outputs/",
        "box:2. Projects/21. Governance//Analysis/Exhibits/",
        "--progress",
    ], argv


def test_dotenv_full_endpoint_override_with_spaces_reaches_rclone_as_one_argument(
    isolated_dir,
):
    """The exact #118 regression: a full-endpoint INCOMING_REMOTE override
    written with normal dotenv outer double quotes (stripped by
    python-dotenv, per dotenv semantics -- the value stored is the bare,
    unquoted, space-bearing path) must still reach rclone as ONE argument,
    not be word-split into several. Before the fix this failed exactly this
    way (verified directly against the pre-fix Makefile: 'box:2.',
    'Projects/21.', 'Governance/Custom', 'Sub/' -- four arguments instead of
    one)."""
    (isolated_dir / ".env").write_text(
        'INCOMING_REMOTE="box:2. Projects/21. Governance/Custom Sub/"\n'
    )
    argv = _rclone_argv(isolated_dir, "ingest")
    assert argv == [
        "copy",
        "box:2. Projects/21. Governance/Custom Sub/",
        "data/raw/",
        "--progress",
    ], argv


def test_dotenv_full_endpoint_override_with_spaces_reaches_rclone_for_deliver_too(
    isolated_dir,
):
    """Same regression, for EXHIBITS_REMOTE/`deliver`."""
    (isolated_dir / ".env").write_text(
        'EXHIBITS_REMOTE="box:2. Projects/21. Governance/Custom Exhibits/"\n'
    )
    argv = _rclone_argv(isolated_dir, "deliver")
    assert argv == [
        "copy",
        "outputs/",
        "box:2. Projects/21. Governance/Custom Exhibits/",
        "--progress",
    ], argv


def test_default_box_project_root_override_with_spaces_reaches_rclone_as_one_argument(
    isolated_dir,
):
    """The other documented space-bearing path: overriding BOX_PROJECT_ROOT
    (not the full endpoint) and letting INCOMING_REMOTE derive from it, per
    README.md's "Most users only need to set BOX_REMOTE and
    BOX_PROJECT_ROOT". Must reach rclone as one argument the same way."""
    (isolated_dir / ".env").write_text("BOX_PROJECT_ROOT=DPIC Janasunani Project\n")
    argv = _rclone_argv(isolated_dir, "ingest")
    assert argv == [
        "copy",
        "box:DPIC Janasunani Project/Data/Raw/",
        "data/raw/",
        "--progress",
    ], argv


def test_incoming_remote_value_containing_a_literal_quote_reaches_rclone_intact(
    isolated_dir,
):
    """sh_quote's whole reason for existing over plain single-quoting (#60):
    a value containing a literal `'` must not break the recipe's shell
    string. Here that value is INCOMING_REMOTE itself, not a DSN."""
    (isolated_dir / ".env").write_text("INCOMING_REMOTE=box:Governance's Folder/\n")
    argv = _rclone_argv(isolated_dir, "ingest")
    assert argv == [
        "copy",
        "box:Governance's Folder/",
        "data/raw/",
        "--progress",
    ], argv


# --- Codex on #138, finding 1: #118's sh_quote wraps the *whole*
# INCOMING_REMOTE/EXHIBITS_REMOTE value as one shell word, which is correct
# for the bare form README.md documents but regresses the old hand-quoted
# form README.md used to recommend (`box:'Custom/Path/'`): main's unquoted
# recipe let the shell consume those inner quotes (`box:Custom/Path/`);
# this branch, before this fix, handed them to rclone literally
# (`box:'Custom/Path/'`), a different -- and likely wrong or nonexistent --
# path. `ingest` would read nothing there and `publish-raw` could publish
# raw citizen data into an unintended apostrophe-named Box folder, silently.
# The Makefile now rejects the old form with a loud $(error) instead of
# reinterpreting it. test_incoming_remote_value_containing_a_literal_quote_
# reaches_rclone_intact above is the control proving a *genuine* embedded
# apostrophe (not the legacy wrapping shape) still flows through untouched.


def test_incoming_remote_with_legacy_hand_quoting_is_rejected_loudly(isolated_dir):
    """Exact repro from the review: `INCOMING_REMOTE=box:'Custom/Path/'` in
    .env, the precise value README.md used to recommend. Must fail loudly
    at parse time -- before ever reaching rclone -- rather than silently
    reading from (`ingest`) or publishing to (`publish-raw`) an
    apostrophe-named Box path nobody intended."""
    (isolated_dir / ".env").write_text("INCOMING_REMOTE=box:'Custom/Path/'\n")
    result = _make("-n", "ingest", cwd=isolated_dir)
    assert result.returncode != 0
    assert "INCOMING_REMOTE" in result.stderr
    assert "box:'Custom/Path/'" in result.stderr


def test_exhibits_remote_with_legacy_hand_quoting_is_rejected_loudly(isolated_dir):
    """Same repro, for EXHIBITS_REMOTE/`deliver` -- the review asked this be
    applied to both endpoints, not just INCOMING_REMOTE."""
    (isolated_dir / ".env").write_text("EXHIBITS_REMOTE=box:'Other/Path/'\n")
    result = _make("-n", "deliver", cwd=isolated_dir)
    assert result.returncode != 0
    assert "EXHIBITS_REMOTE" in result.stderr
    assert "box:'Other/Path/'" in result.stderr


def test_command_line_incoming_remote_with_legacy_hand_quoting_is_also_rejected(
    isolated_dir,
):
    """The legacy shape is rejected regardless of which precedence tier it
    arrives through -- a command-line override written the old way is the
    same trap as a .env one."""
    result = _make(
        "-n", "INCOMING_REMOTE=box:'Custom/Path/'", "ingest", cwd=isolated_dir
    )
    assert result.returncode != 0
    assert "INCOMING_REMOTE" in result.stderr


def test_incoming_remote_ending_in_a_genuine_apostrophe_is_not_mistaken_for_legacy_quoting(
    isolated_dir,
):
    """Negative control the review called out explicitly: a real Box folder
    ending in an apostrophe (not wrapped in one) must not be rejected --
    only the exact `remote:'...'` wrapping shape is. Distinct from
    test_incoming_remote_value_containing_a_literal_quote_reaches_rclone_
    intact above (that one's folder ends in `/`, after the apostrophe;
    this one's folder ends in the apostrophe itself, closer to the
    legacy-quoting shape's own last character)."""
    (isolated_dir / ".env").write_text(
        "INCOMING_REMOTE=box:My Grandmother's\n"
    )
    argv = _rclone_argv(isolated_dir, "ingest")
    assert argv == [
        "copy",
        "box:My Grandmother's",
        "data/raw/",
        "--progress",
    ], argv


def test_command_line_box_remote_containing_dollar_survives_to_rclone(isolated_dir):
    """Regression guard for the $(origin ...)/$(value ...) treatment #118
    also gives BOX_REMOTE (mirroring OLTP_DB_URL_RAW, per the comment above
    BOX_REMOTE_RAW's definition in the Makefile): a command-line BOX_REMOTE
    is stored recursively expanded, so referencing it naively from inside
    sh_quote's $(subst ...) -- itself another expansion -- would re-scan a
    literal `$` in the value for a (likely undefined, silently-emptied) Make
    variable reference, the same bug class as #60/#103, one layer down.
    BOX_REMOTE_RAW's $(value BOX_REMOTE) protects against exactly this."""
    argv = _rclone_argv(isolated_dir, "BOX_REMOTE=a$b", "ingest")
    assert argv == [
        "copy",
        "a$b:2. Projects/21. Governance//Data/Raw/",
        "data/raw/",
        "--progress",
    ], argv


def test_command_line_raw_local_containing_dollar_survives_to_rclone(isolated_dir):
    """The same #60/#103/#118 bug class, found one key further out while
    fixing box-paths for Codex's finding 3 on #138: RAW_LOCAL/EXHIBITS_LOCAL
    never got the _RAW/$(origin ...) treatment (the Makefile's own comment
    above RAW_LOCAL_RAW explains why they were originally exempted, and why
    that turned out to be wrong). Verified directly against the real
    (pre-fix) recipe: `make RAW_LOCAL='data$raw/' ingest` silently
    truncated the rclone argument to `dataaw/` even though it already went
    through sh_quote -- the bare `$(RAW_LOCAL)` reference inside sh_quote's
    own $(subst ...) re-scanned the command-line-origin value for `$`."""
    argv = _rclone_argv(isolated_dir, "RAW_LOCAL=data$raw/", "ingest")
    assert argv == [
        "copy",
        "box:2. Projects/21. Governance//Data/Raw/",
        "data$raw/",
        "--progress",
    ], argv


def test_command_line_exhibits_local_containing_dollar_survives_to_rclone(isolated_dir):
    """Same regression, for EXHIBITS_LOCAL/`deliver`."""
    argv = _rclone_argv(isolated_dir, "EXHIBITS_LOCAL=out$puts/", "deliver")
    assert argv == [
        "copy",
        "out$puts/",
        "box:2. Projects/21. Governance//Analysis/Exhibits/",
        "--progress",
    ], argv


def test_dry_run_ingest_shows_quoted_endpoint_for_default_config(isolated_dir):
    """Lighter-weight companion to the real-run tests above, using `-n` the
    way the rest of this file does: proves the printed recipe text itself is
    now single-quoted at the call site (sh_quote), not just that the
    underlying value is correct."""
    result = _make("-n", "ingest", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert (
        "rclone copy 'box:2. Projects/21. Governance//Data/Raw/' 'data/raw/' --progress"
        in result.stdout
    ), result.stdout


def test_dry_run_deliver_shows_quoted_endpoint_for_default_config(isolated_dir):
    result = _make("-n", "deliver", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert (
        "rclone copy 'outputs/' 'box:2. Projects/21. Governance//Analysis/Exhibits/' --progress"
        in result.stdout
    ), result.stdout


def test_box_paths_still_reports_incoming_remote_without_stray_quote_characters(
    isolated_dir,
):
    """box-paths now echoes INCOMING_REMOTE_RAW/EXHIBITS_REMOTE_RAW (#118),
    which -- unlike the pre-#118 default -- no longer has a literal `'`
    baked into it: the quoting moved to the recipe boundary, so box-paths'
    diagnostic display of the default is now the bare, unquoted path."""
    result = _make("box-paths", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert "INCOMING_REMOTE=box:2. Projects/21. Governance//Data/Raw/" in result.stdout
    assert "'" not in [
        line for line in result.stdout.splitlines() if line.startswith("INCOMING_REMOTE=")
    ][0]


# --- Codex on #138, finding 3: box-paths interpolated the _RAW values
# straight into a double-quoted `echo "KEY=$(...)"` shell string, which put
# them in a position the shell itself re-scans for `$` -- so a value
# containing a literal `$` lost everything from that `$` onward in
# box-paths' output specifically, even though the same value survives
# intact into the real ingest/publish-raw/deliver recipes (which already
# went through sh_quote via #118). The diagnostic disagreed with what the
# recipes actually do -- exactly the drift the comment above box-paths
# claims it prevents. On the GNU Make 3.81 this repo targets, .SHELLFLAGS
# is not honored (it postdates 3.81), so the failure is silent truncation,
# not the "unbound variable" abort a `set -u` shell would otherwise give --
# confirmed directly (see the Makefile's comment above box-paths) before
# fixing it, per the review's own instruction not to trust the predicted
# mechanism uncritically.


def test_box_paths_does_not_truncate_a_value_containing_a_dollar_sign(isolated_dir):
    """Exact repro from the review: `make BOX_REMOTE='a$b' box-paths` used
    to print `BOX_REMOTE=a` (silently dropping `$b`), while `make
    BOX_REMOTE='a$b' ingest` correctly preserved `a$b` in the rclone
    argument -- the same value, two different answers depending on which
    target asked."""
    result = _make("BOX_REMOTE=a$b", "box-paths", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert "BOX_REMOTE=a$b" in result.stdout, result.stdout
    # The derived endpoints inherit BOX_REMOTE and must not be truncated either.
    assert "INCOMING_REMOTE=a$b:2. Projects/21. Governance//Data/Raw/" in result.stdout
    assert "EXHIBITS_REMOTE=a$b:2. Projects/21. Governance//Analysis/Exhibits/" in result.stdout


def test_box_paths_does_not_truncate_dotenv_box_project_root_containing_a_dollar_sign(
    isolated_dir,
):
    """Same bug, reached through the .env tier instead of the command line
    -- BOX_PROJECT_ROOT is exactly as .env-populated as BOX_REMOTE (#104)."""
    (isolated_dir / ".env").write_text("BOX_PROJECT_ROOT=Archive$2026\n")
    result = _make("box-paths", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert "BOX_PROJECT_ROOT=Archive$2026" in result.stdout, result.stdout


def test_box_paths_does_not_truncate_raw_local_or_exhibits_local_containing_a_dollar_sign(
    isolated_dir,
):
    """RAW_LOCAL/EXHIBITS_LOCAL are plain local-directory `?=` defaults, not
    part of the .env-extraction machinery, but box-paths' echo of them went
    through the same vulnerable double-quoted interpolation and must be
    fixed the same way (the review asked for RAW_LOCAL/EXHIBITS_LOCAL
    coverage explicitly)."""
    result = _make(
        "RAW_LOCAL=data$raw/", "EXHIBITS_LOCAL=out$puts/", "box-paths",
        cwd=isolated_dir,
    )
    assert result.returncode == 0, result.stderr
    assert "RAW_LOCAL=data$raw/" in result.stdout, result.stdout
    assert "EXHIBITS_LOCAL=out$puts/" in result.stdout, result.stdout
