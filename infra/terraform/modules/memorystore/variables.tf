variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "name" {
  type = string
}

variable "network_id" {
  type = string
}

variable "tier" {
  description = "STANDARD_HA in production, BASIC in dev."
  type        = string
  default     = "BASIC"

  validation {
    condition     = contains(["BASIC", "STANDARD_HA"], var.tier)
    error_message = "tier must be BASIC or STANDARD_HA."
  }
}

variable "memory_size_gb" {
  type    = number
  default = 1
}

variable "redis_version" {
  type    = string
  default = "REDIS_7_0"
}

variable "transit_encryption_mode" {
  type    = string
  default = "SERVER_AUTHENTICATION"
}

variable "redis_configs" {
  description = "Celery needs keys to survive; do not evict them silently."
  type        = map(string)
  default = {
    maxmemory-policy = "noeviction"
  }
}

variable "maintenance_day" {
  type    = string
  default = "SUNDAY"
}

variable "maintenance_hour" {
  type    = number
  default = 4
}

variable "labels" {
  type    = map(string)
  default = {}
}
