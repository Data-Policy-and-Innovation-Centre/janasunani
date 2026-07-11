# deploy/ — runtime composition and infrastructure

> ## ⚠️ The CPU box already exists and is always-on — do **not** blindly `terraform apply`
>
> The production CPU box (`i-0ef24e15a80ba7128`, EIP `52.66.116.80`) is **already
> running and holds the migrated 1.37M/6.56M-row production data** on its root
> volume. **`terraform apply` is NOT a safe no-op.** The Ubuntu AMI uses
> `most_recent = true`, so a newer Canonical image drifts the `ami` attribute and
> Terraform plans to **destroy and recreate** the box — the root volume is
> `delete_on_termination = true`, so a recreate **erases the production data** (a
> subnet-ordering change can force the same). The instance now carries a
> `lifecycle` guard (`prevent_destroy = true`, `ignore_changes = [ami,
> subnet_id]`), so `apply` will **error out rather than replace it** — but
> don't rely on that as your only check.
>
> **Always `terraform plan` first** and scan for `aws_instance.cpu_box must be
> replaced` or any `N to destroy` — if you see it, **STOP, do not apply.**
> Provisioning is a first-time / rebuild step, not routine ops on the live box.

Two halves: [`terraform/`](terraform/README.md) creates the EC2 boxes;
`docker-compose.yml` is what runs **on** the CPU box.

> **Full end-to-end runbook:** [docs/DEPLOY.md](../docs/DEPLOY.md) — provision →
> migrate → run → back up. This file and `terraform/README.md` are the
> per-directory detail behind it.

## docker-compose.yml (CPU box)

Four services today: `oltp` (Postgres, since Week 1) plus `api` / `frontend` /
`proxy`, which landed with the automated CI→GHCR→box deploy. `mlflow` is not
needed for the demo and is intentionally still absent (see
[docs/ROADMAP.md](../docs/ROADMAP.md) Phase 12). Config from `deploy/.env`
(gitignored — holds `POSTGRES_PASSWORD`, `IMAGE_TAG`, `SITE_ADDRESS`,
`DEMO_USER`/`DEMO_PASSWORD_HASH`; the box's copy is chmod 600 — see
[.env.example](.env.example)).

The `oltp` service (postgres:17, container `janasunani-oltp`) **adopts the
already-running production volume by name** (`janasunani-oltp`, declared
`external`). It binds to `127.0.0.1:5432` only — nothing on the public
interface; app services reach it over the compose network.

`api` (`deploy/api.Dockerfile` → `janasunani-api-live`) and `frontend`
(`frontend/Dockerfile`) publish **no ports** — they're only reachable through
`proxy`. `api` bind-mounts `../models`, `../data/interim`, and
`../data/raw/janasunani-mappings` read-only (models/data are never baked into
the image); `proxy` (`caddy:2-alpine`) is the sole public service, terminating
TLS via nip.io + automatic Let's Encrypt and gating the whole site behind
HTTP Basic Auth (`deploy/proxy/Caddyfile`) — production grievance data must
not be openly public.

**`deploy/deploy.sh` is the only sanctioned up-path.** It pulls the tagged
images, brings the stack up, and blocks until `api` reports healthy before
exiting 0 — run it by hand (`IMAGE_TAG=<sha> bash deploy/deploy.sh`) or let CI
run it (`.github/workflows/deploy.yml`, `workflow_dispatch`-only). Don't
`docker compose up` directly on the box; you'll skip the health gate.

Rules that protect the data:

- **NEVER `docker compose down -v`** — the external volume holds the migrated
  1.37M/6.56M-row production data. `deploy/deploy.sh` never runs `down`.
- Nightly `pg_dump | aws s3 cp` cron on the box is the backup path (bucket
  `grievance-database-backups-main`); the re-runnable migration is the deeper
  backstop.
- Never point pytest at this container (fixtures drop tables — see
  [tests/README](../tests/README.md)).

**Full automated-deploy runbook** (one-time box setup, secrets/vars,
rollback): [docs/DEPLOY.md](../docs/DEPLOY.md) §"Automated demo deploy".

## Terraform (summary — details in [terraform/README.md](terraform/README.md))

- **CPU box**: always-on t3.large, Elastic IP, IAM instance role scoped to the
  three project buckets (documents / DVC-cache prefix / backups). SSH is locked
  to `admin_cidr` — when your IP rotates (VPN on/off), update
  `terraform.tfvars` and re-apply; use `curl -4 -s ifconfig.me` (plain curl may
  return IPv6).
- **GPU box**: `gpu_box_count = 0/1` toggle, g6.xlarge from the Deep Learning
  Base AMI, create/destroy per use. Push outputs (`dvc push` / `aws s3 cp`)
  **before** toggling to 0 — the root volume dies with the instance.
- State/tfvars are local and gitignored (CI + pre-commit guards enforce).
- Both boxes hold no GitHub credential: clone via `ssh -A` agent forwarding.
