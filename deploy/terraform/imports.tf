# The server was first created with the AWS CLI; these blocks adopt those resources into
# Terraform state without changing them. Harmless once imported (Terraform skips them), and
# not needed for a fresh account: delete this file there.
import {
  to = aws_security_group.web
  id = "sg-015bc89eeac568c03"
}

import {
  to = aws_iam_role.ec2
  id = "truck-routing-ec2"
}

import {
  to = aws_iam_role_policy_attachment.ssm
  id = "truck-routing-ec2/arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

import {
  to = aws_iam_role_policy.read_open_meteo_key
  id = "truck-routing-ec2:read-open-meteo-key"
}

import {
  to = aws_iam_instance_profile.ec2
  id = "truck-routing-ec2"
}

import {
  to = aws_eip.web
  id = "eipalloc-0248ac86ff3074924"
}

import {
  to = aws_instance.web
  id = "i-058ef5ba25d61ed3e"
}

import {
  to = aws_eip_association.web
  id = "eipassoc-0f43dee2629029208"
}

import {
  to = aws_vpc_security_group_ingress_rule.web["http_v4"]
  id = "sgr-025d05ddd68283990"
}

import {
  to = aws_vpc_security_group_ingress_rule.web["http_v6"]
  id = "sgr-06cb9c0fc64a83385"
}

import {
  to = aws_vpc_security_group_ingress_rule.web["https_v4"]
  id = "sgr-0cee7d871065f5f06"
}

import {
  to = aws_vpc_security_group_ingress_rule.web["https_v6"]
  id = "sgr-0e4c56f30f3aee003"
}

import {
  to = aws_vpc_security_group_egress_rule.all
  id = "sgr-029e7875170243aa0"
}
