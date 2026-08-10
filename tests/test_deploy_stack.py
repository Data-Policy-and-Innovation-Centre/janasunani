"""Real-code-path checks on the deploy artifacts themselves (not mocks of
them): parses the actual `deploy/docker-compose.yml` and
`deploy/proxy/Caddyfile` on disk, and shells out to `sh -n` / `bash -n` on the
actual scripts — the same files CI ships and the box runs.

Guards the CI -> GHCR -> box deploy stack (see docs/DEPLOY.md "Automated demo
deploy") against the specific footguns called out in deploy/README.md and
docs/DEPLOY.md: the `oltp` service/volume must stay byte-identical to the
Week-1 bring-up (external volume, localhost-only port), the app services must
never be Internet-exposed except through `proxy`, model/data mounts must stay
read-only, and image tags must always be pinned (never `latest`) so a deploy
is reproducible and `deploy.sh` never `docker compose down -v`s the
production OLTP volume.
"""

import os
import re
import shutil
import stat
import subprocess

import pytest
import yaml
from packaging.markers import Marker

from janasunani.config import ROOT_DIR

DEPLOY_DIR = ROOT_DIR / "deploy"
COMPOSE_PATH = DEPLOY_DIR / "docker-compose.yml"
CADDYFILE_PATH = DEPLOY_DIR / "proxy" / "Caddyfile"
DEPLOY_SH_PATH = DEPLOY_DIR / "deploy.sh"
ENTRYPOINT_PATH = DEPLOY_DIR / "api-entrypoint.sh"
DEPLOY_WORKFLOW_PATH = ROOT_DIR / ".github" / "workflows" / "deploy.yml"
DOCKERIGNORE_PATH = ROOT_DIR / ".dockerignore"
FRONTEND_DOCKERFILE_PATH = ROOT_DIR / "frontend" / "Dockerfile"
API_DOCKERFILE_PATH = DEPLOY_DIR / "api.Dockerfile"
DEPLOY_DOC_PATH = ROOT_DIR / "docs" / "DEPLOY.md"
LIVE_LAUNCH_PATHS = (
    ROOT_DIR / "README.md",
    ROOT_DIR / "pyproject.toml",
    ROOT_DIR / "Makefile",
    ROOT_DIR / "docs" / "DEMO.md",
    ROOT_DIR / "frontend" / "README.md",
    ROOT_DIR / "frontend" / ".env.local.example",
    ROOT_DIR / "janasunani" / "inference" / "README.md",
    ROOT_DIR / "janasunani" / "serving" / "README.md",
)


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text())


def test_oltp_service_and_volume_are_untouched():
    """The oltp service/volume are the Week-1 bring-up's production data —
    this stack must adopt, never recreate, them."""
    compose = _compose()

    oltp = compose["services"]["oltp"]
    assert oltp["container_name"] == "janasunani-oltp"
    assert oltp["ports"] == ["127.0.0.1:5432:5432"]

    oltp_volume = compose["volumes"]["oltp-data"]
    assert oltp_volume["external"] is True
    assert oltp_volume["name"] == "janasunani-oltp"


def test_api_and_frontend_publish_no_ports():
    """api/frontend must only be reachable through the proxy — no direct
    public exposure."""
    compose = _compose()

    assert "ports" not in compose["services"]["api"]
    assert "ports" not in compose["services"]["frontend"]


def test_proxy_publishes_only_http_and_https():
    compose = _compose()

    ports = compose["services"]["proxy"]["ports"]
    assert set(ports) == {"80:80", "443:443"}


def test_api_oltp_db_url_targets_the_compose_network_host():
    """The api service must reach Postgres over the compose network (service
    name `oltp`), not localhost/an external host."""
    compose = _compose()

    url = compose["services"]["api"]["environment"]["OLTP_DB_URL"]
    assert "@oltp:5432/" in url
    assert url.startswith("postgresql+asyncpg://")


def test_api_data_mounts_are_read_only():
    """Models and the DVC-tracked lake/mappings are host bind-mounts, ro —
    never baked into the image, never writable by the container."""
    compose = _compose()

    volumes = compose["services"]["api"]["volumes"]
    ro_mounts = {v for v in volumes if v.endswith(":ro")}
    expected = {
        "../models:/app/models:ro",
        "../data/interim:/app/data/interim:ro",
        "../data/raw/janasunani-mappings:/app/data/raw/janasunani-mappings:ro",
    }
    assert expected <= ro_mounts


def test_app_images_are_pinned_to_image_tag_not_latest():
    """A deploy must always be reproducible/rollback-able — no `latest`."""
    compose = _compose()

    for service in ("api", "frontend"):
        image = compose["services"][service]["image"]
        assert "IMAGE_TAG" in image
        assert ":latest" not in image


def test_compose_does_not_use_required_var_syntax_for_app_vars():
    """compose interpolates the WHOLE file before selecting services, so a
    `:?` (required-variable) on IMAGE_TAG or DEMO_PASSWORD_HASH would abort
    even an oltp-only `docker compose up -d oltp` (Codex PR #29 finding) --
    the documented first-time bring-up that only ever sets
    POSTGRES_PASSWORD. deploy.sh enforces both being set to a real value
    instead; compose itself must stay permissive so that command still
    works. (oltp's own POSTGRES_PASSWORD is exempt -- it's exactly the var
    an oltp-only command does set, and requiring it there is the point.)"""
    compose = _compose()

    api_image = compose["services"]["api"]["image"]
    frontend_image = compose["services"]["frontend"]["image"]
    assert ":?" not in api_image, f"api image must not use ':?': {api_image!r}"
    assert ":?" not in frontend_image, (
        f"frontend image must not use ':?': {frontend_image!r}"
    )

    proxy_env = compose["services"]["proxy"].get("environment") or {}
    for key, value in proxy_env.items():
        assert ":?" not in str(value), (
            f"proxy environment {key} must not use ':?': {value!r}"
        )


def test_frontend_has_a_healthcheck():
    """The health-gate must catch a dead/mispackaged frontend too, not just
    a dead api (Codex PR #29 finding) -- deploy.sh waits on this."""
    compose = _compose()

    frontend = compose["services"]["frontend"]
    assert "healthcheck" in frontend
    assert frontend["healthcheck"]["test"]


def test_proxy_credentials_come_from_a_dedicated_env_file_not_interpolated():
    """Compose interpolates `$` in `environment:`/the main `.env` file, which
    would mangle a bcrypt hash like `$2a$14$...` (Codex PR #29 finding).
    DEMO_USER/DEMO_PASSWORD_HASH must come from a separate `env_file` (no
    compose interpolation applied to its contents), not `environment:` and
    not the main deploy/.env. Live-verified: a real `$2a$14$...` hash placed
    in the env_file reaches Caddy byte-for-byte intact and authenticates the
    matching plaintext password.

    Must be the LONG form with `format: raw` and `required: false` (round-3
    finding): a bare `- ./proxy.env` is required-by-default, so Compose
    fails to load the whole project -- including an oltp-only
    `docker compose up -d oltp` -- when proxy.env doesn't exist yet; and
    some Compose versions interpolate `$` in env_file values by default
    unless `format: raw` says otherwise (this repo's dev-machine Compose
    happens not to, which is exactly why this needs an explicit assertion,
    not just a "seems to work locally" check)."""
    compose = _compose()

    proxy = compose["services"]["proxy"]
    proxy_env = proxy.get("environment") or {}
    assert "DEMO_PASSWORD_HASH" not in proxy_env, (
        "DEMO_PASSWORD_HASH must not be a compose `environment:` entry -- "
        "compose interpolates '$' in these values and would mangle a bcrypt "
        "hash; use env_file instead"
    )
    assert "DEMO_USER" not in proxy_env

    env_files = proxy.get("env_file")
    assert env_files, "proxy service must load DEMO_USER/DEMO_PASSWORD_HASH via env_file"
    assert isinstance(env_files, list) and isinstance(env_files[0], dict), (
        "proxy's env_file must use the long form (a list of path/required/"
        f"format mappings), not a bare list of path strings: {env_files!r}"
    )
    proxy_env_entry = next(e for e in env_files if "proxy.env" in e.get("path", ""))
    assert proxy_env_entry.get("required") is False, (
        "proxy.env's env_file entry must set required: false so an "
        "oltp-only `docker compose up -d oltp` still works before "
        "proxy.env exists"
    )
    assert proxy_env_entry.get("format") == "raw", (
        "proxy.env's env_file entry must set format: raw so '$' in the "
        "bcrypt hash is never interpolated, regardless of Compose version"
    )

    # The main deploy/.env.example must not carry the hash either.
    env_example = (DEPLOY_DIR / ".env.example").read_text()
    assert "DEMO_PASSWORD_HASH=" not in env_example

    # deploy/proxy.env (the real, filled-in file) must never be committed;
    # only the .example template is tracked.
    assert (DEPLOY_DIR / "proxy.env.example").exists()
    with open(ROOT_DIR / ".gitignore") as f:
        gitignore = f.read()
    assert "deploy/proxy.env" in gitignore


