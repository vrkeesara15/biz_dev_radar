output "repository_url" {
  description = "Host/path prefix for image references, e.g. us-east1-docker.pkg.dev/proj/bidradar."
  value       = "${var.location}-docker.pkg.dev/${var.project_id}/${var.repository_id}"
}

output "repository_name" {
  value = var.create ? google_artifact_registry_repository.this[0].name : ""
}
