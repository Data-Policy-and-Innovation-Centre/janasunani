# Deployment runbook — Janasunani 2.0 (cloud)

> **Bringing up the real-inference API?** For the step-by-step live-demo
> bring-up (preflight → Postgres/migrations → `janasunani-api-live` → health →
> submit), see [DEMO.md](DEMO.md). This document covers the surrounding cloud
> deployment (CPU box, compose, backups).

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

The Compose stack **grows service-by-service** as phases land: `oltp` (Week 1)
then `api` / `frontend` / `proxy` (the automated CI→GHCR→box deploy, §4
below). `mlflow` is not needed for the demo and stays absent — see
[docs/ROADMAP.md](ROADMAP.md) Phase 12.

```bash
cd ~/janasunani/deploy
docker compose up -d oltp       # first-time bring-up only — see §2 above
docker compose ps               # health
```

`api`/`frontend`/`proxy` are **not** brought up with a plain `docker compose
up -d`: they're pulled-and-deployed images, and `deploy/deploy.sh` is the only
sanctioned way to bring them up (it health-gates the rollout instead of
returning as soon as the containers start) — see §4.

## 4 · Automated demo deploy (CI → GHCR → box)

The routine way to ship a new build of `api`/`frontend`: GitHub Actions builds
both images, pushes them to a **private** GHCR, then SSHes into the box and
runs `deploy/deploy.sh`. Trigger is `workflow_dispatch` only — **Actions →
"Deploy demo" → Run workflow** (optionally set `image_tag` to redeploy an
existing tag instead of rebuilding — a rollback).

### Architecture

```
Browser --443/80--> proxy (Caddy, only public service)
                       |-- handle_path /api/* --> api:8000   (prefix stripped)
                       `-- handle          --> frontend:3000
