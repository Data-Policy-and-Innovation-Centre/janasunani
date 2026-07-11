# Janasunani demo infrastructure (Terraform)

> ## ⚠️ The CPU box already exists and is always-on — do **not** blindly `terraform apply`
>
> The production CPU box (`i-0ef24e15a80ba7128`, EIP `52.66.116.80`) is **already
> running and holds the migrated 1.37M/6.56M-row production data** on its root
> volume. **`terraform apply` is NOT a safe no-op.** The Ubuntu AMI uses
> `most_recent = true` ([main.tf](main.tf)), so a newer Canonical image drifts the
> `ami` attribute and Terraform plans to **destroy and recreate** the box — the
> root volume is `delete_on_termination = true`, so a recreate **erases the
> production data** (a subnet-ordering change can force the same). The instance
> now carries a `lifecycle` guard (`prevent_destroy = true`, `ignore_changes =
> [ami, subnet_id]`), so `apply` will **error out rather than replace it** — but
> don't rely on that as your only check.
>
> **Always `terraform plan` first** and scan for `aws_instance.cpu_box must be
> replaced` or any `N to destroy` — if you see it, **STOP, do not apply.** The
> usage below is for a **first-time bring-up or deliberate rebuild**, not routine
> ops on the existing box.

Two instances, one always on:

- the **always-on CPU box** for the demo stack (Week 1 of the roadmap): Ubuntu 24.04
  EC2 instance + Elastic IP + security group + an IAM instance role scoped to the three
  existing S3 buckets (documents, DVC cache, DB backups). The buckets themselves are
  **not** managed here.
- the **on-demand GPU box** (`gpu.tf`) for DeepSeek OCR batch runs and demo windows —
  count-toggled, off by default, nothing stateful on it. See "GPU box" below.

Plus [`ci.tf`](ci.tf): a GitHub OIDC provider + `janasunani-ci-deploy` IAM role that
the automated deploy workflow (`.github/workflows/deploy.yml`) assumes to
temporarily open/close port 22 on `aws_security_group.cpu_box` for the runner's IP
only, during the deploy job. Doesn't touch the instance, its user_data, or the
baseline ingress rules above. See [docs/DEPLOY.md](../../docs/DEPLOY.md) §"Automated
demo deploy" for the full setup (repo secrets/vars, one-time box prerequisites).

## Usage

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars   # set admin_cidr to your IP/32
terraform init
terraform plan
terraform apply
```

Then, following `docs/ROADMAP.md` (Week 1). The repo **and** its `dpic` dependency
(pyproject.toml) are private, and the box holds no GitHub credential by design —
connect with **SSH agent forwarding** (`-A`) so the clone and `uv sync` authenticate
through your local key:

```bash
ssh-add --apple-use-keychain ~/.ssh/id_ed25519   # the local agent often starts empty
ssh -A ubuntu@$(terraform output -raw public_ip)
# on the box (bootstrap installs docker, aws cli, uv — check /var/log/cloud-init-output.log):
git clone git@github.com:Data-Policy-and-Innovation-Centre/janasunani.git && cd janasunani
uv sync
uv run dvc pull data/raw/Dump20250730.sql.dvc   # the 3.2 GB dump, via the instance role
# postgres up -> alembic upgrade head -> bash scripts/migrate.sh (OLTP_DB_URL=postgres)
```

Cost control: `aws ec2 stop-instances --instance-ids $(terraform output -raw instance_id)`
— the EIP keeps the address; EBS keeps the data. State is local (`terraform.tfstate`,
gitignored) — single maintainer; move to an S3 backend if that changes.

## GPU box

Created only when needed (~$1/hr while it exists — g6.xlarge, L4 24 GB). Built from
the AWS Deep Learning **Base** AMI, so the NVIDIA driver, Docker, and
nvidia-container-toolkit are preinstalled; the bootstrap adds git, poppler, and uv.
Same IAM role as the CPU box; SSH-only security group; ephemeral public IP (no EIP —
the box is create/destroy, not stop/start).

```bash
# up: set gpu_box_count = 1 in terraform.tfvars, then
terraform apply
ssh -A ubuntu@$(terraform output -raw gpu_box_ip)   # -A: private clone via your local key

# on the box:
git clone git@github.com:Data-Policy-and-Innovation-Centre/janasunani.git && cd janasunani
bash scripts/gpu_smoke.sh   # 2-file DeepSeek smoke: format stage (pipeline-core env)
                            # + OCR (ocr-deepseek env), report, exit 0/1

# down: push any outputs (dvc push / aws s3 cp) FIRST — the root volume dies with
# the instance. Then set gpu_box_count = 0 and
terraform apply
```

The two `uv run --extra ...` invocations in the smoke script are the deploy pattern
for the conflicting extras (`[tool.uv].conflicts` in pyproject.toml): one resolved
env per extra, same repo checkout, same SQLite artifact.
