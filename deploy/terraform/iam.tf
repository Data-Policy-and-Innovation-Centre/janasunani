# IAM instance role — the box's only credentials. Scoped to the three existing
# buckets (created outside Terraform; referenced by name, not managed here):
#   - janasunani-documents-main      ingested documents (s3service)
#   - dpic-dvc-cache                 DVC remote (raw dump, Parquet lake, models)
#   - grievance-database-backups-main  nightly pg_dump target (write-mostly)
# plus one that IS managed here, read-only:
#   - janasunani-documents-dsi-reference  benchmark corpus (reference_bucket.tf)

resource "aws_iam_role" "cpu_box" {
  name = "janasunani-cpu-box"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "s3_access" {
  name = "janasunani-s3-access"
  role = aws_iam_role.cpu_box.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ListProjectBuckets"
        Effect = "Allow"
        Action = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [
          "arn:aws:s3:::${var.documents_bucket}",
          "arn:aws:s3:::${var.backups_bucket}",
          "arn:aws:s3:::${var.dsi_reference_bucket}",
        ]
      },
      {
        # The DVC cache bucket is shared across DPIC projects — this role only
        # sees the repo's own prefix (matches the remote in .dvc/config).
        Sid      = "ListDvcCacheRepoPrefixOnly"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = ["arn:aws:s3:::${var.dvc_cache_bucket}"]
        Condition = {
          StringLike = { "s3:prefix" = ["${var.dvc_cache_prefix}/*", var.dvc_cache_prefix] }
        }
      },
      {
        Sid      = "GetDvcCacheBucketLocation"
        Effect   = "Allow"
        Action   = ["s3:GetBucketLocation"]
        Resource = ["arn:aws:s3:::${var.dvc_cache_bucket}"]
      },
      {
        Sid    = "ReadWriteDocuments"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = [
          "arn:aws:s3:::${var.documents_bucket}/*",
        ]
      },
      {
        # Read-only, and deliberately so. The reference corpus is the fixed
        # input every benchmark number is measured against (#319), so the box
        # that runs the benchmarks must not be able to alter what it is
        # measuring. Population happens once, from an operator workstation.
        Sid      = "ReadDsiReferenceCorpusOnly"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["arn:aws:s3:::${var.dsi_reference_bucket}/*"]
      },
      {
        Sid      = "ReadWriteDvcCacheRepoPrefixOnly"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = ["arn:aws:s3:::${var.dvc_cache_bucket}/${var.dvc_cache_prefix}/*"]
      },
      {
        Sid      = "WriteBackups"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = ["arn:aws:s3:::${var.backups_bucket}/*"]
      },
    ]
  })
}

resource "aws_iam_instance_profile" "cpu_box" {
  name = "janasunani-cpu-box"
  role = aws_iam_role.cpu_box.name
}
