# HTTP Basic authentication at the CloudFront edge, copied from
# ai-for-panchayats infra/terraform/app/auth.tf.
#
# It runs at viewer request, before the cache and before the box, so a
# request without the login never reaches the application.
#
# One shared credential is not identity: it cannot say who looked at what, and
# it cannot be withdrawn from one person without rotating it for everyone. It
# fits a small known group (the DPIC team and the Director Grievance, GAPG).
# Rotate with `terraform apply -replace=random_password.basic_auth`.

variable "basic_auth_username" {
  description = "Username for the site's shared login. The password is generated; read it with `terraform output -raw basic_auth_password`."
  type        = string
  default     = "dpic"
}

resource "random_password" "basic_auth" {
  length = 32
  # Typed by hand into a browser prompt and pasted into chat, where a stray
  # quote or backslash becomes a support request.
  special = false
}

# The credential is compiled into the function body: CloudFront Functions have
# no secret store. Anyone with cloudfront:GetFunction in this account can read
# it, which is a wider audience than the Terraform state.
resource "aws_cloudfront_function" "basic_auth" {
  name    = "janasunani-basic-auth"
  runtime = "cloudfront-js-2.0"
  comment = "Shared login for the janasunani site"
  publish = true

  code = templatefile("${path.module}/basic_auth.js.tftpl", {
    expected = "Basic ${base64encode("${var.basic_auth_username}:${random_password.basic_auth.result}")}"
    realm    = "Janasunani"
  })
}
