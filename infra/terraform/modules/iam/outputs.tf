output "api_service_account" {
  value = google_service_account.api.email
}

output "worker_service_account" {
  value = google_service_account.worker.email
}

output "jobs_service_account" {
  value = google_service_account.jobs.email
}

output "scheduler_service_account" {
  value = google_service_account.scheduler.email
}

output "deploy_service_account" {
  value = google_service_account.deploy.email
}

output "runtime_members" {
  description = "IAM members for every runtime identity, for bucket and secret bindings."
  value = [
    "serviceAccount:${google_service_account.api.email}",
    "serviceAccount:${google_service_account.worker.email}",
    "serviceAccount:${google_service_account.jobs.email}",
  ]
}

output "workload_identity_provider" {
  description = "Value for google-github-actions/auth `workload_identity_provider`."
  value       = var.create_workload_identity_pool ? google_iam_workload_identity_pool_provider.github[0].name : ""
}
