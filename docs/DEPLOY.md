# Deployment runbook — Janasunani 2.0 (cloud)

> ## ⚠️ The CPU box already exists and is always-on — do **not** blindly `terraform apply`
>
> The production CPU box (`i-0ef24e15a80ba7128`, EIP `52.66.116.80`) is **already
> running and holds the migrated 1.37M/6.56M-row production data** on its root
> volume. **`terraform apply` is NOT a safe no-op.** The Ubuntu AMI is resolved
> with `most_recent = true`, so when Canonical ships a newer image the `ami`
> attribute drifts and Terraform plans to **destroy and recreate** the box — and
> the root volume is `delete_on_termination = true`, so a recreate **erases the
> production data** (a subnet-ordering change can force the same). The instance
> now carries a `lifecycle` guard (`prevent_destroy = true`, `ignore_changes =
> [ami, subnet_id]`), so `apply` will **error out rather than replace it** — but
> don't rely on that as your only check.
>
> **Always run `terraform plan` first** and scan for `aws_instance.cpu_box must
> be replaced` or any `N to destroy`. If you see it, **STOP — do not apply.**
> The provisioning steps below (§1–§2) are for a **first-time bring-up or a
> deliberate rebuild** — not routine operation of the existing box.

The single source of truth for standing up and running Janasunani on AWS.
Consolidates what was spread across [deploy/README.md](../deploy/README.md) and
[deploy/terraform/README.md](../deploy/terraform/README.md); those remain as
per-directory detail, this is the end-to-end procedure.

## Architecture at a glance

Two EC2 boxes; one always on. Infrastructure is Terraform ([deploy/terraform/](../deploy/terraform/));
what runs *on* the CPU box is Docker Compose ([deploy/docker-compose.yml](../deploy/docker-compose.yml)).

| | **CPU box** (always on) | **GPU box** (on demand) |
|---|---|---|
| Type | `t3.xlarge` for the migration, downsize to `t3.large` after | `g6.xlarge` (L4 24 GB) |
| AMI | Ubuntu 24.04 | Deep Learning **Base** AMI |
| Address | **Elastic IP** (stable) | ephemeral public IP (dies with the box) |
| Lifecycle | stop/start (keeps EIP + EBS) | **create/destroy** (`gpu_box_count = 0/1`) |
| Runs | Postgres OLTP (Compose) + migration/materialize CLIs | DeepSeek OCR batch / demo (`scripts/gpu_smoke.sh`) |
| State | production data on an external named volume | **nothing stateful** |
| Cost | ~always-on t3 | ~$1/hr only while up |

Both share one **IAM instance role** (no static keys) scoped to three existing
S3 buckets, and neither holds a GitHub credential — you clone the private repo
(and its private `dpic` dependency) via **SSH agent forwarding** (`ssh -A`).

