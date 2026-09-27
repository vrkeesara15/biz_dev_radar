# Cloud SQL Postgres 16 (SPEC 10.1): private IP only, daily automated backups and
# point-in-time recovery with 7 days of transaction logs (SPEC 11 "daily backups +
# PITR 7 days"). pgvector and pg_trgm ship with Cloud SQL for PostgreSQL 16 and need no
# flag; the EXTENSION statements themselves live in the Alembic migrations, so this module
# creates the instance and the database and nothing inside it.

resource "google_sql_database_instance" "this" {
  project             = var.project_id
  name                = var.instance_name
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = var.deletion_protection

  settings {
    tier                        = var.tier
    edition                     = var.edition
    availability_type           = var.availability_type
    disk_type                   = "PD_SSD"
    disk_size                   = var.disk_size_gb
    disk_autoresize             = true
    disk_autoresize_limit       = var.disk_autoresize_limit_gb
    deletion_protection_enabled = var.deletion_protection

    backup_configuration {
      enabled                        = true
      start_time                     = var.backup_start_time
      location                       = var.backup_location
      point_in_time_recovery_enabled = true
      # SPEC 11: restore to any second in the last week.
      transaction_log_retention_days = var.transaction_log_retention_days

      backup_retention_settings {
        retained_backups = var.retained_backups
        retention_unit   = "COUNT"
      }
    }

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = var.network_id
      enable_private_path_for_google_cloud_services = true
      ssl_mode                                      = "ENCRYPTED_ONLY"
    }

    maintenance_window {
      day          = var.maintenance_day
      hour         = var.maintenance_hour
      update_track = "stable"
    }

    insights_config {
      query_insights_enabled  = true
      query_string_length     = 1024
      record_application_tags = true
      record_client_address   = false
    }

    dynamic "database_flags" {
      for_each = var.database_flags
      content {
        name  = database_flags.key
        value = database_flags.value
      }
    }

    user_labels = merge(var.labels, { component = "database" })
  }

  lifecycle {
    # A destroy here is never what we meant; changing the region means a new instance.
    ignore_changes = [settings[0].disk_size]
  }
}

resource "google_sql_database" "app" {
  project  = var.project_id
  name     = var.database_name
  instance = google_sql_database_instance.this.name

  # Postgres defaults; the migrations own everything inside.
  charset   = "UTF8"
  collation = "en_US.UTF8"
}

# Two roles, as in the local stack: the owner runs migrations, the app role runs under RLS
# (SPEC 11). Passwords are generated here and written to Secret Manager by the caller —
# they are marked sensitive and never printed.
resource "random_password" "owner" {
  length           = 32
  special          = true
  min_special      = 2
  override_special = "-_.~"
}

resource "random_password" "app" {
  length           = 32
  special          = true
  min_special      = 2
  override_special = "-_.~"
}

resource "google_sql_user" "owner" {
  project  = var.project_id
  name     = var.owner_user
  instance = google_sql_database_instance.this.name
  password = random_password.owner.result
}

resource "google_sql_user" "app" {
  project  = var.project_id
  name     = var.app_user
  instance = google_sql_database_instance.this.name
  password = random_password.app.result
}
