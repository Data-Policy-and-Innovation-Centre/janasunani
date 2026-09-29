# EC2 shape only: port 443 on the box admits CloudFront's origin-facing
# addresses and nothing else. Port 80 stays open to the world elsewhere, for
# Let's Encrypt's HTTP-01 renewal. Remove any existing 0.0.0.0/0 rule on 443,
# or this lock does nothing.
#
# A security group whose rules are inline ingress blocks must not also have
# standalone rule resources; Terraform would fight itself. If yours is inline,
# add this as an inline block instead.

variable "box_security_group_id" {
  description = "The box's security group."
  type        = string
}

resource "aws_vpc_security_group_ingress_rule" "https_from_cloudfront" {
  security_group_id = var.box_security_group_id
  description       = "HTTPS from CloudFront origin-facing addresses only"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront_origin_facing.id
}
