variable "region" {
  type    = string
  default = "us-west-2"
}

variable "availability_zone" {
  type    = string
  default = "us-west-2c"
}

variable "instance_type" {
  type    = string
  default = "t4g.micro" # ARM; the Vite build needs the swap that user-data.sh adds
}

variable "disk_gb" {
  type    = number
  default = 16 # Docker images + ~1.1 GB of forecast grids, with the 3 GB free-space guard
}
