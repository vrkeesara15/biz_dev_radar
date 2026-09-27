output "host" {
  value = google_redis_instance.this.host
}

output "port" {
  value = google_redis_instance.this.port
}

output "region" {
  value = google_redis_instance.this.region
}

output "redis_url" {
  description = "REDIS_URL for the app; rediss:// because transit encryption is on."
  sensitive   = true
  value = format(
    "%s://:%s@%s:%d/0",
    google_redis_instance.this.transit_encryption_mode == "DISABLED" ? "redis" : "rediss",
    urlencode(google_redis_instance.this.auth_string),
    google_redis_instance.this.host,
    google_redis_instance.this.port,
  )
}
