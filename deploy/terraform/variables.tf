variable "aws_region" {
  description = "AWS region (matches config.py AWS_REGION and the DVC remote)."
  type        = string
  default     = "ap-south-1"
}

variable "admin_cidr" {
  description = "IPv4 CIDR allowed to SSH (your IP /32 — \"$(curl -4 -s ifconfig.me)/32\"). No default on purpose."
  type        = string

  validation {
    condition     = can(cidrnetmask(var.admin_cidr)) && var.admin_cidr != "0.0.0.0/0"
    error_message = "admin_cidr must be a valid IPv4 CIDR (use `curl -4 -s ifconfig.me`) and not 0.0.0.0/0 — SSH stays maintainer-only."
  }
}

variable "instance_type" {
  description = <<-EOT
    CPU box size. Default t3.xlarge (4 vCPU / 16 GB) for week 1, where the
    one-time migration runs MySQL + Postgres side by side; downsize to
    t3.large afterwards (stop instance -> change type -> start).
  EOT
  type        = string
  default     = "t3.xlarge"
}

variable "root_volume_gb" {
  description = "Root EBS (gp3) size. Holds the 3.2 GB dump, a ~15 GB restored MySQL scratch DB, the Postgres volume, and the Parquet lake."
  type        = number
  default     = 150
}

variable "gpu_box_count" {
  description = <<-EOT
    0 or 1. The GPU box exists only while a batch OCR run or demo window needs
    it: set to 1 + apply to create, back to 0 + apply to destroy. Default off
    so a plain `terraform apply` never starts GPU billing (~$1/hr).
  EOT
  type        = number
  default     = 0

  validation {
    condition     = var.gpu_box_count == 0 || var.gpu_box_count == 1
    error_message = "gpu_box_count must be 0 or 1 — the pipeline shards across boxes via --worker-id, but one L4 is enough for the sample backfill."
  }
}

variable "gpu_instance_type" {
  description = "GPU box size. g6.xlarge = 4 vCPU / 16 GB / L4 24 GB — fits DeepSeek-OCR (~7 GB bf16) comfortably."
  type        = string
  default     = "g6.xlarge"
}

variable "gpu_availability_zone" {
  description = "AZ for the GPU box. g6 is offered in ap-south-1a/1b but NOT 1c (verified 2026-07-04)."
  type        = string
  default     = "ap-south-1a"
}

variable "gpu_root_volume_gb" {
  description = "GPU box root EBS (gp3). DLAMI snapshot is 75 GB; extra covers model weights, uv envs, HF cache, document sample."
  type        = number
  default     = 100
}

variable "ssh_public_key_path" {
  description = "Local path to the SSH public key installed on the box."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "documents_bucket" {
  description = "Existing S3 bucket for ingested documents (config.py AWS_S3_DOCUMENTS)."
  type        = string
  default     = "janasunani-documents-main"
}

variable "dvc_cache_bucket" {
  description = "Existing S3 bucket backing the DVC remote (.dvc/config)."
  type        = string
  default     = "dpic-dvc-cache"
}

variable "dvc_cache_prefix" {
  description = "This repo's prefix inside the shared DVC cache bucket (.dvc/config remote url path). IAM object access is scoped to it."
  type        = string
  default     = "janasunani"
}

variable "backups_bucket" {
  description = "Existing S3 bucket for nightly pg_dump snapshots."
  type        = string
  default     = "grievance-database-backups-main"
}
