# The DSI reference corpus bucket.
#
# Unlike the three buckets in iam.tf, this one IS managed here. The property
# that makes it worth having is that **no lifecycle rule ever transitions or
# expires an object in it**, and that guarantee is not something a
# hand-created bucket records anywhere. Declaring it in Terraform is what
# stops someone adding a transition rule later "for consistency" and silently
# re-archiving the corpus.
#
# Note the invariant is "no transition or expiration actions", not "no
# lifecycle rule". There is one, below, and it is the enforcement mechanism --
# see its own comment.
#
# Why it exists at all. janasunani-documents-main carries a lifecycle rule
# with an empty prefix -- STANDARD_IA at 30 days, GLACIER at 90 -- so every
# object in it ends up archived. Reading an archived object needs a restore,
# and a restore only stages a *temporary* copy: the storage class stays
# GLACIER and the copy expires. That is fine for ad-hoc sampling and wrong
# for a reference corpus that every future benchmark has to re-read.
#
# The alternatives were worse. Copying objects onto themselves as STANDARD
# resets the lifecycle clock, so they re-archive 90 days later, forever.
# Exempting them from the rule is not expressible: S3 lifecycle filters have
# no negation and the corpus shares no key prefix, so it would mean tagging
# the ~960,000 objects we *do* want archived and filtering on that -- where
# any future object arriving untagged silently never archives, a failure that
# surfaces as a bill months later.
#
# A separate bucket with no transition or expiration rule makes "stays
# readable" the default rather than something maintained. At 56 GB that is
# about $1.40/month.

resource "aws_s3_bucket" "dsi_reference" {
  bucket = var.dsi_reference_bucket

  # The corpus is the fixed input every model number is computed against
  # (#319). Losing it invalidates the comparisons, not just the bytes.
  lifecycle {
    prevent_destroy = true
  }

  # S3 tag values accept letters, digits, spaces and + - = . _ : / @ only.
  # A comma here fails CreateBucket with InvalidTag.
  tags = {
    Purpose = "DSI clinic reference corpus - fixed benchmark input - do not archive"
  }
}

# Declared rather than omitted, which is the opposite of what it looks like.
#
# An absent lifecycle configuration is not *managed* state. The AWS provider
# has nothing to compare against, so a transition rule added later through the
# console is invisible to `terraform plan` -- exactly the drift the header says
# this file exists to prevent. Leaving the resource out documents the intent
# and enforces nothing.
#
# Declaring it makes Terraform the owner: any out-of-band rule now shows up as
# drift and is removed on the next apply.
#
# The single rule is deliberately not a transition. Aborting abandoned
# multipart uploads is storage hygiene, not archival -- it matters while
# loading 56 GB and it never moves an object between storage classes. It is
# here because a lifecycle configuration must carry at least one valid action
# for the ownership to be declarable at all.
resource "aws_s3_bucket_lifecycle_configuration" "dsi_reference" {
  bucket = aws_s3_bucket.dsi_reference.id

  rule {
    id     = "abort-abandoned-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_versioning" "dsi_reference" {
  bucket = aws_s3_bucket.dsi_reference.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dsi_reference" {
  bucket = aws_s3_bucket.dsi_reference.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "dsi_reference" {
  bucket = aws_s3_bucket.dsi_reference.id

  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}
