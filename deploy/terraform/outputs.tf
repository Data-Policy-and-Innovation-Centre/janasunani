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
