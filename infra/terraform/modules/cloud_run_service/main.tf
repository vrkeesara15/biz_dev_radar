# One Cloud Run v2 service. The same module builds api, worker, beat and frontend; the
# differences are arguments, not resources (see infra/cloudrun/services/*.yaml for the
# equivalent Knative spec that a human can read).

resource "google_cloud_run_v2_service" "this" {
  project             = var.project_id
  name                = var.name
  location            = var.region
  ingress             = var.ingress
  deletion_protection = var.deletion_protection
  labels              = merge(var.labels, { component = var.component })

  template {
    service_account                  = var.service_account
    max_instance_request_concurrency = var.concurrency
    timeout                          = "${var.request_timeout_seconds}s"
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"
    labels                           = merge(var.labels, { component = var.component })

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    dynamic "vpc_access" {
      for_each = var.vpc_connector == "" ? [] : [var.vpc_connector]
      content {
        connector = vpc_access.value
        egress    = "PRIVATE_RANGES_ONLY"
      }
    }

    dynamic "volumes" {
      for_each = var.cloudsql_instance == "" ? [] : [var.cloudsql_instance]
      content {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [volumes.value]
        }
      }
    }

    containers {
      image = var.image
      args  = var.args

      ports {
        name           = "http1"
        container_port = var.port
      }

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        # A Celery worker keeps working between requests; throttled CPU stalls it.
        cpu_idle          = var.cpu_idle
        startup_cpu_boost = var.startup_cpu_boost
      }

      dynamic "env" {
        for_each = var.env
        content {
          name  = env.key
          value = env.value
        }
      }

      # Credentials are pulled from Secret Manager at start; never baked into the image.
      dynamic "env" {
        for_each = var.secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }

      dynamic "volume_mounts" {
        for_each = var.cloudsql_instance == "" ? [] : [var.cloudsql_instance]
        content {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }

      dynamic "startup_probe" {
        for_each = var.health_path == "" ? [] : [var.health_path]
        content {
          initial_delay_seconds = 5
          period_seconds        = 5
          timeout_seconds       = 5
          failure_threshold     = 30
          http_get {
            path = startup_probe.value
            port = var.port
          }
        }
      }

      dynamic "liveness_probe" {
        for_each = var.health_path == "" ? [] : [var.health_path]
        content {
          period_seconds    = 30
          timeout_seconds   = 5
          failure_threshold = 3
          http_get {
            path = liveness_probe.value
            port = var.port
          }
        }
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  lifecycle {
    # Preview environments add tagged, zero-traffic revisions out of band (M7-03);
    # Terraform must not delete them on the next apply.
    ignore_changes = [
      client,
      client_version,
      template[0].revision,
    ]
  }
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  count = var.allow_unauthenticated ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.this.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "invokers" {
  for_each = toset(var.invoker_members)

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.this.name
  role     = "roles/run.invoker"
  member   = each.value
}
