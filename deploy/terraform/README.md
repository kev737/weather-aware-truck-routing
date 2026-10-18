# Infrastructure (Terraform)

Everything the app runs on in AWS: the EC2 instance, its Elastic IP, the security group
(80/443 only) and the IAM role and instance profile (SSM access plus reading one parameter).
The server's software is set up by `../ec2/user-data.sh` on first boot and `../ec2/deploy.sh`
on each deploy.

```bash
cd deploy/terraform
export AWS_PROFILE=<your profile>
terraform init
terraform plan        # should say "No changes" for the running server
terraform apply       # after editing the .tf files
terraform destroy     # removes everything, including the Elastic IP (the site URL changes)
```

- **State is local** (`terraform.tfstate`, git-ignored). If it's lost, `imports.tf` re-adopts
  the running resources on the next `plan`/`apply`. In a new account, delete `imports.tf` and
  `apply` creates everything from scratch.
- **Editing the boot script or a newer AMI doesn't replace the server** (`ignore_changes`).
  To rebuild it on purpose: `terraform apply -replace=aws_instance.web`.
- **Not managed here:** the optional paid weather key. Store it by hand as the SecureString
  `/truck-routing/open-meteo-api-key` so it never appears in git or Terraform state.
