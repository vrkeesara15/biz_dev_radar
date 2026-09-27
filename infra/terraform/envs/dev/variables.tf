variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "backend_image" {
  description = "Backend image reference; the deploy pipeline passes a digest."
  type        = string
}

variable "frontend_image" {
  type = string
}

variable "ops_email" {
  type    = string
  default = ""
}

variable "github_repository" {
  type    = string
  default = ""
}

variable "state_bucket" {
  description = <<-EOT
    Bucket holding this environment's Terraform state. Not consumed by a resource: a
    backend block cannot read variables, so this records in code what
    `terraform init -backend-config=backend.hcl` must be pointed at, and the
    state_bucket output makes a mismatch visible.
  EOT
  type        = string
}