def test_caddyfile_routes_api_and_frontend():
    text = CADDYFILE_PATH.read_text()

    assert "handle_path /api/*" in text
    assert "reverse_proxy api:8000" in text
    assert "reverse_proxy frontend:3000" in text
    # basic_auth in front of the whole site (production grievance data must
    # not be openly public) — Decision 8.
    assert "basic_auth" in text


def test_caddyfile_exempts_health_from_basic_auth():
    """deploy.sh's own end-to-end check curls /api/health unauthenticated —
    without an exemption, basic_auth would 401 every single deploy's health
    check (Codex PR #29 finding). Verified live against a real caddy:2-alpine
    container: unauthenticated /api/health -> 200 (routed to the api's
    /health, prefix stripped); unauthenticated / and /api/other -> 401."""
    text = CADDYFILE_PATH.read_text()

    assert "not path /api/health" in text
    # The matcher must actually be attached to the basic_auth directive
    # (not just declared and unused).
    matcher_name = text.split("not path /api/health")[0].splitlines()[-1].split()[0]
    assert matcher_name.startswith("@")
    assert f"basic_auth {matcher_name}" in text


def test_proxy_password_hash_has_no_published_default():
    """A default bcrypt hash baked into a file that's in git is a published,
    already-compromised credential — anyone who can read this repo could
    authenticate to production /history and /api (Codex PR #29 finding).
    No compose file or example env file may ship a real-looking (full-shape)
    bcrypt hash; deploy/proxy.env.example must ship it empty, and deploy.sh
    (not compose) fails closed on it being unset. Matches on the full bcrypt
    shape ($2a$<cost>$<53 more chars>), not just the "$2a$" prefix, so an
    explanatory code comment illustrating the shape (e.g. "$2a$14$...") isn't
    a false positive."""
    bcrypt_shape = re.compile(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}")

    compose_text = COMPOSE_PATH.read_text()
    assert not bcrypt_shape.search(compose_text), (
        "docker-compose.yml must not embed a real/published bcrypt hash "
        "anywhere"
    )

    proxy_env_example = (DEPLOY_DIR / "proxy.env.example").read_text()
    assert not bcrypt_shape.search(proxy_env_example)
    for line in proxy_env_example.splitlines():
        if line.startswith("DEMO_PASSWORD_HASH="):
            assert line == "DEMO_PASSWORD_HASH=", (
                f"deploy/proxy.env.example must not ship a real hash: {line!r}"
            )

    # The main deploy/.env.example must not carry it either (see the
    # env_file test above for *why* it moved).
    env_example = (DEPLOY_DIR / ".env.example").read_text()
    assert not bcrypt_shape.search(env_example)


