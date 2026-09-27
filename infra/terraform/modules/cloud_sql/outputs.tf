output "instance_name" {
  value = google_sql_database_instance.this.name
}

output "connection_name" {
  description = "project:region:instance for the Cloud Run Cloud SQL connector."
  value       = google_sql_database_instance.this.connection_name
}

output "private_ip_address" {
  value = google_sql_database_instance.this.private_ip_address
}

output "region" {
  description = "Where the rows physically live (residency check, SPEC 12)."
  value       = google_sql_database_instance.this.region
}

output "backup_location" {
  description = "Where the daily backups live; must not leave the residency region."
  value       = google_sql_database_instance.this.settings[0].backup_configuration[0].location
}

output "point_in_time_recovery_enabled" {
  value = google_sql_database_instance.this.settings[0].backup_configuration[0].point_in_time_recovery_enabled
}

output "transaction_log_retention_days" {
  value = google_sql_database_instance.this.settings[0].backup_configuration[0].transaction_log_retention_days
}

output "database_name" {
  value = google_sql_database.app.name
}

output "app_dsn" {
  description = "SQLAlchemy DSN for the RLS app role, via the Cloud SQL unix socket."
  sensitive   = true
  value = format(
    "postgresql+asyncpg://%s:%s@/%s?host=/cloudsql/%s",
    google_sql_user.app.name,
    urlencode(random_password.app.result),
    google_sql_database.app.name,
    google_sql_database_instance.this.connection_name,
  )
}

output "owner_dsn" {
  description = "SQLAlchemy DSN for the owner role (migrations)."
  sensitive   = true
  value = format(
    "postgresql+asyncpg://%s:%s@/%s?host=/cloudsql/%s",
    google_sql_user.owner.name,
    urlencode(random_password.owner.result),
    google_sql_database.app.name,
    google_sql_database_instance.this.connection_name,
  )
}
