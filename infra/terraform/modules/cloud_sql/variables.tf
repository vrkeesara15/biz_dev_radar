variable "project_id" {
  type = string
}

variable "region" {
  description = "Residency-bearing: us-east1 or asia-south1. Changing it replaces the instance."
  type        = string
}

variable "instance_name" {
  type = string
}

variable "network_id" {
  description = "VPC self link; the instance gets a private IP on it."
  type        = string
}

variable "database_name" {
  type    = string
  default = "bidradar"
}

variable "owner_user" {
  type    = string
  default = "bidradar"
}

variable "app_user" {
  description = "Non-owner role the API uses; RLS applies to it."
  type        = string
  default     = "bidradar_app"
}

variable "tier" {
  type    = string
  default = "db-custom-2-7680"
}

variable "edition" {
  type    = string
  default = "ENTERPRISE"
}

variable "availability_type" {
  description = "REGIONAL for production (HA), ZONAL for dev."
  type        = string
  default     = "ZONAL"

  validation {
    condition     = contains(["ZONAL", "REGIONAL"], var.availability_type)
    error_message = "availability_type must be ZONAL or REGIONAL."
  }
}

variable "disk_size_gb" {
  type    = number
  default = 50
}

variable "disk_autoresize_limit_gb" {
  type    = number
  default = 500
}

variable "deletion_protection" {
  description = "True in production: SPEC 11 treats the tenant database as a launch blocker."
  type        = bool
  default     = true
}

variable "backup_start_time" {
  description = "HH:MM UTC for the daily automated backup."
  type        = string
  default     = "03:00"
}

variable "backup_location" {
  description = "Backup location; keep it in-region so Indian data never leaves asia-south1."
  type        = string
}

variable "transaction_log_retention_days" {
  description = "PITR window. SPEC 11 requires 7 days."
  type        = number
  default     = 7

  validation {
    condition     = var.transaction_log_retention_days >= 7
    error_message = "SPEC 11 requires at least 7 days of PITR transaction logs."
  }
}

variable "retained_backups" {
  description = "How many daily automated backups to keep."
  type        = number
  default     = 30
}

variable "maintenance_day" {
  type    = number
  default = 7
}

variable "maintenance_hour" {
  type    = number
  default = 4
}

variable "database_flags" {
  description = "Instance flags. pgvector/pg_trgm are extensions, not flags: migrations create them."
  type        = map(string)
  default = {
    "cloudsql.enable_pgaudit"     = "on"
    "pgaudit.log"                 = "ddl,role"
    "log_min_duration_statement"  = "1000"
    "cloudsql.iam_authentication" = "on"
  }
}

variable "labels" {
  type    = map(string)
  default = {}
}
