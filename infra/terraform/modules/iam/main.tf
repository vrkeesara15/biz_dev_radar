# Service accounts, one per workload, each with the smallest set of project roles that
# makes it work (SPEC 11 RBAC / least privilege). Bucket and secret access are granted at
# the resource, not here, so a new bucket cannot accidentally widen an existing identity.
#
#   api       serves HTTP, reads Cloud SQL and the buckets, reads secrets
#   worker    same plus it may start Cloud Run job executions (admin "run now")
#   jobs      the per-adapter Cloud Run jobs and the migrate job
#   scheduler Cloud Scheduler's own identity: it may only *run* jobs
#   deploy    the GitHub Actions identity, impersonated through Workload Identity

locals {
  runtime_roles = [
    "roles/cloudsql.client",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
    "roles/cloudprofiler.agent",
  ]

  runtime_accounts = {
    api    = google_service_account.api.email
    worker = google_service_account.worker.email
    jobs   = google_service_account.jobs.email
  }
}

resource "google_service_account" "api" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-api"
  display_name = "BidRadar API (${var.environment})"
}

resource "google_service_account" "worker" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-worker"
  display_name = "BidRadar Celery worker and beat (${var.environment})"
}

resource "google_service_account" "jobs" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-jobs"
  display_name = "BidRadar Cloud Run jobs (${var.environment})"
}

resource "google_service_account" "scheduler" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-sched"
  display_name = "BidRadar Cloud Scheduler (${var.environment})"
}

resource "google_service_account" "deploy" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-deploy"
  display_name = "BidRadar GitHub Actions deployer (${var.environment})"
}

resource "google_project_iam_member" "runtime" {
  for_each = {
    for pair in setproduct(keys(local.runtime_accounts), local.runtime_roles) :
    "${pair[0]}::${pair[1]}" => { account = pair[0], role = pair[1] }
  }

  project = var.project_id
  role    = each.value.role
  member  = "serviceAccount:${local.runtime_accounts[each.value.account]}"
}

# The admin console's "run now" starts a job execution; nothing else may.
resource "google_project_iam_member" "worker_runs_jobs" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_project_iam_member" "scheduler_runs_jobs" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.scheduler.email}"
}

# ---------------------------------------------------------------- deploy identity
# Workload Identity Federation: GitHub Actions gets a short-lived token, never a JSON key
# (SPEC 11 "never store credentials").
resource "google_iam_workload_identity_pool" "github" {
  count = var.create_workload_identity_pool ? 1 : 0

  project                   = var.project_id
  workload_identity_pool_id = "${var.name_prefix}-gh"
  display_name              = "GitHub Actions (${var.environment})"
  description               = "Keyless deploys for ${var.github_repository}"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  count = var.create_workload_identity_pool ? 1 : 0

  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  # Only this repository, and only from a branch or tag of it.
  attribute_condition = "assertion.repository == \"${var.github_repository}\""

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "deploy_workload_identity" {
  count = var.create_workload_identity_pool ? 1 : 0

  service_account_id = google_service_account.deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github[0].name}/attribute.repository/${var.github_repository}"
}

resource "google_project_iam_member" "deploy" {
  for_each = toset(var.deploy_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deploy.email}"
}

# The deployer sets the runtime service account on a revision, which requires actAs.
resource "google_service_account_iam_member" "deploy_act_as" {
  for_each = local.runtime_accounts

  service_account_id = "projects/${var.project_id}/serviceAccounts/${each.value}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deploy.email}"
}
