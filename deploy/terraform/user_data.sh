#!/usr/bin/env bash
# Cloud-init bootstrap for the Janasunani CPU box (Ubuntu 24.04).
# Installs: Docker (+ compose plugin), AWS CLI v2, uv. Idempotent-ish; runs
# once at first boot. Log: /var/log/cloud-init-output.log
set -euxo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git unzip

# --- Docker (official repo, includes the compose plugin) ---
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
usermod -aG docker ubuntu

# --- AWS CLI v2 (instance role supplies credentials) ---
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp
/tmp/aws/install
rm -rf /tmp/aws /tmp/awscliv2.zip

# --- uv, for the ubuntu user (runs the project CLIs: migrate, materialize, dvc) ---
sudo -u ubuntu bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'

echo "janasunani bootstrap complete"
