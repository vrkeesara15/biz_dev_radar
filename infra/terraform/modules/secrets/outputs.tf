output "secret_ids" {
  description = "setting name -> Secret Manager secret id (what a Cloud Run secretKeyRef needs)."
  value       = { for name, secret in google_secret_manager_secret.this : name => secret.secret_id }
}

output "secret_names" {
  description = "setting name -> fully qualified secret resource name."
  value       = { for name, secret in google_secret_manager_secret.this : name => secret.name }
}

output "replication_location" {
  description = "Where the secret material lives (residency check)."
  value       = var.replication_location
}
