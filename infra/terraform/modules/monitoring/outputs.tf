output "notification_channels" {
  value = local.channels
}

output "alert_policy_names" {
  value = compact([
    try(google_monitoring_alert_policy.api_error_rate[0].display_name, ""),
    try(google_monitoring_alert_policy.job_failures[0].display_name, ""),
    try(google_monitoring_alert_policy.database_disk[0].display_name, ""),
    try(google_monitoring_alert_policy.backup_failure[0].display_name, ""),
  ])
}
