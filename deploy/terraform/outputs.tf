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
