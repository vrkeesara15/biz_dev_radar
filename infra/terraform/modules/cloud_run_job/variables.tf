variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "name_prefix" {
  type = string
}

variable "jobs" {
  description = <<-EOT
    Job name (RFC1035) -> { args, optional timeout_seconds, max_retries, cpu, memory }.
    Built from infra/cloudrun/adapters.auto.tfvars.json plus migrate and smoke.
  EOT
  type        = any
}

variable "image" {
  type = string
}

variable "service_account" {
  type = string
}

variable "env" {
  type    = map(string)
  default = {}
}

variable "secret_env" {
  type    = map(string)
  default = {}
}

variable "cloudsql_instance" {
  type    = string
  default = ""
}

variable "vpc_connector" {
  type    = string
  default = ""
}

variable "default_timeout_seconds" {
  type    = number
  default = 3600
}

variable "default_max_retries" {
  type    = number
  default = 1
}

variable "default_cpu" {
  type    = string
  default = "1"
}

variable "default_memory" {
  type    = string
  default = "2Gi"
}

variable "labels" {
  type    = map(string)
  default = {}
}