def test_entrypoint_and_deploy_script_are_valid_shell():
    for path, shell in ((ENTRYPOINT_PATH, "sh"), (DEPLOY_SH_PATH, "bash")):
        result = subprocess.run(
            [shell, "-n", str(path)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr


def test_deploy_script_never_tears_down_the_volume():
    """`docker compose down -v` would delete the production `janasunani-oltp`
    volume — deploy.sh must never invoke it (see deploy/README.md)."""
    text = DEPLOY_SH_PATH.read_text()
    executable_lines = [
        line for line in text.splitlines() if not line.strip().startswith("#")
    ]
    for line in executable_lines:
        assert not ("down" in line and "-v" in line), (
            f"deploy.sh must never `docker compose down -v`: {line!r}"
        )


def test_deploy_script_reloads_caddy_after_shipping_a_new_caddyfile():
    """The Caddyfile is bind-mounted, so `docker compose up -d` alone leaves
    an already-running proxy container's config untouched -- a shipped auth/
    routing/TLS change would be silently ignored while the deploy still
    reports success (Codex PR #29 finding). deploy.sh must issue a graceful
    reload, guarded so a brand-new (not-yet-running) proxy container --
    which already loads the current file at startup -- doesn't error.
    Live-verified against a real caddy:2-alpine container: `caddy reload
    --config /etc/caddy/Caddyfile --adapter caddyfile` applies a changed
    file with no dropped connections, and is a safe no-op when unchanged."""
    text = DEPLOY_SH_PATH.read_text()

    assert "caddy reload" in text
    assert "--config /etc/caddy/Caddyfile" in text
    assert "--adapter caddyfile" in text
    # Guarded, not unconditional -- must reference whether the proxy
    # container was already running before this invocation of `up -d`.
    assert re.search(r"proxy_was_running", text)


def test_deploy_script_waits_on_both_api_and_frontend_health():
    """A dead/mispackaged frontend must fail the deploy, not just a dead api
    (Codex PR #29 finding)."""
    text = DEPLOY_SH_PATH.read_text()

    assert "janasunani-api" in text
    assert "janasunani-frontend" in text


def test_deploy_script_fails_closed_on_the_demo_password_hash():
    """deploy.sh (not compose -- see the ':?' test above) is where "no real
    password hash configured" must stop a full-stack deploy instead of
    silently exposing production /history and /api behind a broken or
    empty auth (Codex PR #29 finding)."""
    text = DEPLOY_SH_PATH.read_text()

    assert "proxy.env" in text
    assert "DEMO_PASSWORD_HASH" in text
    # Must actually validate the value looks like a bcrypt hash (prefix
    # $2a$/$2b$/$2y$), not just check that the file/variable exists.
    assert "2[aby]" in text, (
        "deploy.sh must validate DEMO_PASSWORD_HASH looks like a real "
        f"bcrypt hash, not just that it's non-empty: no bcrypt-prefix "
        f"check found in {DEPLOY_SH_PATH}"
    )
    assert "exit 1" in text


def test_deploy_script_env_value_reads_survive_a_missing_key():
    """`site="$(grep ... .env | cut -d= -f2-)"` and the equivalent
    DEMO_PASSWORD_HASH read: under `set -euo pipefail`, if the key is
    absent grep exits 1 and the WHOLE pipeline (feeding a command
    substitution assignment) aborts the script right there, before the
    `-z`/empty-value fallback ever runs -- and for SITE_ADDRESS this
    happens AFTER `docker compose up -d`, so an already-changed stack gets
    reported as a hard crash instead of falling through to the documented
    ':80'/http default (round-5 Codex PR #29 finding). Each such read must
    end in `|| true` so a missing key yields an empty string instead of
    aborting. Live-verified under real `set -euo pipefail`: the bare form
    aborts the script silently on a missing key; the `|| true` form
    reaches the fallback logic with an empty value, exit 0."""
    text = DEPLOY_SH_PATH.read_text()

    for var_name, key in (("site", "SITE_ADDRESS"), ("demo_hash", "DEMO_PASSWORD_HASH")):
        pattern = re.compile(
            re.escape(var_name)
            + r'="\$\(grep -E \'\^'
            + re.escape(key)
            + r"=' [^|]+\| cut -d= -f2- ?(\|\| true)?\)\""
        )
        match = pattern.search(text)
        assert match, f"could not find the {key} read in {DEPLOY_SH_PATH}"
        assert match.group(1) == "|| true", (
            f"the {key} read (assigned to ${var_name}) must end in "
            f"'|| true' so a missing key doesn't abort the script under "
            f"set -o pipefail: {match.group(0)!r}"
        )


def test_deploy_script_rolls_back_on_health_or_smoke_check_failure():
    """`docker compose up -d` immediately replaces whatever was previously
    running with the new candidate -- if a health/smoke check fails AFTER
    that, the public demo is left down on the broken candidate even though
    .env still names the last known-good tag (round-5 Codex PR #29
    finding). deploy.sh must capture the prior IMAGE_TAG before touching
    anything and, on a post-up-d failure, re-`up -d` with it. Live-verified
    end-to-end with two locally-tagged stand-in images (one serving a valid
    /health body, one not): after a successful "good-v1" deploy, deploying
    "bad-v2" times out its health check, and the script (a) prints a
    rollback message, (b) re-runs `docker compose up -d` with
    IMAGE_TAG=good-v1 -- confirmed via `docker inspect
    janasunani-api --format {{.Config.Image}}` actually showing
    local/stand-in-api:good-v1 again post-rollback, and /api/health
    responding 200 again, (c) leaves .env's IMAGE_TAG at good-v1
    throughout, (d) exits 1.

    Explicit call sites (`rollback_and_fail`), not a blanket `trap ... ERR`:
    a trap would also fire on the preflight/hash-check exits earlier in the
    script, which happen before `docker compose up -d` and have nothing to
    roll back from."""
    text = DEPLOY_SH_PATH.read_text()

    assert "prev_tag" in text, "must capture the prior IMAGE_TAG from .env"
    assert "rollback_and_fail" in text

    # Must actually re-deploy the prior tag, not just log about it.
    assert re.search(r'IMAGE_TAG="\$prev_tag"\s+docker compose up -d', text), (
        "rollback_and_fail must re-run `docker compose up -d` with "
        "IMAGE_TAG set back to the captured prev_tag"
    )

    # Both failure sites this finding calls out must route through it.
    assert re.search(r"rollback_and_fail\s*\n", text), (
        "wait_healthy's timeout branch must call rollback_and_fail (not a "
        "bare exit 1)"
    )
    # The smoke check became a retry loop in round 6 (covers first-deploy
    # TLS cert issuance latency) -- it calls rollback_and_fail once the
    # retry deadline is exceeded, not inline on every failed grep.
    assert "smoke_deadline" in text and "rollback_and_fail" in text[text.index("smoke_deadline"):]

    # Not a blanket ERR trap -- see the docstring for why. (Checks for an
    # actual `trap ...` command, not just the word "trap" -- which shows up
    # in this file's own explanatory comment about *not* using one.)
    executable_lines = [
        line for line in text.splitlines() if not line.strip().startswith("#")
    ]
    assert not any(re.match(r"\s*trap\b", line) for line in executable_lines), (
        "deploy.sh should use explicit rollback_and_fail() call sites, not "
        "a trap ... ERR handler (see the docstring for why)"
    )


def test_deploy_script_persists_image_tag_only_after_success():
    """deploy.sh must write the IMAGE_TAG= line into .env AFTER pull/up/
    reload/both health waits/the final smoke check all pass -- not before
    (round-4 Codex PR #29 finding). Writing it first meant a failed pull
    (unpublished/rolled-back SHA, transient GHCR error) still left .env
    pointing at a tag that never actually deployed, so a later bare
    `docker compose up -d` would use the bad tag instead of the last
    known-good one. Live-verified: a deploy with a nonexistent image tag
    fails `docker compose pull` and leaves .env's IMAGE_TAG completely
    unchanged; a deploy that gets through both health waits but fails the
    final /api/health smoke check ALSO leaves .env unchanged; only a fully
    successful run (pull -> up -> reload -> both healthy -> smoke check
    passes) updates it."""
    text = DEPLOY_SH_PATH.read_text()
    lines = text.splitlines()

    def first_line_containing(needle: str) -> int:
        for i, line in enumerate(lines):
            if needle in line:
                return i
        raise AssertionError(f"{needle!r} not found in {DEPLOY_SH_PATH}")

    def first_line_equal_to(needle: str) -> int:
        # Exact (stripped) match -- distinguishes the real top-level
        # invocation from the same substring inside rollback_and_fail's
        # body, which is DEFINED earlier in the file (functions are
        # declared before their call sites) but not the line under test
        # here -- rollback_and_fail's re-`up -d` reads
        # `IMAGE_TAG="$prev_tag" docker compose up -d` (different text) and
        # its health re-check reads `wait_healthy janasunani-api &&
        # wait_healthy janasunani-frontend` (joined with `&&`, not `||
        # rollback_and_fail`), so exact-matching the real call sites' full
        # text (with their `|| rollback_and_fail` suffix) avoids both.
        for i, line in enumerate(lines):
            if line.strip() == needle:
                return i
        raise AssertionError(f"no line exactly matching {needle!r} in {DEPLOY_SH_PATH}")

    write_line = first_line_containing("IMAGE_TAG=${IMAGE_TAG}")
    pull_line = first_line_containing("docker compose pull")
    up_line = first_line_equal_to("docker compose up -d || rollback_and_fail")
    wait_api_line = first_line_equal_to("wait_healthy janasunani-api || rollback_and_fail")
    wait_frontend_line = first_line_equal_to("wait_healthy janasunani-frontend || rollback_and_fail")
    # The final end-to-end check greps the health body through the proxy.
    smoke_check_line = first_line_containing('"processor":"pipeline"')

    for label, line in (
        ("docker compose pull", pull_line),
        ("docker compose up -d", up_line),
        ("wait_healthy janasunani-api", wait_api_line),
        ("wait_healthy janasunani-frontend", wait_frontend_line),
        ("the final smoke check", smoke_check_line),
    ):
        assert write_line > line, (
            f"deploy.sh writes IMAGE_TAG into .env at line {write_line + 1}, "
            f"but {label} is at line {line + 1} -- the .env write must come "
            f"AFTER every one of these, not before"
        )


def test_deploy_script_preflights_compose_version():
    """`format: raw` on env_file (docker-compose.yml's proxy service) needs
    Compose >= 2.30.0 -- on an older Compose the whole file fails to parse
    before any service starts, with an opaque error. deploy.sh must check
    `docker compose version` and fail with a clear message before running
    any compose command (round-4 Codex PR #29 finding)."""
    text = DEPLOY_SH_PATH.read_text()
    lines = text.splitlines()

    version_check_line = next(
        i for i, line in enumerate(lines) if "docker compose version" in line
    )
    pull_line = next(i for i, line in enumerate(lines) if "docker compose pull" in line)
    assert version_check_line < pull_line, (
        "deploy.sh must check the Compose version BEFORE the first real "
        "compose command (docker compose pull), not after"
    )
    assert "2.30" in text, (
        "deploy.sh's Compose version preflight must reference 2.30.0 "
        "specifically (the version format: raw needs), not 2.24"
    )
    assert "2.24" not in text

    # docker-compose.yml's explanatory comment and the runbook must state
    # 2.30 as the actual minimum (either may still mention 2.24 in passing,
    # contrasting it with 2.30 -- required: false alone landed in 2.24, but
    # is useless here without format: raw, which needs 2.30 -- that's
    # legitimate context, not the bug; the bug was stating 2.24 AS the
    # minimum, which deploy.sh's own preflight -- checked strictly above --
    # never does).
    compose_text = COMPOSE_PATH.read_text()
    assert "2.30" in compose_text

    deploy_doc = (ROOT_DIR / "docs" / "DEPLOY.md").read_text()
    assert "2.30" in deploy_doc


def test_deploy_job_is_scoped_to_the_box_deploy_environment():
    """Only the `deploy` job needs (and should be able) to read the
    box-shell-granting secrets/vars (BOX_SSH_KEY, BOX_SSH_KNOWN_HOSTS,
    BOX_HOST, CI_DEPLOY_ROLE_ARN, BOX_SG_ID) -- docs/DEPLOY.md directs the
    maintainer to scope those to the `box-deploy` GitHub Actions environment
    rather than the repo level (round-4 Codex PR #29 finding: a repo-level
    secret is readable by any workflow any repo writer can add, bypassing
    both the OIDC narrowing and the environment's required-reviewer gate).
    That guidance only holds if `deploy` actually declares the environment
    and the build jobs -- which never need box access -- don't."""
    with open(DEPLOY_WORKFLOW_PATH) as f:
        workflow = yaml.safe_load(f)

    assert workflow["jobs"]["deploy"].get("environment") == "box-deploy"
    assert "environment" not in workflow["jobs"]["build-api"]
    assert "environment" not in workflow["jobs"]["build-frontend"]


def test_deploy_job_runs_on_rollback_but_not_on_a_real_build_failure():
    """`deploy` `needs: [build-api, build-frontend]`. On a rollback run
    (image_tag set), both builds are SKIPPED by their own `if:` -- and
    GitHub auto-skips a job whose needs were all skipped UNLESS the `if`
    contains `always()` (round-5 Codex PR #29 finding: without it, every
    rollback would silently no-op instead of deploying). `always()` alone
    would be wrong too -- it would deploy even after a real build failure.
    The combination `always() && !failure() && !cancelled()` is the fix:
    always evaluate the condition past skipped needs, but still bail out on
    an actual failure or a cancelled run."""
    with open(DEPLOY_WORKFLOW_PATH) as f:
        workflow = yaml.safe_load(f)

    deploy_if = workflow["jobs"]["deploy"]["if"]
    # PyYAML strips the ${{ }} wrapper's outer braces are template syntax,
    # not YAML -- the whole thing loads as a plain string.
    assert "always()" in deploy_if, (
        "deploy's `if` must include always() or GitHub auto-skips it "
        "whenever build-api/build-frontend were both skipped (a rollback "
        "run, image_tag set)"
    )
    assert "!failure()" in deploy_if
    assert "!cancelled()" in deploy_if


def test_deploy_workflow_rejects_short_shas():
    """build-api/build-frontend only ever publish the FULL github.sha (40
    hex chars) -- never a short SHA. A regex that accepts 7-39 char short
    SHAs lets a rollback request pass validation and then fail
    `docker compose pull` with a confusing image-not-found error (Codex
    PR #29 finding)."""
    text = DEPLOY_WORKFLOW_PATH.read_text()

    assert "{40}" in text, (
        "deploy.yml's image_tag validation must require exactly 40 hex "
        "chars (the full commit SHA), not a variable-length short SHA"
    )
    assert "{7,40}" not in text and "{7," not in text


def test_dockerignore_excludes_proxy_env_but_not_its_example():
    """The api build's context is the repo root (deploy/api.Dockerfile) --
    without an explicit exclusion, `deploy/proxy.env` (the real Basic Auth
    bcrypt hash) gets uploaded into the builder even though the Dockerfile
    never COPYs it (round-3 Codex PR #29 finding). Live-verified: built a
    throwaway image COPYing deploy/ with a fake deploy/proxy.env present --
    only .env.example/proxy.env.example ended up in the context, never the
    real files."""
    import fnmatch

    lines = [
        line.strip()
        for line in DOCKERIGNORE_PATH.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert "deploy/proxy.env" in lines, (
        ".dockerignore must exclude deploy/proxy.env explicitly"
    )
    # Whatever pattern excludes it must not ALSO catch the tracked .example
    # templates -- those have to stay reachable if a Dockerfile ever needs
    # to reference them (and to keep the pattern obviously non-wildcard-risky).
    for pattern in lines:
        for must_stay_reachable in ("deploy/proxy.env.example", "deploy/.env.example"):
            assert not fnmatch.fnmatch(must_stay_reachable, pattern), (
                f".dockerignore pattern {pattern!r} must not also match "
                f"{must_stay_reachable!r}"
            )


def test_deploy_workflow_has_no_moving_demo_tag():
    """build-api and build-frontend are independent jobs. If one pushed a
    moving `:demo` tag and the other then failed, a later `image_tag: demo`
    deploy would mix api/frontend from different commits (round-3 Codex PR
    #29 finding). Chosen fix: drop the moving tag entirely -- only the
    immutable per-commit SHA is ever pushed, and `demo` is no longer a valid
    image_tag input value either. (Confirmed via repo-wide grep before this
    fix: nothing in compose/.env.example/deploy.sh/docs actually depended on
    an image `:demo` tag existing.)"""
    text = DEPLOY_WORKFLOW_PATH.read_text()

    assert ":demo" not in text, (
        "deploy.yml must not push or reference a moving ':demo' image tag "
        "-- api/frontend builds are independent jobs and a partial push "
        "would let a rollback mix commits"
    )
    # The tag regex must not special-case "demo" as a valid input either.
    assert '"$tag" != demo' not in text and "!= demo" not in text


def test_deploy_script_has_a_flock_guard():
    """A hand-run deploy.sh can interleave with a CI-triggered one -- the
    workflow's `concurrency:` group only serializes CI runs against each
    other, not a manual run on the box (Fable review, round-6 finding).
    Must actually acquire the lock before doing anything else (checked
    before the first `docker compose` invocation), not just mention
    `flock` somewhere."""
    text = DEPLOY_SH_PATH.read_text()
    lines = text.splitlines()

    flock_line = next(i for i, line in enumerate(lines) if re.search(r"\bflock\b", line))
    pull_line = next(i for i, line in enumerate(lines) if line.strip() == "docker compose pull api frontend proxy")
    assert flock_line < pull_line, "the flock guard must run before the first docker compose command"


def test_deploy_script_has_a_disk_space_preflight():
    """This root volume also holds prod Postgres, models, the lake, the HF
    cache, and the nightly pg_dump target -- an ~8-12 GB api image pull that
    runs the disk out of room risks all of those, not just the deploy
    (Fable review, round-6 finding). Must check BEFORE `docker compose
    pull`, not after."""
    text = DEPLOY_SH_PATH.read_text()
    lines = text.splitlines()

    df_line = next(i for i, line in enumerate(lines) if "df -Pk" in line)
    pull_line = next(i for i, line in enumerate(lines) if line.strip() == "docker compose pull api frontend proxy")
    assert df_line < pull_line


def test_deploy_script_prunes_old_sha_tagged_images_on_success():
    """Each deploy leaves the prior ~8-12 GB SHA-tagged api image behind;
    only dangling images were pruned before. Must keep exactly current +
    previous (the rollback target), not prune unconditionally (that would
    delete the rollback target itself) (Fable review, round-6 finding)."""
    text = DEPLOY_SH_PATH.read_text()

    assert re.search(r"docker images .* --format", text)
    assert re.search(r'"\$tag" != "\$new_tag" && "\$tag" != "\$prev_tag"', text), (
        "retention pruning must keep both new_tag (current) and prev_tag "
        "(the rollback target), removing everything else"
    )


def test_deploy_script_smoke_check_retries():
    """First deploy of a fresh nip.io hostname needs a few seconds for
    Caddy to issue the Let's Encrypt cert -- a single-shot smoke check would
    spuriously fail (and, post-round-5, trigger a pointless rollback) during
    that window (Fable review, round-6 finding)."""
    text = DEPLOY_SH_PATH.read_text()

    assert "smoke_deadline" in text
    assert re.search(r"while true", text)


def test_health_log_dump_has_a_pii_guard_comment():
    """The `docker logs --tail 100` dump on a health-wait failure lands in
    repo-readable CI Actions logs -- must never carry a grievance payload
    (Fable review, round-6 finding)."""
    text = DEPLOY_SH_PATH.read_text()

    idx = text.index("docker logs --tail 100")
    preceding = text[:idx]
    assert "CI Actions logs" in preceding or "CI logs" in preceding
    assert "grievance payload" in preceding


def test_env_example_warns_about_dollar_and_hash_in_password():
    """Compose interpolates '$' and treats a bare '#' as a comment in .env
    values -- POSTGRES_PASSWORD containing either could silently break
    (Fable review, round-6 finding)."""
    text = (DEPLOY_DIR / ".env.example").read_text()

    idx = text.index("POSTGRES_PASSWORD=change-me")
    preceding = text[:idx]
    assert "'$'" in preceding or "$" in preceding
    assert "#" in preceding


def test_base_images_are_pinned_to_digests():
    """Every base image besides the app images themselves (which are
    already pinned to the full IMAGE_TAG) floats on a tag that's re-pulled
    on every build/deploy -- a base image change underneath an unchanged
    Dockerfile/compose file is exactly the drift IMAGE_TAG pinning exists to
    prevent (Fable review, round-6 finding)."""
    compose_text = COMPOSE_PATH.read_text()
    assert re.search(r"caddy:2-alpine@sha256:[0-9a-f]{64}", compose_text)

    api_dockerfile_text = API_DOCKERFILE_PATH.read_text()
    assert re.search(r"python:3\.13-slim@sha256:[0-9a-f]{64}", api_dockerfile_text)
    assert re.search(r"ghcr\.io/astral-sh/uv:0\.9@sha256:[0-9a-f]{64}", api_dockerfile_text)

    frontend_dockerfile_text = FRONTEND_DOCKERFILE_PATH.read_text()
    assert re.search(r"node:22-alpine@sha256:[0-9a-f]{64}", frontend_dockerfile_text)


def test_caddyfile_deployed_snapshot_is_gitignored_and_versioned():
    """`deploy/proxy/Caddyfile.deployed` is box-local runtime state (the
    rollback target), written at the end of a successful deploy -- must
    never be committed, and deploy.sh must both write it on success and
    read it in rollback_and_fail (Fable review, round-6 finding)."""
    gitignore_text = (ROOT_DIR / ".gitignore").read_text()
    assert "Caddyfile.deployed" in gitignore_text

    deploy_sh_text = DEPLOY_SH_PATH.read_text()
    assert "cp proxy/Caddyfile proxy/Caddyfile.deployed" in deploy_sh_text
    assert "proxy/Caddyfile.deployed" in deploy_sh_text
    # Restoration must happen inside rollback_and_fail, before the
    # tag-rollback decision (a same-tag redeploy shipping only a Caddyfile
    # change has no image to roll back but still needs the file restored).
    rollback_fn_start = deploy_sh_text.index("rollback_and_fail() {")
    tag_decision = deploy_sh_text.index('if [[ -z "$prev_tag"', rollback_fn_start)
    caddyfile_restore = deploy_sh_text.index("cp proxy/Caddyfile.deployed proxy/Caddyfile", rollback_fn_start)
    assert rollback_fn_start < caddyfile_restore < tag_decision


def test_deploy_doc_preflight_uses_the_host_visible_oltp_dsn():
    """The strict-preflight command in docs/DEPLOY.md's box bring-up runs on
    the host, not inside a container, so it needs the 127.0.0.1:5432 form
    the oltp service publishes to the host (its own ports comment says so)
    -- not the oltp:5432 compose-internal hostname the api container's own
    OLTP_DB_URL uses, which only the compose network resolves. Codex review
    on #88: following the doc as written pointed the new strict connectivity
    probe (#88, _oltp_check) at a hostname the host can't reach, failing
    against an otherwise-healthy database."""
    doc_text = DEPLOY_DOC_PATH.read_text()
    anchor = doc_text.index("Verify the box before the first deploy")
    fence_start = doc_text.index("```bash", anchor)
    fence_end = doc_text.index("```", fence_start + len("```bash"))
    command_block = doc_text[fence_start:fence_end]
    assert "127.0.0.1:5432" in command_block
    assert "oltp:5432" not in command_block

    api_dsn = _compose()["services"]["api"]["environment"]["OLTP_DB_URL"]
    assert "oltp:5432" in api_dsn, "sanity: the compose-internal DSN really is oltp:5432"


def test_migration_policy_is_documented():
    """A rollback re-deploys the previous image unchanged and never runs
    `alembic downgrade` -- every migration must be expand-only/backward-
    compatible or a rollback can't un-migrate (Fable review, round-6 H1
    finding). Must be documented in both api-entrypoint.sh (where the
    migration actually runs) and docs/DEPLOY.md (the runbook)."""
    entrypoint_text = ENTRYPOINT_PATH.read_text()
    assert "MIGRATION POLICY" in entrypoint_text
    assert "alembic downgrade" in entrypoint_text
    assert "expand-only" in entrypoint_text or "backward-compat" in entrypoint_text

    doc_text = DEPLOY_DOC_PATH.read_text()
    assert "Migration policy" in doc_text
    assert "expand-only" in doc_text or "backward-compat" in doc_text


def test_rollback_reverifies_health_before_claiming_success():
    """The core H1 regression: re-deploying the previous image does NOT
    guarantee it comes up healthy (e.g. a migration the old image can't
    satisfy) -- rollback_and_fail must call wait_healthy on the ROLLBACK
    target and only print a "rolled back...healthy" success message if that
    actually passes, with a distinct loud failure message if it doesn't."""
    text = DEPLOY_SH_PATH.read_text()

    rollback_fn = text[text.index("rollback_and_fail() {"):]
    assert "wait_healthy janasunani-api" in rollback_fn
    assert "wait_healthy janasunani-frontend" in rollback_fn
    assert "ROLLBACK FAILED" in rollback_fn
    assert "verified healthy" in rollback_fn


def test_up_d_and_caddy_reload_route_through_rollback():
    """Under `set -euo pipefail`, a bare `docker compose up -d` or `caddy
    reload` failure exits the script directly -- AFTER the stack has
    already started mutating -- with no rollback attempted (H2 finding).
    Both must route through rollback_and_fail instead of bare-exiting."""
    text = DEPLOY_SH_PATH.read_text()

    assert re.search(r"docker compose up -d \|\| rollback_and_fail", text), (
        "the main `docker compose up -d` must route failure through "
        "rollback_and_fail"
    )
    assert re.search(r"caddy reload[^\n]*\|\| rollback_and_fail", text), (
        "the main Caddy reload must route failure through rollback_and_fail"
    )


# --- Stubbed-docker subprocess tests: run the REAL deploy.sh with a fake
# `docker`/`flock` on PATH so the rollback control flow (H1/H2) is exercised
# as an actual regression test, not just a grep over the script text. Each
# stub recognizes exactly the argv patterns deploy.sh uses (mirrors the
# script's own docker invocations); a `docker compose up -d` call count in a
# state file distinguishes the forward deploy attempt from the rollback
# attempt, so tests can independently control whether each one "succeeds".

_STUB_DOCKER = r"""#!/usr/bin/env bash
set -euo pipefail
state="${STUB_STATE_DIR:?STUB_STATE_DIR not set}"
args="$*"

case "$args" in
  "compose version --short")
    echo "2.35.0"; exit 0 ;;
  "compose pull api frontend proxy")
    exit 0 ;;
  "compose up -d")
    count_file="$state/up_count"
    count=0
    [[ -f "$count_file" ]] && count="$(cat "$count_file")"
    count=$((count + 1))
    echo "$count" > "$count_file"
    if [[ "$count" -eq 1 ]]; then
      exit "${STUB_FIRST_UP_EXIT:-0}"
    else
      exit "${STUB_ROLLBACK_UP_EXIT:-0}"
    fi
    ;;
  "compose exec -T proxy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile")
    exit "${STUB_RELOAD_EXIT:-0}" ;;
esac

if [[ "${1:-}" == "inspect" ]]; then
  fmt="${3:-}"
  if [[ "$fmt" == *State.Running* ]]; then
    echo "${STUB_PROXY_RUNNING:-true}"
    exit 0
  fi
  if [[ "$fmt" == *State.Health.Status* ]]; then
    count_file="$state/up_count"
    count=0
    [[ -f "$count_file" ]] && count="$(cat "$count_file")"
    if [[ "$count" -le 1 ]]; then
      echo "${STUB_FIRST_HEALTH:-unhealthy}"
    else
      echo "${STUB_ROLLBACK_HEALTH:-unhealthy}"
    fi
    exit 0
  fi
  echo "unhealthy"
  exit 0
fi

if [[ "${1:-}" == "logs" ]]; then
  echo "fake log line"
  exit 0
fi

if [[ "${1:-}" == "images" ]]; then
  exit 0
fi

if [[ "${1:-}" == "image" && "${2:-}" == "prune" ]]; then
  exit 0
fi

if [[ "${1:-}" == "rmi" ]]; then
  exit 0
fi

echo "UNSTUBBED docker invocation: $*" >&2
exit 99
"""

_STUB_FLOCK = "#!/bin/sh\nexit 0\n"


def _write_executable(path, content):
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _make_stub_deploy_dir(tmp_path, caddyfile_diverged=False, **docker_env):
    """A scratch deploy/ directory with a stubbed docker+flock on PATH,
    running the REAL deploy.sh (copied verbatim) against fake docker state.
    Returns (deploy_dir, env) for subprocess.run."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "docker", _STUB_DOCKER)
    _write_executable(bin_dir / "flock", _STUB_FLOCK)

    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    shutil.copy(DEPLOY_SH_PATH, deploy_dir / "deploy.sh")
    (deploy_dir / "deploy.sh").chmod(0o755)
    proxy_dir = deploy_dir / "proxy"
    proxy_dir.mkdir()
    deployed_content = "example.com {\n\trespond 200\n}\n"
    (proxy_dir / "Caddyfile.deployed").write_text(deployed_content)
    live_content = "example.com {\n\trespond 200\n}\n# shipped alongside this deploy\n" if caddyfile_diverged else deployed_content
    (proxy_dir / "Caddyfile").write_text(live_content)
    (deploy_dir / ".env").write_text(
        "POSTGRES_PASSWORD=testpass\nSITE_ADDRESS=:80\nIMAGE_TAG=prev-good-tag\n"
    )
    (deploy_dir / "proxy.env").write_text(
        "DEMO_USER=demo\n"
        "DEMO_PASSWORD_HASH=$2a$14$abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXY\n"
    )

    state_dir = tmp_path / "state"
    state_dir.mkdir()

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["STUB_STATE_DIR"] = str(state_dir)
    env["WAIT_HEALTHY_TIMEOUT_S"] = "1"
    env["WAIT_HEALTHY_POLL_S"] = "1"
    env["MIN_FREE_KIB"] = "1"  # don't let the real disk-space preflight block a test run
    env["IMAGE_TAG"] = "new-bad-tag"
    env.update(docker_env)
    return deploy_dir, env, deployed_content


def _run_deploy_sh(deploy_dir, env):
    return subprocess.run(
        ["bash", str(deploy_dir / "deploy.sh")],
        cwd=deploy_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_rollback_flow_succeeds_and_reports_honestly(tmp_path):
    """H1 regression test: the forward deploy's health check fails, the
    script rolls back, the rollback target comes up healthy, and the script
    says so honestly -- without this fix it either bare-exited (pre-H1) or
    could have printed "rolled back" without ever checking."""
    deploy_dir, env, _ = _make_stub_deploy_dir(
        tmp_path,
        STUB_FIRST_HEALTH="unhealthy",
        STUB_ROLLBACK_HEALTH="healthy",
    )
    result = _run_deploy_sh(deploy_dir, env)
    combined = result.stdout + result.stderr

    assert result.returncode == 1, combined
    assert "Rolling back to last known-good IMAGE_TAG=prev-good-tag" in combined
    assert "rolled back to prev-good-tag and verified healthy" in combined
    assert "ROLLBACK FAILED" not in combined

    env_text = (deploy_dir / ".env").read_text()
    assert "IMAGE_TAG=new-bad-tag" not in env_text
    assert "IMAGE_TAG=prev-good-tag" in env_text


def test_rollback_flow_reports_failure_honestly_when_rollback_target_also_unhealthy(tmp_path):
    """H1's exact regression scenario: the previous image ALSO can't come
    up healthy (e.g. a migration it can't satisfy). Must print a loud
    ROLLBACK FAILED / demo-is-down message and exit non-zero -- must NOT
    claim the rollback succeeded."""
    deploy_dir, env, _ = _make_stub_deploy_dir(
        tmp_path,
        STUB_FIRST_HEALTH="unhealthy",
        STUB_ROLLBACK_HEALTH="unhealthy",
    )
    result = _run_deploy_sh(deploy_dir, env)
    combined = result.stdout + result.stderr

    assert result.returncode == 1, combined
    assert "ROLLBACK FAILED" in combined
    assert "DOWN" in combined
    assert "verified healthy" not in combined


def test_up_d_failure_routes_through_rollback_not_bare_exit(tmp_path):
    """H2(a) regression test: `docker compose up -d` itself fails (not just
    the health check afterward). Must still attempt a rollback, not bare-
    exit under set -e."""
    deploy_dir, env, _ = _make_stub_deploy_dir(
        tmp_path,
        STUB_FIRST_UP_EXIT="1",
        STUB_ROLLBACK_UP_EXIT="0",
        STUB_ROLLBACK_HEALTH="healthy",
    )
    result = _run_deploy_sh(deploy_dir, env)
    combined = result.stdout + result.stderr

    assert result.returncode == 1, combined
    assert "Rolling back to last known-good IMAGE_TAG=prev-good-tag" in combined
    assert "rolled back to prev-good-tag and verified healthy" in combined


def test_rollback_restores_a_diverged_caddyfile(tmp_path):
    """H2(b) regression test: the live Caddyfile diverged from
    Caddyfile.deployed (shipped alongside the failed deploy). rollback_and_fail
    must restore it from the snapshot, independent of whether the image tag
    also changed."""
    deploy_dir, env, deployed_content = _make_stub_deploy_dir(
        tmp_path,
        caddyfile_diverged=True,
        STUB_FIRST_HEALTH="unhealthy",
        STUB_ROLLBACK_HEALTH="healthy",
    )
    live_before = (deploy_dir / "proxy" / "Caddyfile").read_text()
    assert live_before != deployed_content, "test setup sanity check"

    result = _run_deploy_sh(deploy_dir, env)
    combined = result.stdout + result.stderr

    assert result.returncode == 1, combined
    assert "Restoring the last known-good Caddyfile" in combined
    assert (deploy_dir / "proxy" / "Caddyfile").read_text() == deployed_content


# --- CPU-only torch for the api image (issue #48) ----------------------------
#
# The always-on box is CPU-only and hosts the production Postgres, so the api
# image must not carry the CUDA runtime. This is a resolution property, not a
# runtime one, so it is asserted against the real pyproject.toml and uv.lock —
# and it cannot be caught by running the tests, because on darwin the `demo`
# extra still resolves the ordinary PyPI wheel.

PYPROJECT_PATH = ROOT_DIR / "pyproject.toml"
UV_LOCK_PATH = ROOT_DIR / "uv.lock"

def _pyproject() -> dict:
    import tomllib

    return tomllib.loads(PYPROJECT_PATH.read_text())


def test_demo_extra_sources_torch_from_the_cpu_index():
    """`deploy/api.Dockerfile` builds `--extra demo`; that extra must pin torch
    to PyTorch's CPU wheel index."""
    pyproject = _pyproject()
    indexes = {i["name"]: i for i in pyproject["tool"]["uv"].get("index", [])}
    assert "pytorch-cpu" in indexes, "the CPU wheel index is not declared"
    assert indexes["pytorch-cpu"]["url"] == "https://download.pytorch.org/whl/cpu"
    # explicit: nothing resolves from this index unless a source sends it there
    assert indexes["pytorch-cpu"]["explicit"] is True

    sources = pyproject["tool"]["uv"]["sources"]["torch"]
    assert [s["extra"] for s in sources] == ["demo"], (
        "torch's CPU source must apply to `demo` only — categorizer and "
        "ocr-deepseek need CUDA wheels for the GPU-box batch jobs"
    )
    assert all(s["index"] == "pytorch-cpu" for s in sources)


def test_documented_live_launches_use_the_demo_extra():
    """The component extras omit PII; only demo is a complete live environment."""
    commands: list[tuple[str, str]] = []
    for path in LIVE_LAUNCH_PATHS:
        commands.extend(
            (str(path.relative_to(ROOT_DIR)), line.strip())
            for line in path.read_text().splitlines()
            if "uv run" in line and "janasunani-api-live" in line
        )

    assert commands, "no documented live launch commands found"
    stale = [
        f"{path}: {command}"
        for path, command in commands
        if "uv run --extra demo janasunani-api-live" not in command
    ]
    assert not stale, f"live launches must use the complete demo extra: {stale}"


def test_gpu_extras_keep_cuda_torch():
    """The counterpart: pinning the GPU extras to CPU wheels would silently
    remove GPU acceleration from the DeepSeek OCR run and the MuRIL embedding
    backfill (ROADMAP §5.3 S4)."""
    torch_sources = _pyproject()["tool"]["uv"]["sources"]["torch"]
    pinned = {s["extra"] for s in torch_sources}
    for extra in ("categorizer", "pipeline-core", "ocr-deepseek"):
        assert extra not in pinned, f"{extra} must keep the default CUDA torch"


def _demo_requirements_for_linux() -> dict[str, str]:
    """The real resolved requirement set for `--extra demo` on linux.

    Exports the actual lock via uv and evaluates each marker for the box's
    platform, rather than inferring reachability from individual edges. An
    edge's own marker is not enough: a CUDA package can be pulled by an
    unconditional edge from a parent that `demo` selects, and inspecting
    markers in isolation cannot see that (xgboost -> nvidia-nccl-cu12 is
    exactly this shape).
    """
    proc = subprocess.run(
        ["uv", "export", "--frozen", "--extra", "demo", "--no-dev",
         "--no-hashes", "--no-emit-project"],
        cwd=ROOT_DIR, capture_output=True, text=True, check=True,
    )
    resolved: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        requirement, _, marker = line.partition(";")
        if marker.strip():
            env = {
                "sys_platform": "linux",
                "platform_system": "Linux",
                "os_name": "posix",
                "platform_machine": "x86_64",
                "python_version": "3.13",
                "implementation_name": "cpython",
                "platform_python_implementation": "CPython",
            }
            if not Marker(marker.strip()).evaluate(env):
                continue
        name, _, version = requirement.strip().partition("==")
        resolved[re.split(r"[\[ ]", name)[0].lower()] = version.strip()
    return resolved


def test_only_torch_is_sourced_from_the_pytorch_index():
    """`explicit = true` must actually hold across the whole lock.

    download.pytorch.org is not a torch-only mirror — it also carries old
    copies of common packages (certifi, requests, urllib3 among them). Without
    `explicit`, uv treats it as a general index and will happily resolve those
    from it, silently downgrading the TLS stack. That is not hypothetical: it
    happened to this branch while mutation-testing the `explicit` assertion
    itself, and shipped certifi 2022.12.7 before review caught it.

    Asserted on the lock rather than on the pyproject flag, because the flag
    being right does not prove the lock was generated with it right.
    """
    leaked = []
    for block in UV_LOCK_PATH.read_text().split("[[package]]"):
        name = re.search(r'^name = "([^"]+)"', block, re.M)
        pytorch_sourced = re.search(
            r'^source = \{ registry = "https://download\.pytorch\.org[^"]*" \}',
            block,
            re.M,
        )
        if name and pytorch_sourced and name.group(1) != "torch":
            leaked.append(name.group(1))
    assert not leaked, (
        "packages resolved from the PyTorch index that should come from PyPI: "
        f"{sorted(leaked)} — is `explicit = true` set on the index?"
    )


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_demo_resolves_cpu_torch_on_linux():
    """The api image gets the CPU build, not the CUDA one."""
    resolved = _demo_requirements_for_linux()
    assert resolved.get("torch") == "2.12.1+cpu", (
        f"expected the CPU torch build on linux, got {resolved.get('torch')!r}"
    )


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_demo_resolves_live_processor_pii_dependencies_on_linux():
    """The documented live environment must carry the production PII stack."""
    resolved = _demo_requirements_for_linux()
    required = {
        "en-core-web-sm",
        "presidio-analyzer",
        "presidio-anonymizer",
        "spacy",
    }
    assert required <= resolved.keys(), (
        f"demo extra is missing live PII dependencies: {sorted(required - resolved.keys())}"
    )


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_demo_carries_no_cuda_payload_beyond_xgboosts_nccl():
    """The property that shrinks the image, checked against a real resolution.

    torch's CUDA payload (cuda-toolkit, cuda-bindings, nvidia-cublas, cudnn,
    cusparselt, nccl-cu13, nvshmem, triton) must be gone entirely.

    `nvidia-nccl-cu12` is the one documented exception: `xgboost` declares it
    unconditionally on linux, and `demo` pulls xgboost via `pipeline-core`.
    The live processor never uses the format classifier, so it is dead weight
    in this image — tracked separately. Asserted explicitly rather than
    excluded, so this fails if the CUDA surface grows again.
    """
    resolved = _demo_requirements_for_linux()
    cuda = {
        name for name in resolved
        if name.startswith(("nvidia-", "cuda-")) or name == "triton"
    }
    assert cuda == {"nvidia-nccl-cu12"}, (
        "CUDA packages in the linux demo resolution changed; expected only "
        f"xgboost's nvidia-nccl-cu12, got {sorted(cuda)}"
    )
    # and the specific thing this change removed really is absent
    assert "nvidia-nccl-cu12" in resolved and "nvidia-nccl-cu13" not in resolved


def test_locked_torch_resolves_to_a_cpu_wheel_for_linux():
    """A `+cpu` build must actually be locked, with a linux x86_64 wheel —
    the box's platform."""
    lock = UV_LOCK_PATH.read_text()
    assert 'version = "2.12.1+cpu"' in lock, "no CPU torch build in the lock"
    assert "torch-2.12.1%2Bcpu-cp313-cp313-manylinux_2_28_x86_64.whl" in lock, (
        "the locked CPU torch has no linux x86_64 wheel"
    )


# --- Demo rehearsal scripts (Unit B) ---------------------------------------
#
# The 13 Aug freeze gate lives in scripts/demo_rehearsal.sh (Phases A-D) and
# the thin Tier-3 wrapper scripts/e2e_pipeline.sh. These are the integration
# rehearsal's own executables — guard them with the same real-file checks
# test_entrypoint_and_deploy_script_are_valid_shell uses (parse the file on
# disk that CI and the operator actually run).

REHEARSAL_SH_PATH = ROOT_DIR / "scripts" / "demo_rehearsal.sh"
E2E_PIPELINE_SH_PATH = ROOT_DIR / "scripts" / "e2e_pipeline.sh"
MAKEFILE_PATH = ROOT_DIR / "Makefile"


def test_demo_rehearsal_script_exists_and_is_executable():
    """The rehearsal gate must be a tracked, executable shell script."""
    assert REHEARSAL_SH_PATH.exists(), f"{REHEARSAL_SH_PATH} does not exist"
    assert REHEARSAL_SH_PATH.is_file()
    mode = REHEARSAL_SH_PATH.stat().st_mode
    assert mode & stat.S_IEXEC, f"{REHEARSAL_SH_PATH} is not executable"
    # Must start with a bash shebang
    first_line = REHEARSAL_SH_PATH.read_text().splitlines()[0]
    assert first_line.startswith("#!") and "bash" in first_line


def test_e2e_pipeline_script_exists_and_is_executable():
    """The Tier-3 pipeline wrapper must be a tracked, executable shell script."""
    assert E2E_PIPELINE_SH_PATH.exists(), f"{E2E_PIPELINE_SH_PATH} does not exist"
    assert E2E_PIPELINE_SH_PATH.is_file()
    mode = E2E_PIPELINE_SH_PATH.stat().st_mode
    assert mode & stat.S_IEXEC, f"{E2E_PIPELINE_SH_PATH} is not executable"
    first_line = E2E_PIPELINE_SH_PATH.read_text().splitlines()[0]
    assert first_line.startswith("#!") and "bash" in first_line


def test_rehearsal_scripts_are_valid_shell():
    """Both scripts must pass `bash -n` (same gate as deploy.sh)."""
    for path in (REHEARSAL_SH_PATH, E2E_PIPELINE_SH_PATH):
        result = subprocess.run(
            ["bash", "-n", str(path)], capture_output=True, text=True
        )
        assert result.returncode == 0, f"{path.name} failed bash -n: {result.stderr}"


def test_demo_rehearsal_script_covers_required_phases():
    """Static content check: the rehearsal script must implement Phases A-D
    from the plan (ruff+pytest, health curl, supervisor/history, frontend,
    artifact presence, optional model smoke) and warn vs fail handling."""
    text = REHEARSAL_SH_PATH.read_text()
    # Phase A
    assert "ruff check" in text, "Phase A must run ruff check"
    assert "janasunani-demo-preflight" in text
    # Phase B — health + submission + round-trip + supervisor/history/frontend
    assert "/health" in text
    assert "processor" in text and "pipeline" in text
    assert "/grievance" in text
    assert "/supervisor" in text
    assert "/history" in text
    assert "FRONTEND_URL" in text or "127.0.0.1:3000" in text
    # Phase C — artifacts
    assert "routing_crosswalk.json" in text
    assert "outputs/findings" in text
    assert "outputs/sarvam" in text or "sarvam" in text.lower()
    # Phase D — optional model smoke
    assert "JANASUNANI_RUN_MODEL_SMOKE" in text
    assert "test_inference_model_smoke" in text
    # Must exit non-zero on failure and have warn vs fail configurability
    assert "REHEARSAL_STRICT" in text
    assert "exit 1" in text


def test_e2e_pipeline_script_covers_required_steps():
    """The Tier-3 wrapper must invoke the pipeline on a non-PII fixture, export,
    materialize, and optionally curl the rehearsal checks — and must never
    default to data/raw/."""
    text = E2E_PIPELINE_SH_PATH.read_text()
    assert "janasunani-pipeline" in text
    assert "janasunani-export-pipeline" in text or "export_pipeline" in text
    assert "janasunani-materialize" in text or "materialize" in text
    assert "tests/fixtures" in text
    assert "data/raw" not in text or "not data/raw" in text or "never data/raw" in text, (
        "e2e_pipeline.sh must not default to data/raw/ (real PII) — use tests/fixtures/"
    )
    # Must exit non-zero on failure
    assert "set -e" in text or "set -euo pipefail" in text


def test_e2e_pipeline_script_resets_the_default_scratch_db_before_running():
    """#215: a stale PIPELINE_DB from a prior run must not silently make the
    rehearsal non-isolated (every stage skips already-processed rows, the
    exporter re-upserts old ones into OLTP). Static check that the reset
    guard and the `init-db` call are both present; the guard's actual
    refuse/reset behavior is exercised for real below."""
    text = E2E_PIPELINE_SH_PATH.read_text()
    assert "init-db" in text
    assert 'DEFAULT_PIPELINE_DB="data/output/pipeline-e2e.sqlite"' in text
    assert "E2E_ALLOW_DB_RESET" in text
    assert 'rm -f "$PIPELINE_DB"' in text


def test_e2e_pipeline_script_refuses_to_delete_a_non_default_pipeline_db(tmp_path):
    """#215 regression test: the destructive `rm -f "$PIPELINE_DB"` path must
    never fire against a database this script did not itself pick as
    disposable -- e.g. the pipeline CLI's OWN default artifact DB
    (data/output/pipeline.sqlite), which a real ingestion run may have
    populated. Runs the real script (copied verbatim, like the deploy.sh
    stub tests above) against a stale non-default PIPELINE_DB and confirms
    it refuses and exits non-zero *before* touching the file -- not a grep
    over the script text."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script_copy = scripts_dir / "e2e_pipeline.sh"
    shutil.copy(E2E_PIPELINE_SH_PATH, script_copy)
    script_copy.chmod(0o755)

    stale_db = tmp_path / "pipeline.sqlite"  # NOT the script's own default name
    marker = b"stale artifact db -- must not be touched by the guard"
    stale_db.write_bytes(marker)

    fixture = ROOT_DIR / "tests" / "fixtures" / "demo_letter.png"
    assert fixture.exists(), "test fixture missing"

    env = dict(os.environ)
    env["FIXTURE"] = str(fixture)
    env["PIPELINE_DB"] = str(stale_db)
    env.pop("E2E_ALLOW_DB_RESET", None)
    env.pop("E2E_ALLOW_REUSE_DB", None)

    result = subprocess.run(
        ["bash", str(script_copy)],
        cwd=scripts_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    combined = result.stdout + result.stderr

    assert result.returncode != 0, combined
    assert "refusing to remove" in combined, combined
    assert "E2E_ALLOW_DB_RESET" in combined
    # The guard must fire before anything destructive happens -- the stale
    # file must survive completely untouched.
    assert stale_db.read_bytes() == marker


def test_e2e_pipeline_script_allows_reuse_with_explicit_opt_in(tmp_path):
    """The companion positive case: E2E_ALLOW_REUSE_DB=1 must let a
    non-default, pre-existing PIPELINE_DB through without deleting it."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script_copy = scripts_dir / "e2e_pipeline.sh"
    shutil.copy(E2E_PIPELINE_SH_PATH, script_copy)
    script_copy.chmod(0o755)

    stale_db = tmp_path / "pipeline.sqlite"
    marker = b"stale artifact db -- reuse must not touch this"
    stale_db.write_bytes(marker)

    fixture = ROOT_DIR / "tests" / "fixtures" / "demo_letter.png"

    env = dict(os.environ)
    env["FIXTURE"] = str(fixture)
    env["PIPELINE_DB"] = str(stale_db)
    env["E2E_ALLOW_REUSE_DB"] = "1"
    # Point MODELS_DIR at a location with no models -- the script only warns
    # on a missing models dir, and we don't need the run to actually
    # succeed; we only need to observe the DB guard's own decision before
    # the (heavy, model-dependent) pipeline run step runs.
    env["MODELS_DIR"] = str(tmp_path / "no-models")

    result = subprocess.run(
        ["bash", str(script_copy)],
        cwd=scripts_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    combined = result.stdout + result.stderr

    # The guard itself must not refuse or delete -- whatever happens
    # downstream (the real pipeline run needs models this environment may
    # not have) is out of scope for this test.
    assert "refusing to remove" not in combined, combined
    assert "reusing existing pipeline DB" in combined, combined
    assert stale_db.read_bytes() == marker


def test_demo_rehearsal_script_checks_specific_closure_artifact_names():
    """#216: the closure gate must check for the exact filenames
    janasunani/serving/intelligence.py's closure reader accepts
    (_CLOSURE_ARTIFACT_NAMES), not "any file present in outputs/findings/".
    A PII/discard finding sitting in that directory must not make the gate
    report closure artifacts as available."""
    text = REHEARSAL_SH_PATH.read_text()
    assert "closure_finding_summary.csv" in text
    assert "closure_recording_no_action.csv" in text
    # The old bug: gating success on "any file in outputs/findings/ exists"
    # rather than one of the two specific names above.
    assert 'find outputs/findings -type f | wc -l' not in text


def test_demo_rehearsal_script_honors_the_aggregates_dir_env_var():
    """#218: JANASUNANI_SUPERVISOR_AGGREGATES_DIR (workload/spike) must not
    be silently overwritten with JANASUNANI_SUPERVISOR_FINDINGS_DIR
    (closure) -- the two are configured separately in
    janasunani/serving/intelligence.py's supervisor_provider_from_env /
    ArtifactSupervisorProvider, and the rehearsal's AGG_DIR must mirror that
    same fallback (aggregates dir first, findings dir only as a fallback)."""
    text = REHEARSAL_SH_PATH.read_text()
    assert (
        'AGG_DIR="${JANASUNANI_SUPERVISOR_AGGREGATES_DIR:-${JANASUNANI_SUPERVISOR_FINDINGS_DIR:-}}"'
        in text
    )


def test_makefile_has_rehearsal_target():
    """`make rehearsal` must exist and delegate to scripts/demo_rehearsal.sh."""
    text = MAKEFILE_PATH.read_text()
    assert re.search(r"^rehearsal\s*:", text, re.M), "Makefile missing rehearsal target"
    # The target must actually invoke demo_rehearsal.sh (not just be a stub)
    rehearsal_block = text[text.index("rehearsal:") : text.index("rehearsal:") + 500]
    assert "demo_rehearsal.sh" in rehearsal_block, (
        "rehearsal target must run scripts/demo_rehearsal.sh"
    )
    # Must be .PHONY so make treats it as a command even if a file named
    # rehearsal exists
    assert re.search(r"\.PHONY:.*rehearsal", text), "rehearsal must be .PHONY"


# --- Phase C behaviour, run against real directories -----------------------
#
# Codex findings on #231. Both were cases where the rehearsal's verdict and
# ArtifactSupervisorProvider's behaviour disagreed, which is the one thing a
# rehearsal gate must not do: it either passes a demo that will not work, or
# fails a demo that would have.


def _run_phase_c(tmp_path, *, env_extra=None, findings=()):
    """Execute the real phase_c_artifacts body against a scratch tree."""
    workdir = tmp_path / "repo"
    (workdir / "outputs" / "findings").mkdir(parents=True)
    for name in findings:
        (workdir / "outputs" / "findings" / name).write_text("a,b\n1,2\n")
    # Item 1 (routing crosswalk) always fails when the file is absent,
    # regardless of strict mode. Stub it present so these tests measure only
    # the closure/aggregates behaviour under test, not an unrelated failure.
    crosswalk = workdir / "janasunani" / "routing" / "reference" / "routing_crosswalk.json"
    crosswalk.parent.mkdir(parents=True)
    crosswalk.write_text("{}\n")

    script = REHEARSAL_SH_PATH.read_text()
    # Take the helpers, check_artifact, and the phase C function verbatim, so
    # the test runs the shipped logic rather than a paraphrase of it. In
    # particular, check_artifact must be the real existence-based helper, not
    # a mock -- one of the bugs under test here is exactly that helper
    # reporting OK for a path that exists but should not count as success.
    helpers = "\n".join(
        line for line in script.splitlines()
        if line.startswith(("info()", "ok()", "warn()", "log()", "fail()"))
    )
    ca_start = script.index("check_artifact() {")
    ca_end = script.index("\n}\n", ca_start) + 3
    check_artifact_fn = script[ca_start:ca_end]

    start = script.index("phase_c_artifacts() {")
    end = script.index("\n}\n", start) + 3
    body = script[start:end]

    harness = f"""
set -u
WARNINGS=0
FAILURES=0
REHEARSAL_SKIP_ARTIFACTS="${{REHEARSAL_SKIP_ARTIFACTS:-0}}"
REHEARSAL_ALLOW_DATA="${{REHEARSAL_ALLOW_DATA:-0}}"
REHEARSAL_STRICT="${{REHEARSAL_STRICT:-0}}"
{helpers}
{check_artifact_fn}
{body}
phase_c_artifacts || true
echo "WARNINGS=$WARNINGS FAILURES=$FAILURES"
"""
    env = dict(os.environ)
    env.pop("JANASUNANI_SUPERVISOR_AGGREGATES_DIR", None)
    env.pop("JANASUNANI_SUPERVISOR_FINDINGS_DIR", None)
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", "-c", harness],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
    )


def test_phase_c_accepts_exactly_one_closure_artifact(tmp_path):
    result = _run_phase_c(tmp_path, findings=["closure_finding_summary.csv"])
    assert "closure: outputs/findings/closure_finding_summary.csv" in result.stdout
    assert "FAILURES=0" in result.stdout


def test_phase_c_rejects_two_closure_artifacts(tmp_path):
    """The provider reports unavailable unless exactly one candidate exists.

    Stopping at the first match let a strict rehearsal pass while the live
    closure panel was unavailable, which is the transition state between the
    two filenames. A duplicate is a deterministic break (the provider will
    unconditionally refuse to serve it), not a "not published yet" state, so
    this must fail the run outright rather than only when --strict is set --
    the failure count must actually move, not just the logged message.
    """
    result = _run_phase_c(
        tmp_path,
        findings=["closure_finding_summary.csv", "closure_recording_no_action.csv"],
    )
    assert "2 candidates present" in result.stdout
    assert "FAILURES=1" in result.stdout


def test_phase_c_falls_back_to_findings_when_aggregates_is_empty(tmp_path):
    """ArtifactSupervisorProvider searches both dirs, so the gate must too.

    An aggregates dir that is set but empty does not make the panels
    unavailable when the findings dir still holds both named artifacts.
    """
    empty = tmp_path / "aggregates-empty"
    empty.mkdir()
    findings = tmp_path / "findings"
    findings.mkdir()
    (findings / "workload.csv").write_text("a\n1\n")
    (findings / "spike.csv").write_text("a\n1\n")

    result = _run_phase_c(
        tmp_path,
        env_extra={
            "JANASUNANI_SUPERVISOR_AGGREGATES_DIR": str(empty),
            "JANASUNANI_SUPERVISOR_FINDINGS_DIR": str(findings),
        },
        findings=["closure_finding_summary.csv"],
    )
    assert f"aggregates: {findings} (workload.csv, spike.csv)" in result.stdout
    assert "FAILURES=0" in result.stdout


def test_phase_c_workload_in_aggregates_spike_in_findings_passes(tmp_path):
    """Each artifact falls back independently, per
    ArtifactSupervisorProvider._load_workload() / _load_spike(): workload.csv
    found in the aggregates dir and spike.csv found only in the findings dir
    must still pass, because the provider would serve both panels.
    """
    aggregates = tmp_path / "aggregates"
    aggregates.mkdir()
    (aggregates / "workload.csv").write_text("a\n1\n")
    findings = tmp_path / "findings"
    findings.mkdir()
    (findings / "spike.csv").write_text("a\n1\n")

    result = _run_phase_c(
        tmp_path,
        env_extra={
            "JANASUNANI_SUPERVISOR_AGGREGATES_DIR": str(aggregates),
            "JANASUNANI_SUPERVISOR_FINDINGS_DIR": str(findings),
        },
        findings=["closure_finding_summary.csv"],
    )
    assert f"aggregates: workload.csv in {aggregates}, spike.csv in {findings}" in result.stdout
    assert "FAILURES=0" in result.stdout


def test_phase_c_fails_when_aggregates_has_only_workload_csv(tmp_path):
    """ArtifactSupervisorProvider._load_spike() looks for spike.csv by name;
    a directory holding only workload.csv (with no spike.csv anywhere in the
    fallback chain) does not make the spike panel available. Counting "any
    csv present" let this pass; the gate must check workload.csv and
    spike.csv individually.
    """
    aggregates = tmp_path / "aggregates"
    aggregates.mkdir()
    (aggregates / "workload.csv").write_text("a\n1\n")

    result = _run_phase_c(
        tmp_path,
        env_extra={"JANASUNANI_SUPERVISOR_AGGREGATES_DIR": str(aggregates)},
        findings=["closure_finding_summary.csv"],
    )
    assert "missing: spike.csv" in result.stdout
    assert "FAILURES=1" in result.stdout


def test_phase_c_fails_on_unrelated_csv_in_aggregates_dir(tmp_path):
    """A directory holding some unrelated csv is not the same as holding
    workload.csv and spike.csv; the provider looks for those two names
    specifically, so an unrelated file must not report available.
    """
    aggregates = tmp_path / "aggregates"
    aggregates.mkdir()
    (aggregates / "unrelated.csv").write_text("a\n1\n")

    result = _run_phase_c(
        tmp_path,
        env_extra={"JANASUNANI_SUPERVISOR_AGGREGATES_DIR": str(aggregates)},
        findings=["closure_finding_summary.csv"],
    )
    assert "missing: workload.csv, spike.csv" in result.stdout
    assert "FAILURES=1" in result.stdout
