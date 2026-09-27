# Per-region object storage with customer-managed encryption keys (SPEC 10.1 Files row,
# SPEC 11 "CMEK at rest"). One keyring and one key PER REGION, in the same location as the
# bucket, so an Indian tenant's objects are encrypted by a key that also lives in India.

resource "google_kms_key_ring" "this" {
  project  = var.project_id
  name     = "${var.name_prefix}-${var.location}"
  location = var.location
}

resource "google_kms_crypto_key" "this" {
  name            = "${var.name_prefix}-storage"
  key_ring        = google_kms_key_ring.this.id
  rotation_period = var.key_rotation_period
  purpose         = "ENCRYPT_DECRYPT"

  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = var.key_protection_level
  }

  lifecycle {
    # Destroying a key makes every object in the bucket unreadable forever.
    prevent_destroy = true
  }
}

# The Cloud Storage service agent must be allowed to use the key, or bucket creation fails.
data "google_storage_project_service_account" "gcs" {
  project = var.project_id
}

resource "google_kms_crypto_key_iam_member" "gcs_agent" {
  crypto_key_id = google_kms_crypto_key.this.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"
}

resource "google_storage_bucket" "this" {
  project                     = var.project_id
  name                        = var.bucket_name
  location                    = var.location
  storage_class               = var.storage_class
  force_destroy               = var.force_destroy
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  encryption {
    default_kms_key_name = google_kms_crypto_key.this.id
  }

  versioning {
    enabled = var.versioning
  }

  # SPEC 5.1 keeps the raw source payload for replay/audit; 400 days covers a full
  # procurement cycle plus the annual review.
  lifecycle_rule {
    condition {
      age            = var.raw_retention_days
      matches_prefix = ["raw/"]
    }
    action {
      type = "Delete"
    }
  }

  # Tenant exports (M7-07) are a delivery mechanism, not a store: 30 days and gone.
  lifecycle_rule {
    condition {
      age            = var.export_retention_days
      matches_prefix = ["exports/"]
    }
    action {
      type = "Delete"
    }
  }

  # Anything superseded by a newer version is kept only briefly.
  lifecycle_rule {
    condition {
      num_newer_versions = 2
      with_state         = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  lifecycle_rule {
    condition {
      age            = var.nearline_after_days
      matches_prefix = ["documents/", "parsed/"]
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  logging {
    log_bucket        = var.access_log_bucket == "" ? var.bucket_name : var.access_log_bucket
    log_object_prefix = "access-logs/"
  }

  labels = merge(var.labels, { component = "files", residency = var.residency })

  depends_on = [google_kms_crypto_key_iam_member.gcs_agent]
}

resource "google_storage_bucket_iam_member" "writers" {
  for_each = toset(var.object_admin_members)
  bucket   = google_storage_bucket.this.name
  role     = "roles/storage.objectAdmin"
  member   = each.value
}
