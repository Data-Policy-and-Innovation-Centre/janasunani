# Set these for the project. Every other template reads them.
locals {
  project     = "myapp"                                   # prefixes every AWS name
  github_repo = "Data-Policy-and-Innovation-Centre/myapp" # owner/name

  # The origin's public HTTPS name. For one EC2 box with an Elastic IP and no
  # domain: "${replace(aws_eip.box.public_ip, ".", "-")}.nip.io", which is also
  # the site address Caddy gets its Let's Encrypt certificate for.
  origin_domain = "203-0-113-10.nip.io"

  # Who the shared login is for. Recorded here so the next person sees it.
  audience = "the DPIC team and <named officials>"

  # Existing resources in the project's Terraform:
  runtime_role_name        = "myapp-box"       # the EC2 instance (or ECS execution) role that pulls images
  deploy_role_name         = "myapp-ci-deploy" # the OIDC role the deploy job assumes
  github_oidc_provider_arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"

  ecr_repositories = ["${local.project}-api", "${local.project}-frontend"]
}
