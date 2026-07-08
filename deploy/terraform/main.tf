# Janasunani demo infrastructure — the always-on CPU box.
#
# Minimal by design (see docs/ROADMAP.md "Cross-cutting — Infrastructure"):
# one EC2 instance running docker-compose (api + frontend + mlflow +
# oltp-postgres + proxy) plus the migration/materialization one-offs. S3 access
# via an IAM instance role — no static keys on the box. The on-demand GPU box
# is the separate, count-toggled instance in gpu.tf.
#
# State is local (terraform.tfstate, gitignored) — fine for a single
# maintainer; move to an S3 backend if a second operator ever appears.

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "janasunani"
      ManagedBy = "terraform"
    }
  }
}

# --- Networking: default VPC, public subnet ---------------------------------

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_security_group" "cpu_box" {
  name        = "janasunani-cpu-box"
  description = "Janasunani demo CPU box: SSH from the maintainer, HTTP/S from the world"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "SSH from the maintainer IP only"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.admin_cidr]
  }

  ingress {
    description = "HTTP (proxy)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS (proxy)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "All outbound (apt, pip, HF hub, S3, ...)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# --- Instance ----------------------------------------------------------------

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_key_pair" "maintainer" {
  key_name   = "janasunani-maintainer"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

resource "aws_instance" "cpu_box" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnets.default.ids[0]
  vpc_security_group_ids = [aws_security_group.cpu_box.id]
  key_name               = aws_key_pair.maintainer.key_name
  iam_instance_profile   = aws_iam_instance_profile.cpu_box.name
  user_data              = file("${path.module}/user_data.sh")

  root_block_device {
    volume_type = "gp3"
    volume_size = var.root_volume_gb
    # Everything stateful lives here: the Postgres named volume, the restored
    # MySQL scratch DB during migration, the dump, and the Parquet lake.
    delete_on_termination = true
  }

  lifecycle {
    # Hard backstop: Terraform errors instead of destroying/replacing the box.
    prevent_destroy = true
    # AMI drift (most_recent) and default-subnet-list reordering must not
    # force a replacement of the live, stateful box — keep whatever is
    # already running instead of chasing the data source.
    ignore_changes = [ami, subnet_id]
  }

  tags = {
    Name = "janasunani-cpu-box"
  }
}

# Stable public address so the demo URL survives instance stop/start.
resource "aws_eip" "cpu_box" {
  instance = aws_instance.cpu_box.id
  domain   = "vpc"

  tags = {
    Name = "janasunani-cpu-box"
  }
}
