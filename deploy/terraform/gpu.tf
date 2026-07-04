# The on-demand GPU box — DeepSeek OCR batch runs and demo windows only.
#
# Count-toggled: `gpu_box_count = 0` (the default) means no instance and no
# cost; set it to 1 in terraform.tfvars and `terraform apply` to bring the box
# up, back to 0 to destroy it. Nothing stateful lives here — pipeline outputs
# land in its SQLite artifact and are pushed to S3/DVC before teardown, so
# create/destroy is the normal workflow (an EIP would be pointless).
#
# AMI: AWS Deep Learning Base (OSS NVIDIA driver, Ubuntu 24.04) — driver,
# Docker, and nvidia-container-toolkit preinstalled, which is exactly the
# fresh-box pain we don't want to own. No conda/framework stack; the project
# env comes from uv (see gpu_user_data.sh).

data "aws_ami" "dlami_gpu" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 24.04)*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# g6 instances are not offered in every AZ (ap-south-1c lacks them), so the
# GPU box pins its subnet to an AZ that has them instead of taking the
# default-subnet list's first entry like the CPU box does.
data "aws_subnet" "gpu" {
  vpc_id            = data.aws_vpc.default.id
  availability_zone = var.gpu_availability_zone
  default_for_az    = true
}

resource "aws_security_group" "gpu_box" {
  name        = "janasunani-gpu-box"
  description = "Janasunani GPU box: SSH from the maintainer only, no inbound services"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "SSH from the maintainer IP only"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.admin_cidr]
  }

  egress {
    description = "All outbound (apt, pip, HF hub for the DeepSeek weights, S3)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Same role as the CPU box (S3: documents read/write, DVC cache prefix,
# backups write) — the GPU box needs a strict subset, and one role keeps the
# IAM surface small. Separate instance profile only because a profile attaches
# to one instance at a time per name.
resource "aws_iam_instance_profile" "gpu_box" {
  name = "janasunani-gpu-box"
  role = aws_iam_role.cpu_box.name
}

resource "aws_instance" "gpu_box" {
  count = var.gpu_box_count

  ami                    = data.aws_ami.dlami_gpu.id
  instance_type          = var.gpu_instance_type
  subnet_id              = data.aws_subnet.gpu.id
  vpc_security_group_ids = [aws_security_group.gpu_box.id]
  key_name               = aws_key_pair.maintainer.key_name
  iam_instance_profile   = aws_iam_instance_profile.gpu_box.name
  user_data              = file("${path.module}/gpu_user_data.sh")

  root_block_device {
    volume_type = "gp3"
    # DLAMI snapshot is 75 GB; headroom for the DeepSeek weights (~7 GB),
    # two uv envs, HF cache, and the document sample.
    volume_size           = var.gpu_root_volume_gb
    delete_on_termination = true
  }

  tags = {
    Name = "janasunani-gpu-box"
  }
}
