# One Docker repository per region. Production pulls the SAME digest that dev tested
# (SPEC 12), so prod-us and prod-in read from the dev repository unless the environment
# creates its own; `remote_repository_uri` records where the images actually come from.

resource "google_artifact_registry_repository" "this" {
  count = var.create ? 1 : 0

  project       = var.project_id
  location      = var.location
  repository_id = var.repository_id
  description   = "BidRadar container images (${var.environment})"
  format        = "DOCKER"

  docker_config {
    immutable_tags = var.immutable_tags
  }

  cleanup_policy_dry_run = var.cleanup_dry_run

  cleanup_policies {
    id     = "keep-recent-releases"
    action = "KEEP"
    most_recent_versions {
      keep_count = var.keep_versions
    }
  }

  cleanup_policies {
    id     = "delete-old-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = var.untagged_ttl
    }
  }

  labels = var.labels
}

resource "google_artifact_registry_repository_iam_member" "writers" {
  for_each = var.create ? toset(var.writer_members) : toset([])

  project    = var.project_id
  location   = var.location
  repository = google_artifact_registry_repository.this[0].name
  role       = "roles/artifactregistry.writer"
  member     = each.value
}

resource "google_artifact_registry_repository_iam_member" "readers" {
  for_each = var.create ? toset(var.reader_members) : toset([])

  project    = var.project_id
  location   = var.location
  repository = google_artifact_registry_repository.this[0].name
  role       = "roles/artifactregistry.reader"
  member     = each.value
}
