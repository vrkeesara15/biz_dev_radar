# dev: Cloud Run us-east1 (SPEC 12). Replace project_id with the real project before apply.
project_id        = "bidradar-dev"
region            = "us-east1"
state_bucket      = "bidradar-dev-tfstate"
backend_image     = "us-east1-docker.pkg.dev/bidradar-dev/bidradar/backend:latest"
frontend_image    = "us-east1-docker.pkg.dev/bidradar-dev/bidradar/frontend:latest"
ops_email         = "ops@example.com"
github_repository = "bidradar/bidradar"
