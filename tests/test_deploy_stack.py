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

import re
import subprocess

import yaml

from janasunani.config import ROOT_DIR

DEPLOY_DIR = ROOT_DIR / "deploy"
COMPOSE_PATH = DEPLOY_DIR / "docker-compose.yml"
CADDYFILE_PATH = DEPLOY_DIR / "proxy" / "Caddyfile"
DEPLOY_SH_PATH = DEPLOY_DIR / "deploy.sh"
ENTRYPOINT_PATH = DEPLOY_DIR / "api-entrypoint.sh"
DEPLOY_WORKFLOW_PATH = ROOT_DIR / ".github" / "workflows" / "deploy.yml"
DOCKERIGNORE_PATH = ROOT_DIR / ".dockerignore"


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
