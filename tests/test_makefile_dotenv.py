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
    must be settable independently of BOX_REMOTE/BOX_PROJECT_ROOT."""
    (isolated_dir / ".env").write_text(
        "INCOMING_REMOTE=customremote:'Custom/Path/'\n"
        "EXHIBITS_REMOTE=customremote:'Other/Path/'\n"
    )
    result = _make("box-paths", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert "INCOMING_REMOTE=customremote:'Custom/Path/'" in result.stdout
    assert "EXHIBITS_REMOTE=customremote:'Other/Path/'" in result.stdout
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
    extraction: BOX_REMOTE, INCOMING_REMOTE, EXHIBITS_REMOTE."""
    (isolated_dir / ".env").write_text(
        "BOX_REMOTE=my-DOTENV_OK:-remote\n"
        "INCOMING_REMOTE=customremote:'DOTENV_OK:/Incoming/'\n"
        "EXHIBITS_REMOTE=customremote:'DOTENV_OK:/Exhibits/'\n"
    )
    result = _make("box-paths", cwd=isolated_dir)
    assert result.returncode == 0, result.stderr
    assert "BOX_REMOTE=my-DOTENV_OK:-remote" in result.stdout, result.stdout
    assert "INCOMING_REMOTE=customremote:'DOTENV_OK:/Incoming/'" in result.stdout
    assert "EXHIBITS_REMOTE=customremote:'DOTENV_OK:/Exhibits/'" in result.stdout


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
