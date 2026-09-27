# ------------------------------------------------------------------ residency
# SPEC 12's India checklist ends with "verify bucket and DB locations". These four outputs
# are that verification: `terraform output residency` prints where the data physically is.

output "residency" {
  description = "Where this environment's data lives, for the SPEC 12 residency check."
  value = {
    environment     = var.environment
    declared        = var.residency
    gcp_region      = var.region
    bucket_name     = module.gcs.bucket_name
    bucket_location = module.gcs.bucket_location
    cmek_location   = module.gcs.kms_key_location
    database_region = module.cloud_sql.region
    backup_location = module.cloud_sql.backup_location
    redis_region    = module.memorystore.region
    secrets_region  = module.secrets.replication_location
  }
}

output "residency_ok" {
  description = "True when every store is in the environment's own region."
  value = alltrue([
    lower(module.gcs.bucket_location) == lower(var.region),
    lower(module.gcs.kms_key_location) == lower(var.region),
    module.cloud_sql.region == var.region,
    module.cloud_sql.backup_location == var.region,
    module.memorystore.region == var.region,
    module.secrets.replication_location == var.region,
  ])
}

output "residency_note" {
  description = "Why the other region's bucket name points at nothing."
  value       = "REGION=${var.residency}; a tenant with the other residency would resolve ${local.absent_bucket}, which does not exist, so a misrouted write fails instead of leaking."
}

# ------------------------------------------------------------------ backups
output "backups" {
  description = "SPEC 11: daily backups + PITR 7 days. docs/runbooks/restore-drill.md drills it."
  value = {
    instance                       = module.cloud_sql.instance_name
    connection_name                = module.cloud_sql.connection_name
    point_in_time_recovery_enabled = module.cloud_sql.point_in_time_recovery_enabled
    transaction_log_retention_days = module.cloud_sql.transaction_log_retention_days
    retained_backups               = var.sql_retained_backups
    backup_location                = module.cloud_sql.backup_location
  }
}

# ------------------------------------------------------------------ deploy inputs
output "service_urls" {
  value = {
    api      = module.api.uri
    frontend = module.frontend.uri
  }
}

output "cloud_run_services" {
  description = "Service names the deploy workflow updates."
  value = {
    api      = module.api.name
    worker   = module.worker.name
    beat     = module.beat.name
    frontend = module.frontend.name
  }
}

output "cloud_run_jobs" {
  description = "Job names; `migrate` is executed before every traffic shift (M7-03)."
  value       = module.jobs.job_names
}

output "scheduler_jobs" {
  description = "Adapter -> cron actually scheduled in this environment."
  value       = module.scheduler.schedules
}

output "artifact_registry_url" {
  value = module.artifact_registry.repository_url
}

output "deploy_service_account" {
  value = module.iam.deploy_service_account
}

output "workload_identity_provider" {
  description = "For google-github-actions/auth; no JSON key ever exists."
  value       = module.iam.workload_identity_provider
}

output "secret_ids" {
  description = "Setting name -> Secret Manager id. Values are added out of band."
  value       = module.secrets.secret_ids
}

output "runtime_service_accounts" {
  value = {
    api       = module.iam.api_service_account
    worker    = module.iam.worker_service_account
    jobs      = module.iam.jobs_service_account
    scheduler = module.iam.scheduler_service_account
  }
}

output "vpc_connector" {
  value = module.network.connector_id
}
