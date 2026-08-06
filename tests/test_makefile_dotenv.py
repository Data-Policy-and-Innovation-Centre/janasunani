"""Real-code-path checks that OLTP_DB_URL survives adversarial characters
through every documented precedence tier of the Makefile (#60 and its
sequels): command line, shell-exported environment, and `.env`.

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
