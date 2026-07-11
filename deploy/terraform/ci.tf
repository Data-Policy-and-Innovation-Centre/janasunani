# CI access for the automated demo deploy (.github/workflows/deploy.yml).
#
# The deploy job needs two things from AWS, both scoped as tightly as
# possible and both temporary/least-privilege:
#   1. A federated identity (GitHub OIDC) so the workflow can assume an AWS
#      role with no long-lived AWS access key stored as a GitHub secret.
#   2. Permission to open port 22 to the runner's own IP for the duration of
#      the deploy job only, then revoke it (an `if: always()` step in the
#      workflow) — the box's baseline security group (main.tf) keeps SSH
#      locked to `var.admin_cidr` outside that window.
#
# This does NOT touch aws_instance.cpu_box, its user_data, or the baseline
# ingress rules in main.tf.

data "aws_caller_identity" "current" {}

# --- GitHub Actions OIDC provider --------------------------------------------
#
# AWS allows AT MOST ONE OIDC provider per account for a given issuer URL.
# Before `terraform apply`, run:
#   aws iam list-open-id-connect-providers
# If an entry for token.actions.githubusercontent.com already exists (e.g.
# created by another DPIC project's CI), set create_github_oidc_provider =
# false in terraform.tfvars — Terraform will then reference the existing
# provider via a data source instead of creating a duplicate (which errors).
variable "create_github_oidc_provider" {
  description = "Create the token.actions.githubusercontent.com OIDC provider. Set false (and rely on the data-source fallback below) if `aws iam list-open-id-connect-providers` shows it already exists for this account."
  type        = bool
  default     = true
}

# SHA-1 fingerprint of the root CA currently in
# token.actions.githubusercontent.com's TLS chain (GitHub's OIDC issuer sits
# behind Let's Encrypt; fetched via
# `openssl s_client -connect token.actions.githubusercontent.com:443 -showcerts`
# and confirmed against the root cert with `openssl x509 -noout -fingerprint
# -sha1`). AWS has documented that it no longer actually validates this field
# against the live chain for GitHub's provider specifically — IAM just
# requires a syntactically valid (40 hex chars) value — but keeping it a real
# fingerprint rather than an arbitrary placeholder costs nothing. Recompute
# with the command above if IAM ever starts rejecting it. Avoids pulling in
# the `tls` provider (unused elsewhere in this project) just for this.
locals {
  github_actions_oidc_thumbprint = "ab9d0263244dd0326eb67015705a667e79cfe998"
}

resource "aws_iam_openid_connect_provider" "github_actions" {
  count           = var.create_github_oidc_provider ? 1 : 0
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [local.github_actions_oidc_thumbprint]
}

data "aws_iam_openid_connect_provider" "github_actions" {
  count = var.create_github_oidc_provider ? 0 : 1
  url   = "https://token.actions.githubusercontent.com"
}

locals {
  github_oidc_provider_arn = (
    var.create_github_oidc_provider
    ? aws_iam_openid_connect_provider.github_actions[0].arn
    : data.aws_iam_openid_connect_provider.github_actions[0].arn
  )
}

# --- CI deploy role ------------------------------------------------------

resource "aws_iam_role" "ci_deploy" {
  name = "janasunani-ci-deploy"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = local.github_oidc_provider_arn }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          # Any ref/event on this repo — workflow_dispatch is the only
          # trigger (deploy.yml), but the sub claim varies by ref, so this
          # stays a wildcard on the repo rather than pinning a branch.
          "token.actions.githubusercontent.com:sub" = "repo:Data-Policy-and-Innovation-Centre/janasunani:*"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "ci_deploy_sg" {
  name = "janasunani-ci-deploy-sg-ingress"
  role = aws_iam_role.ci_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "TempSshIngressOnCpuBoxSgOnly"
        Effect = "Allow"
        Action = [
          "ec2:AuthorizeSecurityGroupIngress",
          "ec2:RevokeSecurityGroupIngress",
        ]
        Resource = "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:security-group/${aws_security_group.cpu_box.id}"
      },
      {
        # DescribeSecurityGroups has no resource-level permissions in IAM —
        # must be "*" (the authorize/revoke actions above are the ones that
        # actually change anything, and those stay scoped).
        Sid      = "DescribeForTheAuthorizeRevokeCalls"
        Effect   = "Allow"
        Action   = ["ec2:DescribeSecurityGroups"]
        Resource = "*"
      },
    ]
  })
}
