variable "project_id" {
  type = string
}

variable "location" {
  description = "Bucket location; the KMS keyring is created in the same one (residency)."
  type        = string
}

variable "residency" {
  description = "us or in; recorded as a label and surfaced in the residency outputs."
  type        = string

  validation {
    condition     = contains(["us", "in"], var.residency)
    error_message = "residency must be us or in."
  }
}

variable "name_prefix" {
  type = string
}

variable "bucket_name" {
  description = "Globally unique bucket name, e.g. bidradar-dev-us."
  type        = string
}

variable "storage_class" {
  type    = string
  default = "STANDARD"
}

variable "versioning" {
  type    = bool
  default = true
}

variable "force_destroy" {
  description = "Only ever true for dev; a production bucket must not be emptied by a plan."
  type        = bool
  default     = false
}

variable "raw_retention_days" {
  description = "Archived source payloads under raw/ (SPEC 5.1)."
  type        = number
  default     = 400
}

variable "export_retention_days" {
  description = "Tenant exports under exports/ (M7-07)."
  type        = number
  default     = 30
}

variable "nearline_after_days" {
  type    = number
  default = 120
}

variable "key_rotation_period" {
  description = "CMEK rotation; 90 days in seconds."
  type        = string
  default     = "7776000s"
}

variable "key_protection_level" {
  type    = string
  default = "SOFTWARE"
}

variable "object_admin_members" {
  description = "IAM members (serviceAccount:...) allowed to read and write objects."
  type        = list(string)
  default     = []
}

variable "access_log_bucket" {
  description = "Where to write access logs; empty means the bucket logs to itself."
  type        = string
  default     = ""
}

variable "labels" {
  type    = map(string)
  default = {}
}
