output "public_ip" {
  description = "Stable public IP of the CPU box (Elastic IP)."
  value       = aws_eip.cpu_box.public_ip
}

output "instance_id" {
  description = "EC2 instance ID (for aws ec2 stop-instances / start-instances)."
  value       = aws_instance.cpu_box.id
}

output "ssh_command" {
  description = "SSH into the box."
  value       = "ssh ubuntu@${aws_eip.cpu_box.public_ip}"
}

output "gpu_box_ip" {
  description = "Public IP of the GPU box (ephemeral — changes per create), or null when gpu_box_count = 0."
  value       = one(aws_instance.gpu_box[*].public_ip)
}

output "gpu_ssh_command" {
  description = "SSH into the GPU box with agent forwarding (for the private git clone), or null when off."
  value = (
    length(aws_instance.gpu_box) > 0
    ? "ssh -A ubuntu@${aws_instance.gpu_box[0].public_ip}"
    : null
  )
}

# --- CI deploy (ci.tf) -------------------------------------------------------

output "ci_deploy_role_arn" {
  description = "IAM role the deploy.yml workflow assumes via GitHub OIDC (repo var CI_DEPLOY_ROLE_ARN)."
  value       = aws_iam_role.ci_deploy.arn
}

output "cpu_box_security_group_id" {
  description = "CPU box security group ID (repo var BOX_SG_ID) — deploy.yml temporarily authorizes/revokes port 22 for the runner's IP here."
  value       = aws_security_group.cpu_box.id
}
