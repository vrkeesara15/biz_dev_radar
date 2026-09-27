# prod-us: Cloud Run us-east1 (SPEC 12). backend_image / frontend_image are overridden by
# the deploy workflow with the DIGEST that staging-in approved; the tags here are a
# readable default, never what a promotion uses.
project_id                = "bidradar-prod-us"
region                    = "us-east1"
state_bucket              = "bidradar-prod-us-tfstate"
artifact_registry_project = "bidradar-dev"
backend_image             = "us-east1-docker.pkg.dev/bidradar-dev/bidradar/backend:latest"
frontend_image            = "us-east1-docker.pkg.dev/bidradar-dev/bidradar/frontend:latest"
ops_email                 = "ops@example.com"
github_repository         = "bidradar/bidradar"
