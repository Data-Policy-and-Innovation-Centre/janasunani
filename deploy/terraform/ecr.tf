# Container registry: ECR, the same pattern as ai-for-panchayats.
#
# CI pushes through its own OIDC role; the box pulls with its instance role.
# Neither side holds a registry password, and the images stay in the AWS
# account with the data.

locals {
  ecr_repositories = ["janasunani-api", "janasunani-frontend"]
  github_repo      = "Data-Policy-and-Innovation-Centre/janasunani"
}

resource "aws_ecr_repository" "app" {
  for_each = toset(local.ecr_repositories)

  name = each.key
  # A tag always means one image. Images are tagged with the commit SHA, and
  # the build jobs skip a tag that is already pushed.
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }
}

# BuildKit's layer cache for the api build. It rewrites one `api` tag on every
# build, which an immutable repository would refuse, so it lives apart. The box
# never pulls from it.
resource "aws_ecr_repository" "build_cache" {
  name                 = "janasunani-build-cache"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
}

# Only untagged images expire: an age rule over tagged ones would one day
# delete the image the box is running, and a rollback would have nothing to
# pull. Old SHA tags are removed deliberately, not on a timer.
resource "aws_ecr_lifecycle_policy" "app" {
  for_each   = merge(aws_ecr_repository.app, { build_cache = aws_ecr_repository.build_cache })
  repository = each.value.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire untagged images after 7 days"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 7
      }
      action = { type = "expire" }
    }]
  })
}

# --- CI push role -------------------------------------------------------------
#
# Assumed by build-api/build-frontend in .github/workflows/deploy.yml. Trust is
# pinned to the main branch: a workflow dispatched from any other ref cannot
# push an image the box would then run. The build jobs do not use the
# box-deploy environment, so pushing needs no approval; deploying still does.

resource "aws_iam_role" "ci_image_push" {
  name = "janasunani-ci-image-push"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = local.github_oidc_provider_arn }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${local.github_repo}:ref:refs/heads/main"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "ci_image_push" {
  name = "janasunani-ci-image-push"
  role = aws_iam_role.ci_image_push.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # No resource-level permission exists for this action.
        Sid      = "RegistryLogin"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        # Push, plus the reads buildx needs for the registry layer cache and
        # the "already pushed?" check.
        Sid    = "PushToTheseRepositoriesOnly"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:CompleteLayerUpload",
          "ecr:DescribeImages",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
        ]
        Resource = concat([for r in aws_ecr_repository.app : r.arn], [aws_ecr_repository.build_cache.arn])
      },
    ]
  })
}

# --- Deploy role: refuse a tag that was never pushed ---------------------------

resource "aws_iam_role_policy" "ci_deploy_describe_images" {
  name = "janasunani-ci-deploy-describe-images"
  role = aws_iam_role.ci_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "CheckTheTagExistsBeforeTouchingTheBox"
      Effect   = "Allow"
      Action   = ["ecr:DescribeImages"]
      Resource = [for r in aws_ecr_repository.app : r.arn]
    }]
  })
}

# --- Box: pull only ----------------------------------------------------------

resource "aws_iam_role_policy" "cpu_box_ecr_pull" {
  name = "janasunani-ecr-pull"
  role = aws_iam_role.cpu_box.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "RegistryLogin"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "PullFromTheseRepositoriesOnly"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
        Resource = [for r in aws_ecr_repository.app : r.arn]
      },
    ]
  })
}
