# Janasunani demo infrastructure (Terraform)

Two instances, one always on:

- the **always-on CPU box** for the demo stack (Week 1 of the roadmap): Ubuntu 24.04
  EC2 instance + Elastic IP + security group + an IAM instance role scoped to the three
  existing S3 buckets (documents, DVC cache, DB backups). The buckets themselves are
  **not** managed here.
- the **on-demand GPU box** (`gpu.tf`) for DeepSeek OCR batch runs and demo windows —
  count-toggled, off by default, nothing stateful on it. See "GPU box" below.

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
