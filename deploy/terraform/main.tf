# The EC2 host that runs the app: one instance in the default VPC, an Elastic IP (the site
# name is that IP on sslip.io), a firewall open on 80/443 only, and a role for SSM access.
# No SSH: the instance is managed through AWS Systems Manager.

data "aws_caller_identity" "current" {}

data "aws_subnet" "default" {
  availability_zone = var.availability_zone
  default_for_az    = true
}

# Latest Amazon Linux 2023 for ARM; only used when the instance is (re)created.
data "aws_ssm_parameter" "al2023_arm" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

locals {
  name = "truck-routing"
  site = "${replace(aws_eip.web.public_ip, ".", "-")}.sslip.io"
  # Optional paid Open-Meteo key, stored by hand as a SecureString (never in git or state).
  open_meteo_key_param = "/truck-routing/open-meteo-api-key"
}

resource "aws_security_group" "web" {
  name        = "${local.name}-web"
  description = "Weather-aware truck routing: HTTP/HTTPS only"
  vpc_id      = data.aws_subnet.default.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "web" {
  for_each = {
    http_v4  = { port = 80, cidr_ipv4 = "0.0.0.0/0", cidr_ipv6 = null }
    http_v6  = { port = 80, cidr_ipv4 = null, cidr_ipv6 = "::/0" }
    https_v4 = { port = 443, cidr_ipv4 = "0.0.0.0/0", cidr_ipv6 = null }
    https_v6 = { port = 443, cidr_ipv4 = null, cidr_ipv6 = "::/0" }
  }
  security_group_id = aws_security_group.web.id
  ip_protocol       = "tcp"
  from_port         = each.value.port
  to_port           = each.value.port
  cidr_ipv4         = each.value.cidr_ipv4
  cidr_ipv6         = each.value.cidr_ipv6
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.web.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_iam_role" "ec2" {
  name = "${local.name}-ec2"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# deploy.sh reads the optional weather key on each deploy; the role may read that one parameter only.
resource "aws_iam_role_policy" "read_open_meteo_key" {
  name = "read-open-meteo-key"
  role = aws_iam_role.ec2.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.open_meteo_key_param}"
      },
      {
        Effect    = "Allow"
        Action    = "kms:Decrypt"
        Resource  = "*"
        Condition = { StringEquals = { "kms:ViaService" = "ssm.${var.region}.amazonaws.com" } }
      },
    ]
  })
}

resource "aws_iam_instance_profile" "ec2" {
  name = "${local.name}-ec2"
  role = aws_iam_role.ec2.name
}

resource "aws_eip" "web" {
  domain = "vpc"
  tags   = { Name = local.name }
}

resource "aws_instance" "web" {
  ami                    = data.aws_ssm_parameter.al2023_arm.value
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnet.default.id
  vpc_security_group_ids = [aws_security_group.web.id]
  iam_instance_profile   = aws_iam_instance_profile.ec2.name

  # First boot only: Docker, a clone of the repo, the app and Caddy (deploy/ec2/user-data.sh).
  user_data = replace(file("${path.module}/../ec2/user-data.sh"), "#!/bin/bash\n", "#!/bin/bash\nSITE=${local.site}\n")

  credit_specification {
    cpu_credits = "unlimited"
  }

  metadata_options {
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 2          # reachable from containers
    http_endpoint               = "enabled"
  }

  root_block_device {
    volume_size = var.disk_gb
    volume_type = "gp3"
  }

  tags = { Name = local.name }

  lifecycle {
    # A newer AMI or an edited boot script must not replace a running server; they apply
    # the next time it's recreated (terraform apply -replace=aws_instance.web).
    ignore_changes = [ami, user_data]
  }
}

resource "aws_eip_association" "web" {
  instance_id   = aws_instance.web.id
  allocation_id = aws_eip.web.id
}
