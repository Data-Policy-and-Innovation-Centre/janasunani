# CloudFront in front of the CPU box, with the site's one shared login checked
# at the edge (auth.tf). Same shape as ai-for-panchayats infra/terraform/app,
# with one difference: the origin is Caddy on the box, which already holds a
# Let's Encrypt certificate for its nip.io name, so the CloudFront-to-box hop
# is HTTPS rather than plain HTTP.
#
# Who this is for: the DPIC team and the Director Grievance, GAPG, all of whom
# may see raw complaints. One shared credential is not identity; see auth.tf.

locals {
  # 52.66.116.80 -> 52-66-116-80.nip.io. nip.io resolves it back to the
  # Elastic IP, and it is the name Caddy's certificate is issued for
  # (SITE_ADDRESS in deploy/.env).
  origin_domain = "${replace(aws_eip.cpu_box.public_ip, ".", "-")}.nip.io"
}

data "aws_ec2_managed_prefix_list" "cloudfront_origin_facing" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

# Everything the viewer sent except Host. CloudFront checks the origin's
# certificate against the Host it forwards, so forwarding the viewer's
# *.cloudfront.net Host would fail every request with a 502. Without it,
# CloudFront sends the nip.io name, which is also the site Caddy serves.
data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

# The box's address is public and port 443 admits every CloudFront
# distribution, including other accounts'. Caddy forwards only requests that
# carry this value, and only this distribution adds it. The value goes into
# deploy/proxy.env on the box as ORIGIN_VERIFY_SECRET.
resource "random_password" "origin_verify" {
  length  = 40
  special = false
}

resource "aws_cloudfront_distribution" "app" {
  enabled         = true
  is_ipv6_enabled = true
  comment         = "janasunani"

  # Includes India. PriceClass_100 would serve Odisha from Europe.
  price_class = "PriceClass_200"

  origin {
    domain_name = local.origin_domain
    origin_id   = "box"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]

      # 60 s is the default quota ceiling. POST /grievance runs OCR and every
      # model in one request, so a long document can pass it and the viewer
      # sees a 504 while the box finishes. Measure on the first deploy; raise
      # the quota (up to 180 s) or make submission asynchronous.
      origin_read_timeout      = 60
      origin_keepalive_timeout = 60
    }

    custom_header {
      name  = "X-Origin-Verify"
      value = random_password.origin_verify.result
    }
  }

  # No caching: this is an API and pages built per request.
  default_cache_behavior {
    target_origin_id       = "box"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]

    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.basic_auth.arn
    }
  }

  # Next.js fingerprints these by content, so caching them long is safe. The
  # login runs on this behavior too: it runs before the cache lookup, so a
  # cached bundle is still gated.
  ordered_cache_behavior {
    path_pattern           = "/_next/static/*"
    target_origin_id       = "box"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    cache_policy_id = data.aws_cloudfront_cache_policy.optimized.id

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.basic_auth.arn
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
