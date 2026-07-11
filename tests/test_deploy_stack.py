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

import subprocess

import yaml

from janasunani.config import ROOT_DIR

DEPLOY_DIR = ROOT_DIR / "deploy"
COMPOSE_PATH = DEPLOY_DIR / "docker-compose.yml"
CADDYFILE_PATH = DEPLOY_DIR / "proxy" / "Caddyfile"
DEPLOY_SH_PATH = DEPLOY_DIR / "deploy.sh"
ENTRYPOINT_PATH = DEPLOY_DIR / "api-entrypoint.sh"


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
    DEMO_PASSWORD_HASH must fail closed (compose's `:?` required-variable
    syntax); DEMO_USER may default since the username isn't the secret."""
    compose = _compose()

    hash_value = compose["services"]["proxy"]["environment"]["DEMO_PASSWORD_HASH"]
    assert ":?" in hash_value, (
        f"DEMO_PASSWORD_HASH must be a required (':?') compose variable, not a "
        f"defaulted one: {hash_value!r}"
    )
    assert "$2a$" not in hash_value, (
        f"DEMO_PASSWORD_HASH must not embed a real/published bcrypt hash as a "
        f"fallback default: {hash_value!r}"
    )

    # The env.example placeholder must stay empty too — never a real hash.
    env_example = (DEPLOY_DIR / ".env.example").read_text()
    for line in env_example.splitlines():
        if line.startswith("DEMO_PASSWORD_HASH="):
            assert line == "DEMO_PASSWORD_HASH=", (
                f"deploy/.env.example must not ship a real hash: {line!r}"
            )


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
