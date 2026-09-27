# Alerting for the things that silently stop working: the API returning 5xx, a Cloud Run
# job failing (an adapter that stops crawling looks exactly like a quiet day), the
# database filling up, and PITR/backups drifting off. Notification goes to the ops email
# and, when configured, the ops Slack webhook the nightly smoke already uses.

resource "google_monitoring_notification_channel" "email" {
  count = var.ops_email == "" ? 0 : 1

  project      = var.project_id
  display_name = "BidRadar ops (${var.environment})"
  type         = "email"

  labels = {
    email_address = var.ops_email
  }
}

locals {
  channels = concat(
    google_monitoring_notification_channel.email[*].id,
    var.extra_notification_channels,
  )
}

resource "google_monitoring_alert_policy" "api_error_rate" {
  count = var.enabled ? 1 : 0

  project      = var.project_id
  display_name = "BidRadar ${var.environment}: API 5xx rate"
  combiner     = "OR"

  conditions {
    display_name = "5xx responses over ${var.error_rate_threshold}/s for 5 minutes"

    condition_threshold {
      filter = join(" AND ", [
        "resource.type = \"cloud_run_revision\"",
        "resource.labels.service_name = \"${var.api_service_name}\"",
        "metric.type = \"run.googleapis.com/request_count\"",
        "metric.labels.response_code_class = \"5xx\"",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = var.error_rate_threshold
      duration        = "300s"

      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }

  notification_channels = local.channels
  severity              = "ERROR"

  documentation {
    content   = "See docs/runbooks/deploy.md for rollback; the previous revision is one traffic shift away."
    mime_type = "text/markdown"
  }
}

resource "google_monitoring_alert_policy" "job_failures" {
  count = var.enabled ? 1 : 0

  project      = var.project_id
  display_name = "BidRadar ${var.environment}: Cloud Run job failed"
  combiner     = "OR"

  conditions {
    display_name = "A job execution failed"

    condition_threshold {
      filter = join(" AND ", [
        "resource.type = \"cloud_run_job\"",
        "metric.type = \"run.googleapis.com/job/completed_task_attempt_count\"",
        "metric.labels.result = \"failed\"",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = local.channels
  severity              = "WARNING"

  documentation {
    content   = "An adapter that stops crawling looks like a quiet day. Check source_runs and the nightly smoke."
    mime_type = "text/markdown"
  }
}

resource "google_monitoring_alert_policy" "database_disk" {
  count = var.enabled ? 1 : 0

  project      = var.project_id
  display_name = "BidRadar ${var.environment}: Cloud SQL disk above ${var.disk_threshold_ratio * 100}%"
  combiner     = "OR"

  conditions {
    display_name = "Disk utilisation"

    condition_threshold {
      filter = join(" AND ", [
        "resource.type = \"cloudsql_database\"",
        "metric.type = \"cloudsql.googleapis.com/database/disk/utilization\"",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = var.disk_threshold_ratio
      duration        = "600s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }

  notification_channels = local.channels
  severity              = "WARNING"
}

# SPEC 11 requires a restore drill; this makes a drill that stopped running visible.
resource "google_monitoring_alert_policy" "backup_failure" {
  count = var.enabled ? 1 : 0

  project      = var.project_id
  display_name = "BidRadar ${var.environment}: Cloud SQL automated backup failed"
  combiner     = "OR"

  conditions {
    display_name = "A scheduled backup did not succeed"

    condition_matched_log {
      filter = join(" AND ", [
        "resource.type=\"cloudsql_database\"",
        "protoPayload.methodName=\"cloudsql.backupRuns.create\"",
        "severity>=ERROR",
      ])
    }
  }

  alert_strategy {
    notification_rate_limit {
      period = "3600s"
    }
  }

  notification_channels = local.channels
  severity              = "ERROR"

  documentation {
    content   = "Run scripts/restore_drill.sh --dry-run and see docs/runbooks/restore-drill.md."
    mime_type = "text/markdown"
  }
}
