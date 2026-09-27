# Memorystore for Redis: the Celery broker and the API rate-limit token buckets (M7-06).
# Private service access only, AUTH on and TLS in transit.

resource "google_redis_instance" "this" {
  project            = var.project_id
  name               = var.name
  region             = var.region
  tier               = var.tier
  memory_size_gb     = var.memory_size_gb
  redis_version      = var.redis_version
  authorized_network = var.network_id
  connect_mode       = "PRIVATE_SERVICE_ACCESS"

  auth_enabled            = true
  transit_encryption_mode = var.transit_encryption_mode

  redis_configs = var.redis_configs
  labels        = merge(var.labels, { component = "cache" })

  maintenance_policy {
    weekly_maintenance_window {
      day = var.maintenance_day
      start_time {
        hours   = var.maintenance_hour
        minutes = 0
        seconds = 0
        nanos   = 0
      }
    }
  }
}
