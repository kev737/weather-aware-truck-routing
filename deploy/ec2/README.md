# EC2 deployment

One `t4g.micro` (Amazon Linux 2023, ARM) in us-west-2 with an Elastic IP. `user-data.sh` runs on
first boot: 2 GB swap, Docker, a clone of this repo, a build of the root `Dockerfile`, and Caddy in
front for automatic HTTPS on `<elastic-ip-with-dashes>.sslip.io`. Only ports 80 and 443 are open; the
instance is managed through AWS Systems Manager (no SSH keys).

Why EC2 and not a shared PaaS: Open-Meteo's free tier is limited per IP address, and shared hosting
IPs (tested on Render) are already over that limit. A dedicated IP keeps the weather fast and free.

Redeploy after pushing to `main`:

```bash
aws ssm send-command --instance-ids <instance-id> --document-name AWS-RunShellScript \
  --parameters 'commands=["/opt/deploy.sh"]'
```
