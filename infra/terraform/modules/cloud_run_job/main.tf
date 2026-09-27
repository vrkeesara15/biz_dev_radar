# Cloud Run jobs: one per adapter (SPEC 10.1 "Cloud Scheduler -> Cloud Run jobs, one per
# adapter"), plus migrate and smoke. `for_each` over a map keyed by job name keeps the
# Terraform address stable when an adapter is added in the middle of the list.

resource "google_cloud_run_v2_job" "this" {
  for_each = var.jobs

  project             = var.project_id
  name                = "${var.name_prefix}-${each.key}"
  location            = var.region
  deletion_protection = false
  labels              = merge(var.labels, { component = "job", job = each.key })

  template {
    parallelism = 1
    task_count  = 1

    template {
      service_account = var.service_account
      max_retries     = try(each.value.max_retries, var.default_max_retries)
      timeout         = "${try(each.value.timeout_seconds, var.default_timeout_seconds)}s"

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
        args  = each.value.args

        resources {
          limits = {
            cpu    = try(each.value.cpu, var.default_cpu)
            memory = try(each.value.memory, var.default_memory)
          }
        }

        dynamic "env" {
          for_each = var.env
          content {
            name  = env.key
            value = env.value
          }
        }

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
      }
    }
  }
}
