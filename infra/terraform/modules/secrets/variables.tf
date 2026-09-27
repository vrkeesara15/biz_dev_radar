variable "project_id" {
  type = string
}

variable "name_prefix" {
  description = "Prefix for every secret id, e.g. bidradar-dev."
  type        = string
}

variable "replication_location" {
  description = "Single region for user-managed replication; residency-bearing."
  type        = string
}

variable "secret_names" {
  description = "Setting names from SECRET_SETTINGS (snake_case); hyphenated for the secret id."
  type        = list(string)
}

variable "accessor_members" {
  description = "IAM members granted secretAccessor on every secret."
  type        = list(string)
  default     = []
}

variable "managed_values" {
  description = "setting name -> value for the few secrets Terraform generates itself."
  type        = map(string)
  default     = {}
  sensitive   = true
}

variable "labels" {
  type    = map(string)
  default = {}
}
