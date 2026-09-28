# What the operator reads after apply. Verify in SKILL.md uses all three.

output "site_url" {
  description = "The site's address."
  value       = "https://${aws_cloudfront_distribution.app.domain_name}"
}

output "basic_auth_username" {
  value = var.basic_auth_username
}

output "basic_auth_password" {
  description = "The site's shared password. Share it only with local.audience."
  value       = random_password.basic_auth.result
  sensitive   = true
}

output "origin_verify_secret" {
  description = "ORIGIN_VERIFY_SECRET for deploy/proxy.env on the box (chmod 600)."
  value       = random_password.origin_verify.result
  sensitive   = true
}
