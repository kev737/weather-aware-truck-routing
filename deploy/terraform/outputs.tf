output "url" {
  value = "https://${local.site}"
}

output "instance_id" {
  value = aws_instance.web.id
}

output "public_ip" {
  value = aws_eip.web.public_ip
}