api --> oltp:5432 (compose network; existing container/volume, untouched)
```

- **TLS**: `SITE_ADDRESS` is a [nip.io](https://nip.io) hostname
  (`52-66-116-80.nip.io` — resolves to the box's own Elastic IP by
  construction), so Caddy obtains a real Let's Encrypt certificate
  automatically — no DNS to manage, no self-signed warning. It's a
  `deploy/.env` var, never hard-coded (`deploy/proxy/Caddyfile`).
- **Auth**: the whole site sits behind Caddy `basic_auth` (bcrypt hash) —
  production grievance data (`/history`, `/api/history`,
  `/api/grievance/{id}`) must not be openly public. One exemption:
  `/api/health` bypasses `basic_auth` (leaks nothing but
  `{"status":"ok","processor":"pipeline"}`) so `deploy/deploy.sh`'s own
  end-to-end check — and any external uptime monitor — can probe it
  unauthenticated. The credentials live in `deploy/proxy.env` (a *separate*
  file from `deploy/.env` — Compose interpolates `$` in `deploy/.env`
  values, which would mangle a bcrypt hash like `$2a$14$...`; `env_file`
  injects `proxy.env`'s contents verbatim, no interpolation, no escaping
  needed). `deploy/deploy.sh` fails closed if the hash isn't set to a
  real-looking value — compose itself has no default.
- **Models/data**: host bind-mounts (`../models`, `../data/interim`,
  `../data/raw/janasunani-mappings`, all `:ro`) — never baked into the `api`
  image. A new deploy doesn't re-pull model weights; a model update is a
  separate `dvc pull` on the box.
- **GHCR is private**: the box authenticates with a `read:packages`-scoped
  PAT (§"One-time box setup" below), not a public pull.

### One-time box setup (maintainer)

```bash
cd ~/janasunani && git fetch && git checkout deploy/cpu-box   # or whatever branch owns this stack
cd ~/janasunani
# Scoped pull — do NOT run a bare `dvc pull` (see docs/DEMO.md §1):
uv run dvc pull models/categorizer.dvc models/page_type_classifier/vit_type_classifier.dvc data/raw/janasunani-mappings.dvc
ls data/interim/*.parquet   # already on the box from §"Materialize" above; if missing, `uv run dvc pull data/interim`

# GHCR is private — authenticate with a read:packages PAT (GitHub → Settings
# → Developer settings → Personal access tokens; classic or fine-grained,
# read:packages only):
echo "<PAT>" | docker login ghcr.io -u <github-username> --password-stdin

cd ~/janasunani/deploy
cp .env.example .env && chmod 600 .env
# fill in: POSTGRES_PASSWORD (URL-safe — no ':' '@' '/' '?'; matches §2),
#          SITE_ADDRESS=52-66-116-80.nip.io,
#          IMAGE_TAG can stay blank (deploy.sh writes it on every deploy)

cp proxy.env.example proxy.env && chmod 600 proxy.env
# fill in DEMO_PASSWORD_HASH in proxy.env (NOT deploy/.env — Compose would
# mangle the '$' in a bcrypt hash pasted into deploy/.env; deploy.sh refuses
# to bring the full stack up without a real-looking hash here):
docker run --rm caddy:2-alpine caddy hash-password --plaintext '<a real password>'
```

Then the maintainer (not CI, not this file's author) creates a GitHub
Actions **environment** and provisions CI's AWS access and repo
secrets/vars — **run each of these yourself; nothing here does it for you:**

1. **GitHub → repo Settings → Environments → New environment**, name it
   exactly `box-deploy` (matches `environment: box-deploy` in
   `.github/workflows/deploy.yml` and the OIDC trust condition in
   `deploy/terraform/ci.tf`) — the `deploy` job cannot obtain AWS
   credentials without this existing. Optional but recommended: add yourself
   as a required reviewer on the environment so a live deploy needs a manual
   approval click.
2. Apply the CI IAM role/OIDC provider:
   ```bash
   cd deploy/terraform
   terraform plan     # scan for `N to destroy` on the EXISTING resources —
                       # ci.tf only ADDS an OIDC provider + IAM role; if you see
                       # any destroy/replace on aws_instance.cpu_box or
                       # aws_security_group.cpu_box, STOP, do not apply.
   terraform apply
   terraform output ci_deploy_role_arn cpu_box_security_group_id
   ```
3. Set these secrets/vars (secrets are repo-level; the vars below can be
   repo-level or scoped to the `box-deploy` environment):

| Secret / var | Where | Value |
|---|---|---|
| `DPIC_GITHUB_SSH_KEY` | secret | already exists (pipeline.yml reuses it) |
| `BOX_SSH_KEY` | secret | a **new, CI-only** SSH keypair's private half — generate with `ssh-keygen -t ed25519 -f ci-deploy-key -N ''`; put the **public** half in the box's `~/.ssh/authorized_keys` |
| `BOX_SSH_KNOWN_HOSTS` | secret | `ssh-keyscan 52.66.116.80` output |
| `BOX_HOST` | repo var | `52.66.116.80` |
| `CI_DEPLOY_ROLE_ARN` | repo var | `terraform output -raw ci_deploy_role_arn` |
| `BOX_SG_ID` | repo var | `terraform output -raw cpu_box_security_group_id` |

### Routine flow

**Actions → "Deploy demo" → Run workflow** (leave `image_tag` empty to build
from the current default branch, or set it to redeploy/roll back to an
existing SHA). The workflow: builds `api`+`frontend` for `linux/amd64` (the
box's arch — this can't be validated on an arm64 dev machine, see
"Known gaps" below), pushes both to GHCR, opens port 22 to the runner's own
IP on `aws_security_group.cpu_box` (via the OIDC-assumed `ci_deploy` role),
ships `docker-compose.yml` / `deploy.sh` / `proxy/Caddyfile` to
`~/janasunani/deploy/` over SCP, runs `deploy/deploy.sh` over SSH (which pulls
images, brings the stack up, and blocks until `/health` reports
`"processor":"pipeline"` through the proxy), then **always** revokes the
port-22 rule, success or failure.

**Rollback**: run the workflow again with `image_tag` set to a prior
`github.sha` that was previously deployed (GHCR keeps every pushed tag) — or
by hand on the box: `IMAGE_TAG=<sha> bash deploy/deploy.sh`.

### Hard rules specific to this path

- Port 22 is admin-only (`var.admin_cidr`, §1) **except** during a running
  deploy job, when CI's own IP is temporarily authorized and then revoked
  (`if: always()`) — never widen the baseline security group rule itself.
- `deploy/deploy.sh` never runs `docker compose down` (let alone `-v`) — see
  §7 below.
- Never point pytest at the box's Postgres (§7) — this path doesn't change
  that; CI's own test job runs against a throwaway Postgres, never the box.
- Disk hygiene: `deploy.sh` runs `docker image prune -f` after a successful
  deploy; if disk pressure still builds up (many SHAs pushed over time),
  `docker image prune -af --filter until=720h` clears anything untagged and
  unused for 30+ days.

### Known gaps — what this repo's automation does NOT verify for you

- **The `linux/amd64` `api` build itself** — it resolves ~8–12 GB of CUDA
  torch and can only be built for real on an amd64 runner (GitHub Actions),
  never on an arm64 dev Mac. Review the Dockerfile carefully; the first real
  signal is the CI build log / GHCR push.
- **On-box browser E2E** — submit a grievance → real pipeline output renders
  and persists to `live_grievances` → `/history` shows it → `basic_auth`
  actually gates access. Do this once after the first automated deploy.

## 5 · Backups (nightly `pg_dump`)

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

## 6 · GPU box (on demand)

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

## 7 · Lifecycle & cost control

```bash
# pause the CPU box (keeps the EIP address and the EBS/volume data):
aws ec2 stop-instances  --instance-ids $(terraform output -raw instance_id)
aws ec2 start-instances --instance-ids $(terraform output -raw instance_id)
```
The GPU box is create/destroy (toggle `gpu_box_count`), never stop/start.

## 8 · Hard rules (violating these loses production data or leaks PII)

1. **Never `docker compose down -v`** on the CPU box — the external volume
   `janasunani-oltp` holds the migrated 1.37M/6.56M-row production data.
   `deploy/deploy.sh` (§4) never runs `down` at all.
2. **Never point pytest at the box's Postgres** — test fixtures DROP TABLES.
   Tests use a throwaway Postgres on `127.0.0.1:5433` only.
3. **Push GPU-box outputs before teardown** — its root volume dies with it.
4. Terraform **state / tfvars / keys stay local** (gitignored; pre-commit + CI
   guards enforce). The Postgres password lives only in the box's chmod-600
   `deploy/.env`.
5. S3 access is via the **instance role** — no static keys on the boxes.
6. **Port 22 stays admin-only** (`var.admin_cidr`) outside a running deploy
   job — CI's temporary widening (§4) is scoped to `ci_deploy`'s narrow IAM
   policy (`ec2:AuthorizeSecurityGroupIngress`/`RevokeSecurityGroupIngress` on
   `aws_security_group.cpu_box` only) and is always revoked, success or
   failure.

## Reference

**Terraform variables** ([variables.tf](../deploy/terraform/variables.tf)):
`aws_region` (ap-south-1), `admin_cidr` (no default — your IP/32),
`instance_type` (t3.xlarge), `root_volume_gb` (150), `gpu_box_count` (0),
`gpu_instance_type` (g6.xlarge), `gpu_availability_zone` (ap-south-1a),
`ssh_public_key_path` (`~/.ssh/id_ed25519.pub`), and the three bucket vars.
[ci.tf](../deploy/terraform/ci.tf) adds `create_github_oidc_provider` (default
`true` — see its comment on the one-per-account OIDC provider limit).

**Buckets:** documents `janasunani-documents-main` · DVC cache
`dpic-dvc-cache/janasunani` · DB backups `grievance-database-backups-main`.

**Secrets/vars for the automated deploy** (§4): see the table there —
`DPIC_GITHUB_SSH_KEY` (existing), `BOX_SSH_KEY` + `BOX_SSH_KNOWN_HOSTS`
(secrets), `BOX_HOST` + `CI_DEPLOY_ROLE_ARN` + `BOX_SG_ID` (repo vars).

**See also:** [deploy/terraform/README.md](../deploy/terraform/README.md) (IaC
detail), [deploy/README.md](../deploy/README.md) (compose detail),
[docs/ROADMAP.md](ROADMAP.md) (sequencing + project snapshot). The hard
safety rules live in §8 above.
