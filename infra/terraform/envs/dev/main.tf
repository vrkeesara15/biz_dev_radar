# dev — Cloud Run us-east1, live sources with low quotas and synthetic tenants (SPEC 12).
# Every merge to main lands here. This is also the project that BUILDS the images: the
# other three environments pull the digest dev produced, so what ships was tested.

module "bidradar" {
  source = "../../modules/environment"

  environment = "dev"
  project_id  = var.project_id
  region      = var.region
  residency   = "us"
  app_env     = "dev"
  name_prefix = "bidradar-dev"
  bucket_name = "${var.project_id}-bidradar-dev-us"

  backend_image            = var.backend_image
  frontend_image           = var.frontend_image
  create_artifact_registry = true

  # Small and disposable: a dev database is a convenience, not an asset.
  sql_tier                = "db-custom-1-3840"
  sql_availability_type   = "ZONAL"
  sql_disk_size_gb        = 20
  sql_deletion_protection = false
  sql_retained_backups    = 7
  bucket_force_destroy    = true

  redis_tier           = "BASIC"
  redis_memory_size_gb = 1

  # Scale to zero between merges; the worker still keeps one instance so Celery runs.
  api_min_instances      = 0
  api_max_instances      = 4
  worker_max_instances   = 2
  frontend_min_instances = 0
  frontend_max_instances = 2

  subnet_cidr    = "10.10.0.0/20"
  connector_cidr = "10.10.240.0/28"

  cors_origins = "https://dev.bidradar.example"

  # dev exercises both adapter families so an India regression shows up before staging-in.
  scheduled_adapter_regions = ["us", "in"]

  ops_email                     = var.ops_email
  monitoring_enabled            = true
  github_repository             = var.github_repository
  create_workload_identity_pool = true
}
