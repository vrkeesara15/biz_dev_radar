# staging-in — Cloud Run asia-south1 (Mumbai), live Indian sources and two pilot India
# tenants (SPEC 12). This is where the India checklist runs, so it is the first
# environment whose bucket, CMEK key, database, backups and secrets must all be Indian —
# `terraform output residency_ok` is that check.

module "bidradar" {
  source = "../../modules/environment"

  environment = "staging-in"
  project_id  = var.project_id
  region      = var.region
  residency   = "in"
  app_env     = "staging"
  name_prefix = "bidradar-stg-in"
  bucket_name = "${var.project_id}-bidradar-staging-in"

  backend_image             = var.backend_image
  frontend_image            = var.frontend_image
  create_artifact_registry  = false
  artifact_registry_project = var.artifact_registry_project

  # Pilot tenants have real data: protect it, but one zone is enough before launch.
  sql_tier                = "db-custom-2-7680"
  sql_availability_type   = "ZONAL"
  sql_disk_size_gb        = 50
  sql_deletion_protection = true
  sql_retained_backups    = 30
  bucket_force_destroy    = false

  redis_tier           = "BASIC"
  redis_memory_size_gb = 1

  api_min_instances      = 1
  api_max_instances      = 6
  worker_max_instances   = 3
  frontend_min_instances = 1
  frontend_max_instances = 3

  subnet_cidr    = "10.20.0.0/20"
  connector_cidr = "10.20.240.0/28"

  cors_origins = "https://staging-in.bidradar.example"

  # Indian portals only; SAM.gov has no Indian tenant to serve here.
  scheduled_adapter_regions = ["in"]

  ops_email                     = var.ops_email
  monitoring_enabled            = true
  github_repository             = var.github_repository
  create_workload_identity_pool = true
}
