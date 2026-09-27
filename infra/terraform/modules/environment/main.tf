# One BidRadar environment (SPEC 12: dev, staging-in, prod-us, prod-in). Each env under
# infra/terraform/envs/ is this module plus a tfvars file, so the four environments differ
# in numbers and region, never in shape — which is what makes "the same image in both
# regions" true rather than aspirational.

locals {
  # Residency must match the region, or an Indian tenant's rows would sit in Virginia.
  expected_region = var.residency == "in" ? "asia-south1" : "us-east1"

  labels = merge(var.labels, {
    app         = "bidradar"
    environment = var.environment
    residency   = var.residency
    managed_by  = "terraform"
  })

  registry_project = var.artifact_registry_project == "" ? var.project_id : var.artifact_registry_project

  # The adapter list comes from the registry via the generator, not from HCL (OQ-70).
  adapter_jobs = coalesce(
    var.adapter_jobs,
    jsondecode(file("${path.module}/../../../cloudrun/adapters.auto.tfvars.json")).adapter_jobs,
  )

  adapter_job_defs = {
    for row in local.adapter_jobs :
    replace(row.source_id, "_", "-") => {
      args            = ["job:${row.source_id}"]
      timeout_seconds = 3600
      memory          = "2Gi"
    }
  }

  jobs = merge(local.adapter_job_defs, {
    migrate = { args = ["migrate"], timeout_seconds = 900, memory = "1Gi", max_retries = 0 }
    smoke   = { args = ["smoke"], timeout_seconds = 1800, memory = "1Gi" }
  })

  scheduler_triggers = {
    for row in local.adapter_jobs :
    replace(row.source_id, "_", "-") => {
      schedule = row.schedule
      job_name = "${var.name_prefix}-${replace(row.source_id, "_", "-")}"
    }
    if contains(var.scheduled_adapter_regions, row.region)
  }

  # A deployment serves ONE residency. The other region's bucket name deliberately points
  # at a bucket that does not exist, so a misrouted tenant fails loudly instead of writing
  # Indian documents into a US bucket (see the residency_note output).
  absent_bucket = "${var.name_prefix}-no-foreign-residency"

  backend_env = merge({
    APP_ENV             = var.app_env
    REGION              = var.residency
    STORAGE_BACKEND     = "gcs"
    GCS_BUCKET_US       = var.residency == "us" ? module.gcs.bucket_name : local.absent_bucket
    GCS_BUCKET_IN       = var.residency == "in" ? module.gcs.bucket_name : local.absent_bucket
    SCANNER_BACKEND     = "clamav"
    CLAMAV_HOST         = "127.0.0.1"
    OCR_BACKEND         = "tesseract"
    OCR_LANGUAGES       = "eng+hin"
    TRUST_PROXY_HEADERS = "true"
    CORS_ORIGINS        = var.cors_origins
    LOG_LEVEL           = "INFO"
    APP_VERSION         = var.backend_image
  }, var.extra_env)

  secret_env = {
    for name in var.secret_settings :
    upper(name) => module.secrets.secret_ids[name]
  }
}

# A region/residency mismatch is a compliance incident, not a typo: refuse to plan.
check "residency_matches_region" {
  assert {
    condition     = var.region == local.expected_region
    error_message = "residency ${var.residency} must deploy to ${local.expected_region}, not ${var.region}."
  }
}

module "network" {
  source = "../network"

  project_id     = var.project_id
  region         = var.region
  name_prefix    = var.name_prefix
  subnet_cidr    = var.subnet_cidr
  connector_cidr = var.connector_cidr
}

module "iam" {
  source = "../iam"

  project_id                    = var.project_id
  environment                   = var.environment
  name_prefix                   = var.name_prefix
  github_repository             = var.github_repository
  create_workload_identity_pool = var.create_workload_identity_pool
}

module "artifact_registry" {
  source = "../artifact_registry"

  create         = var.create_artifact_registry
  project_id     = local.registry_project
  location       = var.region
  environment    = var.environment
  writer_members = ["serviceAccount:${module.iam.deploy_service_account}"]
  reader_members = module.iam.runtime_members
  labels         = local.labels
}

module "cloud_sql" {
  source = "../cloud_sql"

  project_id          = var.project_id
  region              = var.region
  instance_name       = "${var.name_prefix}-pg"
  network_id          = module.network.network_id
  tier                = var.sql_tier
  availability_type   = var.sql_availability_type
  disk_size_gb        = var.sql_disk_size_gb
  deletion_protection = var.sql_deletion_protection
  retained_backups    = var.sql_retained_backups
  # Backups stay inside the residency region (SPEC 11 / 12).
  backup_location = var.region
  labels          = local.labels

  depends_on = [module.network]
}

module "memorystore" {
  source = "../memorystore"

  project_id     = var.project_id
  region         = var.region
  name           = "${var.name_prefix}-redis"
  network_id     = module.network.network_id
  tier           = var.redis_tier
  memory_size_gb = var.redis_memory_size_gb
  labels         = local.labels

  depends_on = [module.network]
}

module "gcs" {
  source = "../gcs"

