# prod-in — Cloud Run asia-south1 (Mumbai), real Indian tenants (SPEC 12). The DPDP
# commitment is that nothing here leaves asia-south1: bucket, CMEK key, database, its
# backups and the Secret Manager replicas are all pinned to the region, and
# `terraform output residency_ok` is false if any of them is not.

module "bidradar" {
  source = "../../modules/environment"

  environment = "prod-in"
  project_id  = var.project_id
  region      = var.region
  residency   = "in"
  app_env     = "production"
  name_prefix = "bidradar-prod-in"
  bucket_name = "${var.project_id}-bidradar-prod-in"

  backend_image             = var.backend_image
  frontend_image            = var.frontend_image
  create_artifact_registry  = false
  artifact_registry_project = var.artifact_registry_project

  sql_tier                = "db-custom-4-15360"
  sql_availability_type   = "REGIONAL"
  sql_disk_size_gb        = 100
  sql_deletion_protection = true
  sql_retained_backups    = 30
  bucket_force_destroy    = false

  redis_tier           = "STANDARD_HA"
  redis_memory_size_gb = 4

  api_min_instances      = 2
  api_max_instances      = 20
  worker_max_instances   = 6
  frontend_min_instances = 2
  frontend_max_instances = 10

  subnet_cidr    = "10.40.0.0/20"
  connector_cidr = "10.40.240.0/28"

  cors_origins = "https://in.bidradar.example"

  scheduled_adapter_regions = ["in"]

  ops_email                     = var.ops_email
  monitoring_enabled            = true
  github_repository             = var.github_repository
  create_workload_identity_pool = true
}
