# prod-us — Cloud Run us-east1, real US tenants (SPEC 12). Same image digest as prod-in;
# only the region, the residency and the sizing differ.

module "bidradar" {
  source = "../../modules/environment"

  environment = "prod-us"
  project_id  = var.project_id
  region      = var.region
  residency   = "us"
  app_env     = "production"
  name_prefix = "bidradar-prod-us"
  bucket_name = "${var.project_id}-bidradar-prod-us"

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
  api_max_instances      = 30
  worker_max_instances   = 8
  frontend_min_instances = 2
  frontend_max_instances = 15

  subnet_cidr    = "10.30.0.0/20"
  connector_cidr = "10.30.240.0/28"

  cors_origins = "https://app.bidradar.example"

  scheduled_adapter_regions = ["us"]

  ops_email                     = var.ops_email
  monitoring_enabled            = true
  github_repository             = var.github_repository
  create_workload_identity_pool = true
}
