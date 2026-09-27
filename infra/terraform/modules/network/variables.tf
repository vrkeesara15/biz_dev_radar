variable "project_id" {
  description = "GCP project that owns the network."
  type        = string
}

variable "region" {
  description = "GCP region (us-east1 or asia-south1); the connector and NAT are regional."
  type        = string
}

variable "name_prefix" {
  description = "Resource name prefix, e.g. bidradar-dev."
  type        = string
}

variable "subnet_cidr" {
  description = "Primary subnet range."
  type        = string
  default     = "10.10.0.0/20"
}

variable "connector_cidr" {
  description = "Serverless VPC Access connector range; must be a free /28."
  type        = string
  default     = "10.10.240.0/28"
}

variable "private_service_prefix_length" {
  description = "Size of the range handed to Cloud SQL / Memorystore."
  type        = number
  default     = 20
}

variable "connector_min_instances" {
  type    = number
  default = 2
}

variable "connector_max_instances" {
  type    = number
  default = 3
}

variable "connector_machine_type" {
  type    = string
  default = "e2-micro"
}
