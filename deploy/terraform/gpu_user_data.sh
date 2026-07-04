#!/usr/bin/env bash
# Cloud-init bootstrap for the Janasunani GPU box (Deep Learning Base AMI,
# Ubuntu 24.04). The AMI already ships the NVIDIA driver, Docker,
# nvidia-container-toolkit, and the AWS CLI — this only adds what the project
# needs on top. Log: /var/log/cloud-init-output.log
set -euxo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git poppler-utils # poppler: pdf2image page rendering

# uv, for the ubuntu user (runs the pipeline CLIs; resolves the conflicting
# pipeline-core / ocr-deepseek extras into separate envs per `uv run --extra`)
sudo -u ubuntu bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'

# Sanity marker for the runbook: driver visible at first boot.
nvidia-smi --query-gpu=name,memory.total --format=csv || echo "WARN: nvidia-smi failed"

echo "janasunani gpu bootstrap complete"
