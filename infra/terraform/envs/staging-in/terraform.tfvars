# staging-in: Cloud Run asia-south1 / Mumbai (SPEC 12). Images are promoted from dev.
project_id                = "bidradar-staging-in"
region                    = "asia-south1"
state_bucket              = "bidradar-staging-in-tfstate"
artifact_registry_project = "bidradar-dev"
backend_image             = "us-east1-docker.pkg.dev/bidradar-dev/bidradar/backend:latest"
frontend_image            = "us-east1-docker.pkg.dev/bidradar-dev/bidradar/frontend:latest"
ops_email                 = "ops@example.com"
github_repository         = "bidradar/bidradar"
