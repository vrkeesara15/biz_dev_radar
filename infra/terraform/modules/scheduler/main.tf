# One Cloud Scheduler trigger per adapter, using the adapter's OWN cron string
# (SourceAdapter.schedule in the registry, exported to
# infra/cloudrun/adapters.auto.tfvars.json). Per-source cron means one portal's outage
# never delays another's crawl (SPEC 10.1 "Isolated failures, per-source cron").

locals {
  run_api = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs"
}

resource "google_cloud_scheduler_job" "this" {
  for_each = var.triggers

  project     = var.project_id
  region      = var.region
  name        = "${var.name_prefix}-${each.key}"
  description = "Run the ${each.key} Cloud Run job"
  schedule    = each.value.schedule
  time_zone   = var.time_zone

  attempt_deadline = var.attempt_deadline

  retry_config {
    retry_count          = var.retry_count
    min_backoff_duration = var.min_backoff
    max_backoff_duration = var.max_backoff
    max_doublings        = 3
  }

  http_target {
    http_method = "POST"
    uri         = "${local.run_api}/${each.value.job_name}:run"

    oauth_token {
      service_account_email = var.service_account
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}
