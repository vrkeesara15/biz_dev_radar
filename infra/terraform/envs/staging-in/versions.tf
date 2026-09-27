terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.40"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Partial configuration: the state bucket differs per environment and must live in the
  # environment's own region (an Indian environment's state names Indian resources).
  #   terraform init -backend-config=backend.hcl
  # CI validates with `-backend=false`, so no credentials are needed to check HCL.
  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
}
