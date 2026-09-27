# One Secret Manager secret per credential setting (SPEC 11: "API keys ... in Secret
# Manager, referenced by ID"). The list comes from `SECRET_SETTINGS` in
# backend/app/core/config.py, mirrored into secret_names by the caller, so a new
# credential in config.py is one line here and one line in the Cloud Run env.
#
# VALUES ARE NEVER IN TERRAFORM. This module creates the secret containers and their IAM;
# a human (or the deploy pipeline) adds versions with `gcloud secrets versions add`.
# Replication is user-managed and pinned to the environment's own region so an Indian
# tenant's database password never lands outside asia-south1.

resource "google_secret_manager_secret" "this" {
  for_each = toset(var.secret_names)

  project   = var.project_id
  secret_id = "${var.name_prefix}-${replace(each.value, "_", "-")}"

  replication {
    user_managed {
      replicas {
        location = var.replication_location
      }
    }
  }

  labels = merge(var.labels, { setting = replace(each.value, "_", "-") })
}

resource "google_secret_manager_secret_iam_member" "accessors" {
  for_each = {
    for pair in setproduct(var.secret_names, var.accessor_members) :
    "${pair[0]}::${pair[1]}" => { secret = pair[0], member = pair[1] }
  }

  project   = var.project_id
  secret_id = google_secret_manager_secret.this[each.value.secret].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value.member
}

# Secrets whose value Terraform itself produced (the generated Cloud SQL passwords and the
# DSNs built from them). Everything else stays empty until a human fills it.
resource "google_secret_manager_secret_version" "managed" {
  # The KEYS are setting names, not credentials, so they are safe as resource instance
  # keys; `nonsensitive` says so explicitly (Terraform refuses a sensitive for_each).
  for_each = toset(nonsensitive(keys(var.managed_values)))

  secret      = google_secret_manager_secret.this[each.value].id
  secret_data = var.managed_values[each.value]
}
