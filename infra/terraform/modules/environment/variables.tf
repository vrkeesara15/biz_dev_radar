variable "environment" {
  description = "dev | staging-in | prod-us | prod-in (SPEC 12)."
  type        = string

  validation {
    condition     = contains(["dev", "staging-in", "prod-us", "prod-in"], var.environment)
    error_message = "environment must be one of dev, staging-in, prod-us, prod-in (SPEC 12)."
  }
}

variable "project_id" {
  type = string
}

variable "region" {
  description = "GCP region. us-east1 for dev/prod-us, asia-south1 for staging-in/prod-in."
  type        = string

  validation {
    condition     = contains(["us-east1", "asia-south1"], var.region)
    error_message = "SPEC 12 deploys only to us-east1 and asia-south1."
  }
}

variable "residency" {
  description = "us or in; must match the region, which a check below enforces."
  type        = string

  validation {
    condition     = contains(["us", "in"], var.residency)
    error_message = "residency must be us or in."
  }
}

variable "app_env" {
  description = "APP_ENV the containers see; production turns on JSON logging."
  type        = string
  default     = "production"
}

variable "name_prefix" {
  description = "Prefix for every resource name, e.g. bidradar-dev."
  type        = string
}

variable "bucket_name" {
  description = "Globally unique bucket name for this environment's residency region."
  type        = string
}

# ------------------------------------------------------------------ images
variable "backend_image" {
  description = "Backend image, by digest in production so a promotion cannot drift."
  type        = string
}

variable "frontend_image" {
  type = string
}

variable "artifact_registry_project" {
  description = "Project that owns the image repository; empty means this project."
  type        = string
  default     = ""
}

variable "create_artifact_registry" {
  description = "True for dev (images are built there); false for environments that pull."
  type        = bool
  default     = false
}

# ------------------------------------------------------------------ sizing
variable "sql_tier" {
  type    = string
  default = "db-custom-2-7680"
}

variable "sql_availability_type" {
  type    = string
  default = "ZONAL"
}

variable "sql_disk_size_gb" {
  type    = number
  default = 50
}

variable "sql_deletion_protection" {
  type    = bool
  default = true
}

variable "sql_retained_backups" {
  type    = number
  default = 30
}

variable "redis_tier" {
  type    = string
  default = "BASIC"
}

variable "redis_memory_size_gb" {
  type    = number
  default = 1
}

variable "api_min_instances" {
  type    = number
  default = 1
}

variable "api_max_instances" {
  type    = number
  default = 20
}

variable "worker_max_instances" {
  type    = number
  default = 5
}

variable "frontend_min_instances" {
  type    = number
  default = 1
}

variable "frontend_max_instances" {
  type    = number
  default = 10
}

variable "bucket_force_destroy" {
  description = "Only dev. A production bucket must never be emptied by a plan."
  type        = bool
  default     = false
}

# ------------------------------------------------------------------ networking
variable "subnet_cidr" {
  type    = string
  default = "10.10.0.0/20"
}

variable "connector_cidr" {
  type    = string
  default = "10.10.240.0/28"
}

# ------------------------------------------------------------------ app config
variable "cors_origins" {
  description = "Comma-separated origins the API accepts."
  type        = string
}

variable "frontend_api_base_url" {
  description = "API origin baked into the front-end bundle; empty uses the API's own URI."
  type        = string
  default     = ""
}

variable "secret_settings" {
  description = <<-EOT
    Credential setting names, mirroring SECRET_SETTINGS in backend/app/core/config.py.
    backend/tests/unit/test_terraform_config.py fails when the two lists drift.
  EOT
  type        = list(string)
  default = [
    "database_url",
    "database_url_owner",
    "redis_url",
    "auth_secret",
    "field_encryption_key",
    "s3_access_key",
    "s3_secret_key",
    "sam_api_key",
    "anthropic_api_key",
    "voyage_api_key",
    "sendgrid_api_key",
    "smtp_password",
    "vapid_private_key",
    "stripe_secret_key",
    "stripe_webhook_secret",
    "razorpay_key_id",
    "razorpay_key_secret",
    "razorpay_webhook_secret",
    "sentry_dsn",
    "langfuse_public_key",
    "langfuse_secret_key",
  ]
}

variable "adapter_jobs" {
  description = <<-EOT
    Overrides the generated adapter list. Leave null: the real value is read from
    infra/cloudrun/adapters.auto.tfvars.json, which `python -m app.jobs.generate_cloudrun`
    writes from the adapter registry, so a new adapter gets its job and its Cloud
    Scheduler trigger without anyone editing HCL.
  EOT
  type = list(object({
    source_id = string
    schedule  = string
    region    = string
  }))
  default = null
}

variable "scheduled_adapter_regions" {
  description = <<-EOT
    Which adapters this environment actually crawls on a cron. A job is created for every
    adapter (so an operator can run any of them by hand) but only these get a trigger:
    prod-in has no reason to hit SAM.gov every 30 minutes.
  EOT
  type        = list(string)
  default     = ["us", "in"]
}

variable "extra_env" {
  description = "Additional non-secret environment variables for the backend containers."
  type        = map(string)
  default     = {}
}

# ------------------------------------------------------------------ ops
variable "ops_email" {
  type    = string
  default = ""
}

variable "monitoring_enabled" {
  type    = bool
  default = true
}

variable "github_repository" {
  type    = string
  default = ""
}

variable "create_workload_identity_pool" {
  type    = bool
  default = true
}

variable "labels" {
  type    = map(string)
  default = {}
}