  project_id           = var.project_id
  location             = var.region
  residency            = var.residency
  name_prefix          = var.name_prefix
  bucket_name          = var.bucket_name
  force_destroy        = var.bucket_force_destroy
  object_admin_members = module.iam.runtime_members
  labels               = local.labels
}

module "secrets" {
  source = "../secrets"

  project_id           = var.project_id
  name_prefix          = var.name_prefix
  replication_location = var.region
  secret_names         = var.secret_settings
  accessor_members     = module.iam.runtime_members
  labels               = local.labels

  # The only values Terraform knows: the DSNs it just generated. Everything else stays an
  # empty secret until a human adds a version (SPEC 11).
  managed_values = {
    database_url       = module.cloud_sql.app_dsn
    database_url_owner = module.cloud_sql.owner_dsn
    redis_url          = module.memorystore.redis_url
  }
}

# ------------------------------------------------------------------ services
module "api" {
  source = "../cloud_run_service"

  project_id            = var.project_id
  region                = var.region
  name                  = "${var.name_prefix}-api"
  component             = "api"
  image                 = var.backend_image
  args                  = ["api"]
  service_account       = module.iam.api_service_account
  env                   = local.backend_env
  secret_env            = local.secret_env
  cloudsql_instance     = module.cloud_sql.connection_name
  vpc_connector         = module.network.connector_id
  min_instances         = var.api_min_instances
  max_instances         = var.api_max_instances
  concurrency           = 80
  cpu                   = "2"
  memory                = "2Gi"
  ingress               = "INGRESS_TRAFFIC_ALL"
  allow_unauthenticated = true
  deletion_protection   = var.sql_deletion_protection
  labels                = local.labels
}

module "worker" {
  source = "../cloud_run_service"

  project_id        = var.project_id
  region            = var.region
  name              = "${var.name_prefix}-worker"
  component         = "worker"
  image             = var.backend_image
  args              = ["worker"]
  service_account   = module.iam.worker_service_account
  env               = local.backend_env
  secret_env        = local.secret_env
  cloudsql_instance = module.cloud_sql.connection_name
  vpc_connector     = module.network.connector_id
  min_instances     = 1
  max_instances     = var.worker_max_instances
  concurrency       = 1
  cpu               = "2"
  memory            = "4Gi"
  # Celery works between requests; a throttled CPU stalls it mid-task.
  cpu_idle          = false
  startup_cpu_boost = false
  ingress           = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  labels            = local.labels
}

module "beat" {
  source = "../cloud_run_service"

  project_id        = var.project_id
  region            = var.region
  name              = "${var.name_prefix}-beat"
  component         = "beat"
  image             = var.backend_image
  args              = ["beat"]
  service_account   = module.iam.worker_service_account
  env               = local.backend_env
  secret_env        = local.secret_env
  cloudsql_instance = module.cloud_sql.connection_name
  vpc_connector     = module.network.connector_id
  # Exactly one beat: two schedulers double every periodic task.
  min_instances     = 1
  max_instances     = 1
  concurrency       = 1
  cpu               = "1"
  memory            = "1Gi"
  cpu_idle          = false
  startup_cpu_boost = false
  ingress           = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  labels            = local.labels
}

module "frontend" {
  source = "../cloud_run_service"

  project_id = var.project_id
  region     = var.region
  name       = "${var.name_prefix}-web"
  component  = "frontend"
  image      = var.frontend_image
  # The image CMD already starts the standalone server.
  args            = []
  service_account = module.iam.api_service_account
  env = {
    NODE_ENV                 = "production"
    PORT                     = "3000"
    AUTH_TRUST_HOST          = "true"
    NEXT_PUBLIC_API_BASE_URL = var.frontend_api_base_url == "" ? module.api.uri : var.frontend_api_base_url
  }
  secret_env = {
    AUTH_SECRET = module.secrets.secret_ids["auth_secret"]
  }
  port                  = 3000
  health_path           = ""
  min_instances         = var.frontend_min_instances
  max_instances         = var.frontend_max_instances
  concurrency           = 80
  cpu                   = "1"
  memory                = "1Gi"
  ingress               = "INGRESS_TRAFFIC_ALL"
  allow_unauthenticated = true
  labels                = local.labels
}

# ------------------------------------------------------------------ jobs + cron
module "jobs" {
  source = "../cloud_run_job"

  project_id        = var.project_id
  region            = var.region
  name_prefix       = var.name_prefix
  jobs              = local.jobs
  image             = var.backend_image
  service_account   = module.iam.jobs_service_account
  env               = local.backend_env
  secret_env        = local.secret_env
  cloudsql_instance = module.cloud_sql.connection_name
  vpc_connector     = module.network.connector_id
  labels            = local.labels
}

module "scheduler" {
  source = "../scheduler"

  project_id      = var.project_id
  region          = var.region
  name_prefix     = var.name_prefix
  triggers        = local.scheduler_triggers
  service_account = module.iam.scheduler_service_account

  depends_on = [module.jobs]
}

module "monitoring" {
  source = "../monitoring"

  enabled          = var.monitoring_enabled
  project_id       = var.project_id
  environment      = var.environment
  api_service_name = module.api.name
  ops_email        = var.ops_email
}
