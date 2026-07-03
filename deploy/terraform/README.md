# Janasunani demo infrastructure (Terraform)

The **always-on CPU box** for the demo stack (Week 1 of the roadmap): one Ubuntu 24.04
EC2 instance + Elastic IP + security group + an IAM instance role scoped to the three
existing S3 buckets (documents, DVC cache, DB backups). The buckets themselves are
**not** managed here. The on-demand GPU box arrives in Week 2 as a separate instance.

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
