variable "project_id" {
  type = string
}

variable "environment" {
  type = string
}

variable "name_prefix" {
  description = "Service account id prefix; GCP caps account_id at 30 characters."
  type        = string

  validation {
    condition     = length(var.name_prefix) <= 22
    error_message = "name_prefix must leave room for the -worker suffix inside 30 characters."
  }
}

variable "github_repository" {
  description = "owner/repo allowed to mint tokens for the deploy account."
  type        = string
  default     = ""
}

variable "create_workload_identity_pool" {
  description = "One pool per project; false when several environments share a project."
  type        = bool
  default     = true
}

variable "deploy_roles" {
  description = "What GitHub Actions may do. No project owner, no key creation."
  type        = list(string)
  default = [
    "roles/run.admin",
    "roles/artifactregistry.writer",
    "roles/cloudsql.client",
    "roles/logging.viewer",
  ]
}
