output "residency" {
  description = "SPEC 12 residency check: bucket, CMEK, database and backup locations."
  value       = module.bidradar.residency
}

output "residency_ok" {
  value = module.bidradar.residency_ok
}

output "backups" {
  value = module.bidradar.backups
}

output "service_urls" {
  value = module.bidradar.service_urls
}

output "cloud_run_services" {
  value = module.bidradar.cloud_run_services
}

output "cloud_run_jobs" {
  value = module.bidradar.cloud_run_jobs
}

output "scheduler_jobs" {
  value = module.bidradar.scheduler_jobs
}

output "artifact_registry_url" {
  value = module.bidradar.artifact_registry_url
}

output "deploy_service_account" {
  value = module.bidradar.deploy_service_account
}

output "workload_identity_provider" {
  value = module.bidradar.workload_identity_provider
}

output "secret_ids" {
  value = module.bidradar.secret_ids
}

output "state_bucket" {
  value = var.state_bucket
}
