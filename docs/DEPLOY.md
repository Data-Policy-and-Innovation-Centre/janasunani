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
> (VPN on/off, ISP), and the failure looks like a dead box: a TCP timeout
> before any handshake. Use plain **`curl -4`** — bare curl may return IPv6,
> which won't match the /32.
>
> **Do not reach for `terraform apply` first.** An apply during a running
> `Deploy demo` job reconciles away CI's temporary runner rule and severs the
> SSH session executing `deploy.sh`. Follow the add/verify/revoke procedure in
> [deploy/terraform/README.md](../deploy/terraform/README.md#when-ssh-to-the-box-times-out),
> which also covers revoking the stale rule from **every** group — leaving it
> means whoever next receives that address has SSH access to citizen data.

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
  values, which would mangle a bcrypt hash like `$2a$14$...`). The `proxy`
  service loads it via the long-form `env_file:` with `format: raw` (no
  interpolation of `$`, version-independent — the `format` key on env_file
  entries needs **Compose >= 2.30.0** specifically (not 2.24 — that's only
  when `required: false` landed); on an older Compose the whole file fails
  to *parse*, before any service starts. `deploy/terraform/user_data.sh`
  installs `docker-compose-plugin` from Docker's official apt repo, which
  tracks current stable releases, so this is satisfied on a
  freshly-provisioned box — `deploy/deploy.sh` also preflights the
  installed version and fails with a clear message if it's too old, rather
  than letting compose's opaque parse error be the first sign) and
  `required: false` (so a bare `docker compose up -d oltp`, §2, doesn't
  fail just because `proxy.env` doesn't exist yet). `deploy/deploy.sh`
  fails closed if the hash isn't set to a real-looking value before
  bringing the full stack up — compose itself has no default and,
  deliberately, no required-var gate on it either (see
  docker-compose.yml's header comment).
- **Models/data**: host bind-mounts (`../models`, `../data/interim`,
  `../data/raw/janasunani-mappings`, all `:ro`) — never baked into the `api`
  image. A new deploy doesn't re-pull model weights. Approved releases are
  materialized separately into `models/releases` and activated atomically;
  legacy DVC mirrors remain the final local fallback. Serving never resolves an
  MLflow alias or downloads public model weights.
- **GHCR is private**: the box authenticates with a `read:packages`-scoped
  PAT (§"One-time box setup" below), not a public pull.
- **Reproducibility**: `api`/`frontend` are pinned to the full 40-char
  `IMAGE_TAG` (never `latest`); every OTHER base image (`caddy:2-alpine` in
  `docker-compose.yml`, `python:3.13-slim` and `ghcr.io/astral-sh/uv:0.9` in
  `deploy/api.Dockerfile`, `node:22-alpine` in `frontend/Dockerfile`) is
  pinned to a resolved `@sha256:...` digest, not just a floating tag — a
  base image re-pulling something different underneath an otherwise-
  unchanged Dockerfile is exactly the drift IMAGE_TAG pinning exists to
  prevent. Bump a digest by re-resolving:
  `docker buildx imagetools inspect <image>:<tag>`.

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

**Materialize the reviewed model release before the first strict deploy.** Copy
[`model-release.example.json`](../deploy/model-release.example.json) to a
protected operator file and replace every placeholder; never run the example
unchanged.

```bash
cd ~/janasunani
uv run janasunani-model-release materialize \
  --spec <approved-release.json> \
  --release-root models/releases \
  --activate
```

When a spec uses a registry alias, MLflow is used only by this pre-deploy
control-plane command. Roll back by
activating a previously materialized manifest after its checksums validate:

```bash
uv run janasunani-model-release activate \
  models/releases/<old-release>/release-manifest.json
```

The full manifest contract is in [MODELS.md](MODELS.md). **Then verify the box
before the first deploy**, with `--strict`:

```bash
cd ~/janasunani
OLTP_DB_URL="postgresql+asyncpg://postgres:<PW>@127.0.0.1:5432/janasunani" \
  uv run --extra demo janasunani-demo-preflight --strict
```

Host-visible, not the compose-internal DSN: this command runs on the host,
not inside a container, so it needs the same `127.0.0.1:5432` form §2 used
for `alembic upgrade head` above — not the `oltp:5432` service hostname
`deploy/docker-compose.yml` gives the `api` container, which the compose
network resolves and the host does not. `<PW>` is the same
`POSTGRES_PASSWORD` from `deploy/.env`.

Loads no model weights, so it answers in milliseconds — except the `oltp
store` check as of #88, which opens a real, timeout-bounded connection when
OLTP_DB_URL is set, so a wrong password/host/port here fails this command
directly rather than passing quietly. Without `--strict` three checks report
`WARN` rather than failing, because each one leaves the demo *running* —
which is exactly why they are easy to miss. The current advisory set is:

| Check | What you get if it is not OK |
|---|---|
| `routing mappings` | every response carries `method:"fallback"`; departments are illustrative, not real |
| `model release` | no approved active manifest, checksum drift, or an operator override shadowing pinned bytes |
| `router` | requested incidence artifact unavailable; safe crosswalk/mapping fallback remains |
| `triage` | requested actionability artifact unavailable; bounded/off fallback is reported |
| `history lake` | `/history` returns an empty page, indistinguishable from "no results" |
| `oltp store` | `InMemoryResultStore`: submissions return 201 and vanish on restart |

`--strict` makes every advisory warning fatal. Use it here and after any model
release activation or DVC pull, so a
box that is merely *up* is not mistaken for a box that is *ready*. Local
`make up` deliberately runs without it.

Then the maintainer (not CI, not this file's author) creates a GitHub
Actions **environment** and provisions CI's AWS access and repo
secrets/vars — **run each of these yourself; nothing here does it for you:**

1. **GitHub → repo Settings → Environments → New environment**, name it
   exactly `box-deploy` (matches `environment: box-deploy` in
   `.github/workflows/deploy.yml` and the OIDC trust condition in
   `deploy/terraform/ci.tf`) — the `deploy` job cannot obtain AWS
   credentials without this existing, and (per step 3 below) can't read the
   box-shell-granting secrets/vars without it either. **Strongly
   recommended**: add yourself as a **required reviewer** on the
   environment (a live deploy — SSH to the box holding production PII —
   then needs a manual approval click) and, if this repo ever gains other
   contributors, restrict which branches can deploy to it (Environment
   protection rules → "Deployment branches").
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
3. Set these secrets/vars — **all five box-shell/box-address ones below go
   on the `box-deploy` environment specifically** (repo Settings →
   Environments → `box-deploy` → Environment secrets / Environment
   variables), **not** repo-level. A repo-level secret is readable by any
   workflow any repo writer can add or modify — for `BOX_SSH_KEY` in
   particular that's a bypass of both the OIDC narrowing (ci.tf's `sub`
   condition) and the environment's required-reviewer gate, handing out SSH
   to the box that holds the migrated production grievance data. Only
   `DPIC_GITHUB_SSH_KEY` stays a repo secret — it's a read-only deploy key
   against `dpic-org` (used by the build jobs, which don't declare
   `environment: box-deploy` and so can't see environment secrets anyway),
   not something that grants access to the box itself.

| Secret / var | Where | Value |
|---|---|---|
| `DPIC_GITHUB_SSH_KEY` | **repo** secret | already exists (pipeline.yml reuses it) |
| `BOX_SSH_KEY` | **`box-deploy` environment** secret | a **new, CI-only** SSH keypair's private half — generate with `ssh-keygen -t ed25519 -f ci-deploy-key -N ''`; put the **public** half in the box's `~/.ssh/authorized_keys` |
| `BOX_SSH_KNOWN_HOSTS` | **`box-deploy` environment** secret | `ssh-keyscan 52.66.116.80` output |
| `BOX_HOST` | **`box-deploy` environment** var | `52.66.116.80` |
| `CI_DEPLOY_ROLE_ARN` | **`box-deploy` environment** var | `terraform output -raw ci_deploy_role_arn` |
| `BOX_SG_ID` | **`box-deploy` environment** var | `terraform output -raw cpu_box_security_group_id` |

The `deploy` job already declares `environment: box-deploy` (added for the
OIDC trust narrowing — see ci.tf's comment), which is exactly what makes it
able to read environment-scoped secrets/vars; `build-api`/`build-frontend`
deliberately do *not* declare it, so they can't.

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
by hand on the box: `IMAGE_TAG=<sha> bash deploy/deploy.sh`. In the common
case you don't have to do this yourself: `deploy.sh` rolls back
**automatically** when a deploy fails after it's already started replacing
the running containers — see "Automatic rollback" below.

### Automatic rollback

`docker compose up -d` replaces the previously-running (working) containers
with the new candidate immediately, before health is verified — so a bare
"exit on failure" would leave the public demo down on a broken candidate.
Instead, once `up -d` has run, every subsequent failure (the `up -d` command
itself partially failing, the Caddy reload, either health wait, or the final
smoke check) routes through a rollback:

1. Restores `deploy/proxy/Caddyfile.deployed` — a snapshot of the last
   Caddyfile that was actually part of a successful deploy — over the live
   `deploy/proxy/Caddyfile` and reloads Caddy. (Independent of the image-tag
   rollback below: redeploying the *same* `IMAGE_TAG` specifically to ship a
   Caddyfile change can fail this way too.)
2. If the tag actually changed, re-`docker compose up -d`s with `IMAGE_TAG`
   set back to the last known-good value recorded in `deploy/.env`.
3. **Re-verifies** the rollback with the same health wait used for a forward
   deploy — it does **not** just assume the previous image comes back up.
   If the previous image *also* fails to come up healthy (see "Migration
   policy" below for the main way this happens), it prints a loud
   `ROLLBACK FAILED` and says the demo is down, rather than a false "rolled
   back" success.

If there's no prior known-good tag (a first-ever deploy), rollback is
skipped and the script just fails — there's nothing to roll back to.

### Migration policy

A rollback here re-deploys the **previous image, unchanged** — `deploy.sh`
never runs `alembic downgrade` (the old image doesn't have the new revision
file to downgrade *from*). That means every schema migration shipped
through this pipeline must be **expand-only / backward-compatible**: the OLD
code has to still be able to boot and run correctly against the NEW schema.

- Fine: add a nullable column, add a new table, add an index.
- **Not fine in the same deploy**: rename or drop a column/table, narrow a
  type, add a `NOT NULL` without a default — anything the old code's queries
  would choke on.
- If you need one of the "not fine" changes, split it: an **expand** deploy
  first (old code ignores the new column/table; ships and bakes for a
  while), then a separate **contract** deploy later, once rolling back past
  the expand step is no longer a realistic need.

Violate this and a rollback can't un-migrate — the "rolled back" api
container's `alembic upgrade head` (`deploy/api-entrypoint.sh`) either
errors immediately (`Can't locate revision`, if the old image predates a
revision the DB is already at) or runs but then crash-loops on a query the
old code can't form against the new shape. `deploy.sh`'s rollback health
re-check (above) will catch this and say so loudly — but only the migration
policy itself prevents it.

### Hard rules specific to this path

- Port 22 is admin-only (`var.admin_cidr`, §1) **except** during a running
  deploy job, when CI's own IP is temporarily authorized and then revoked
  (`if: always()`) — never widen the baseline security group rule itself.
- `deploy/deploy.sh` never runs `docker compose down` (let alone `-v`) — see
  §7 below.
- Never point pytest at the box's Postgres (§7) — this path doesn't change
  that; CI's own test job runs against a throwaway Postgres, never the box.
- A `flock` on `deploy/.deploy.lock` (gitignored, box-local) guards against a
  hand-run `deploy.sh` interleaving with a CI-triggered one — the workflow's
  own `concurrency:` group only serializes CI runs against each other, not
  against someone running the script by hand on the box. A second run
  blocks (doesn't error) until the first finishes.
- **Disk hygiene**: this root volume also holds prod Postgres, models, the
  Parquet lake, the HF cache, and the nightly `pg_dump` target — a
  disk-full here risks all of those, not just the deploy. `deploy.sh`
  refuses to even start pulling images if free space drops below ~20 GiB
  (deliberately several multiples of one api image; the threshold is kept
  where it was after the CPU-torch switch shrank the image, since the point
  is headroom for prod Postgres and the lake, not a tight fit), and after a
  successful
  deploy prunes every SHA-tagged `janasunani-api`/`janasunani-frontend`
  image except the current and previous (the rollback target) — plus the
  usual `docker image prune -f` for dangling layers. If disk pressure still
  builds up, `docker system df` shows where; `docker image prune -af
  --filter until=720h` is the manual backstop.

### Known gaps — what this repo's automation does NOT verify for you

- **The `linux/amd64` `api` build itself** — it can only be built for real on
  an amd64 runner (GitHub Actions), never on an arm64 dev Mac. Review the
  Dockerfile carefully; the first real signal is the CI build log / GHCR push.
  This is also where the CPU-torch switch (#48) gets confirmed: on darwin the
  `demo` extra still resolves the ordinary PyPI wheel, so the image-size drop
  is not observable locally. In the build log, expect
  `torch-2.12.1+cpu-...manylinux_2_28_x86_64.whl`, and **exactly one**
  `nvidia-*` download — `nvidia-nccl-cu12`, which xgboost declares
  unconditionally on linux (~300 MB, tracked separately). Any other `nvidia-*`
  or `cuda-*` wheel means the CPU source did not take effect and the image is
  still carrying the CUDA runtime.
- **On-box browser E2E** — submit a grievance → real pipeline output renders
  and persists to `live_grievances` → `/history` shows it → `basic_auth`
  actually gates access. Do this once after the first automated deploy.

### Known operational follow-ups

Not implemented yet — tracked here so they aren't forgotten, not because
they're low-stakes:

- **Workflow timeout vs. deploy.sh's own runtime.** The `deploy` job's
  `timeout-minutes: 45` (and the underlying SSH session) can be shorter than
  a slow pull plus up to two 1800s (30 min) health waits plus a rollback
  attempt — if the job/SSH connection is killed mid-`deploy.sh`, the script
  dies with it, **skipping the rollback it would otherwise have run**.
  Future: run `deploy.sh` detached on the box (`setsid`/`nohup`, or `trap ''
  HUP`) and have the workflow poll an exit-status file instead of holding
  the SSH session open for the whole run; raise the job timeout accordingly.
- **Rollback ref/artifact skew.** A rollback dispatched from a newer ref
  ships that ref's *current* `docker-compose.yml`/`Caddyfile` alongside the
  *old* image being rolled back to — if those files changed shape between
  the two commits (a new compose key the old image's entrypoint doesn't
  expect, a Caddyfile routing change the old api doesn't serve), the
  combination was never actually tested together. Future: dispatch a
  rollback from the ref matching `image_tag` (checked-out branch/tag
  matches), or have the workflow `git checkout` `inputs.image_tag` before
  the artifact-shipping step so the shipped files match the image being
  deployed.
- **Security-group ingress rule leakage/collision.** The temporary port-22
  `/32` rule (`.github/workflows/deploy.yml`) can leak if the runner dies
  between the authorize and revoke steps (rare, but `if: always()` doesn't
  help if the whole VM is killed), and a `terraform apply` mid-deploy would
  reset `aws_security_group.cpu_box` to its baseline rules, stripping CI's
  temporary one out from under a running deploy. Future: reconcile
  (list-and-revoke) stale CI-added rules before authorizing a new one each
  run, or move off SG-based ingress entirely to AWS SSM Session Manager
  (no inbound port needed at all).

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
`DPIC_GITHUB_SSH_KEY` (existing, repo secret) vs. `BOX_SSH_KEY` +
`BOX_SSH_KNOWN_HOSTS` + `BOX_HOST` + `CI_DEPLOY_ROLE_ARN` + `BOX_SG_ID`
(**`box-deploy` environment** secrets/vars, not repo-level — see §4 for why
that split matters).

**See also:** [deploy/terraform/README.md](../deploy/terraform/README.md) (IaC
detail), [deploy/README.md](../deploy/README.md) (compose detail),
[docs/ROADMAP.md](ROADMAP.md) (sequencing + project snapshot). The hard
safety rules live in §8 above.