**Region** `ap-south-1` · GPU pinned to **`ap-south-1a`** (g6 isn't offered in 1c).
Current live CPU box: `i-0ef24e15a80ba7128`, EIP `52.66.116.80`.

## Prerequisites (local machine)

- Terraform, AWS CLI v2, an SSH keypair (default `~/.ssh/id_ed25519[.pub]`).
- AWS credentials with rights to create EC2/EIP/IAM/security groups.
- The three S3 buckets already exist (Terraform does **not** manage them):
  `janasunani-documents-main`, `dpic-dvc-cache` (prefix `janasunani`),
  `grievance-database-backups-main`.

## 1 · Provision the infrastructure

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars
#   set admin_cidr to your IP/32:   echo "$(curl -4 -s ifconfig.me)/32"
terraform init
terraform plan
terraform apply
```

Outputs: `public_ip`, `instance_id`, `ssh_command`, and (when the GPU box is up)
`gpu_box_ip` / `gpu_ssh_command`.

> **SSH times out later?** `admin_cidr` pins SSH to your IP, which rotates
> (VPN on/off, ISP). Re-run `echo "$(curl -4 -s ifconfig.me)/32"`, update
> `terraform.tfvars`, `terraform apply`. Use plain **`curl -4`** — bare curl may
> return IPv6, which won't match the /32.

## 2 · CPU box — first-time bring-up

```bash
# agent forwarding is REQUIRED — the box has no GitHub key
ssh-add --apple-use-keychain ~/.ssh/id_ed25519      # local agent often starts empty
ssh -A ubuntu@$(terraform output -raw public_ip)

# --- on the box (bootstrap already installed docker + aws cli + uv;
#     check /var/log/cloud-init-output.log if anything's missing) ---
git clone git@github.com:Data-Policy-and-Innovation-Centre/janasunani.git
cd janasunani
uv sync
uv run dvc pull data/raw/Dump20250730.sql.dvc       # 3.2 GB dump, via the instance role
```

### Start Postgres and load the data

`deploy/.env` holds the DB password (gitignored; chmod 600 on the box):

```bash
cd ~/janasunani/deploy
cp .env.example .env            # set POSTGRES_PASSWORD (must match OLTP_DB_URL below)
chmod 600 .env
docker compose up -d oltp       # postgres:17, container janasunani-oltp, 127.0.0.1:5432 only
cd ~/janasunani

export OLTP_DB_URL="postgresql+asyncpg://postgres:<PW>@127.0.0.1:5432/janasunani"
uv run alembic upgrade head     # create/upgrade schema (engine-portable)
bash scripts/migrate.sh         # ephemeral MySQL → restore dump → load OLTP (idempotent)
#   tunables: DUMP, MYSQL_PORT (3307), KEEP_MYSQL (1), OLTP_DB_URL
```

A full run yields **1,371,288 complaints / 6,556,171 action-history rows**.
Run `migrate.sh` **on the box**, never across the internet.

### Materialize the analytics lake

DVC's `materialize` stage deps on the local SQLite path, so with Postgres OLTP
run the CLI directly and commit the outputs:

```bash
uv run janasunani-materialize          # OLTP (Postgres) → data/interim/*.parquet
uv run dvc commit && uv run dvc push    # push the Parquet outs to the DVC remote
```

## 3 · Run the application stack

The Compose stack **grows service-by-service** as phases land; today it defines
only `oltp`. Future services (`mlflow` → `api` → `frontend` → `proxy`) get added
at Phase 12 integration, reaching Postgres over the Compose network.

```bash
cd ~/janasunani/deploy
docker compose up -d            # bring up all defined services
docker compose ps               # health
```

## 4 · Backups (nightly `pg_dump`)

**Policy:** a nightly `pg_dump | aws s3 cp` writes a snapshot to
`s3://grievance-database-backups-main/janasunani/`. The IAM role already grants
the write (the `WriteBackups` statement in `deploy/terraform/iam.tf`).

**⚠ Important gap — the backup is NOT reproducible from code.** The script
(`~/bin/backup-oltp.sh`) and its crontab live **only on the box**; they were set
up by hand during the Week-1 bring-up and are **not** in `user_data.sh` or the
repo. If the CPU box is rebuilt from Terraform, **the nightly backup will not
come back on its own** — recreate the script + cron manually. The re-runnable
migration (`migrate.sh` from the DVC-tracked dump) is the deeper backstop.

Manual on-demand backup / verify:
```bash
~/bin/backup-oltp.sh
aws s3 ls s3://grievance-database-backups-main/janasunani/
```
*(TODO: codify this into `user_data.sh` or a systemd timer so it survives a rebuild.)*

## 5 · GPU box (on demand)

```bash
# up: set gpu_box_count = 1 in terraform.tfvars, then
terraform apply
ssh -A ubuntu@$(terraform output -raw gpu_box_ip)

# on the box:
git clone git@github.com:Data-Policy-and-Innovation-Centre/janasunani.git && cd janasunani
bash scripts/gpu_smoke.sh       # 2-file DeepSeek smoke: format (pipeline-core env)
                                # + OCR (ocr-deepseek env); one uv env per conflicting extra

# down: push outputs FIRST (dvc push / aws s3 cp) — the root volume dies with the box.
# then set gpu_box_count = 0 and
terraform apply
```

## 6 · Lifecycle & cost control

```bash
# pause the CPU box (keeps the EIP address and the EBS/volume data):
aws ec2 stop-instances  --instance-ids $(terraform output -raw instance_id)
aws ec2 start-instances --instance-ids $(terraform output -raw instance_id)
```
The GPU box is create/destroy (toggle `gpu_box_count`), never stop/start.

## 7 · Hard rules (violating these loses production data or leaks PII)

1. **Never `docker compose down -v`** on the CPU box — the external volume
   `janasunani-oltp` holds the migrated 1.37M/6.56M-row production data.
2. **Never point pytest at the box's Postgres** — test fixtures DROP TABLES.
   Tests use a throwaway Postgres on `127.0.0.1:5433` only.
3. **Push GPU-box outputs before teardown** — its root volume dies with it.
4. Terraform **state / tfvars / keys stay local** (gitignored; pre-commit + CI
   guards enforce). The Postgres password lives only in the box's chmod-600
   `deploy/.env`.
5. S3 access is via the **instance role** — no static keys on the boxes.

## Reference

**Terraform variables** ([variables.tf](../deploy/terraform/variables.tf)):
`aws_region` (ap-south-1), `admin_cidr` (no default — your IP/32),
`instance_type` (t3.xlarge), `root_volume_gb` (150), `gpu_box_count` (0),
`gpu_instance_type` (g6.xlarge), `gpu_availability_zone` (ap-south-1a),
`ssh_public_key_path` (`~/.ssh/id_ed25519.pub`), and the three bucket vars.

**Buckets:** documents `janasunani-documents-main` · DVC cache
`dpic-dvc-cache/janasunani` · DB backups `grievance-database-backups-main`.

**See also:** [deploy/terraform/README.md](../deploy/terraform/README.md) (IaC
detail), [deploy/README.md](../deploy/README.md) (compose detail),
[docs/ROADMAP.md](ROADMAP.md) (sequencing), [docs/HANDOFF.md](HANDOFF.md)
(cloud state + safety rules).
