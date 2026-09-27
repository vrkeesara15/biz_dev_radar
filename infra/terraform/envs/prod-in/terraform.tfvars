# prod-in: Cloud Run asia-south1 / Mumbai (SPEC 12). The image is the SAME digest prod-us
# runs; the deploy workflow passes it explicitly.
project_id                = "bidradar-prod-in"
region                    = "asia-south1"
state_bucket              = "bidradar-prod-in-tfstate"
artifact_registry_project = "bidradar-dev"
backend_image             = "us-east1-docker.pkg.dev/bidradar-dev/bidradar/backend:latest"
frontend_image            = "us-east1-docker.pkg.dev/bidradar-dev/bidradar/frontend:latest"
ops_email                 = "ops@example.com"
github_repository         = "bidradar/bidradar"
