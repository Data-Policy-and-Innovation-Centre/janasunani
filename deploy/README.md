# deploy/ — runtime composition and infrastructure

Two halves: [`terraform/`](terraform/README.md) creates the EC2 boxes;
`docker-compose.yml` is what runs **on** the CPU box.

## docker-compose.yml (CPU box)

Grows service-by-service as Part II lands (`oltp` today; `mlflow` → `api` →
`frontend` → `proxy` to come). Config from `deploy/.env` (gitignored — holds
`POSTGRES_PASSWORD`; the box's copy is chmod 600).

The `oltp` service (postgres:17, container `janasunani-oltp`) **adopts the
already-running production volume by name** (`janasunani-oltp`, declared
`external`). It binds to `127.0.0.1:5432` only — nothing on the public
interface; future app services reach it over the compose network.

Rules that protect the data:

- **NEVER `docker compose down -v`** — the external volume holds the migrated
  1.37M/6.56M-row production data.
- Nightly `pg_dump | aws s3 cp` cron on the box is the backup path (bucket
  `grievance-database-backups-main`); the re-runnable migration is the deeper
  backstop.
- Never point pytest at this container (fixtures drop tables — see
  [tests/README](../tests/README.md)).

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
