output "bucket_name" {
  value = google_storage_bucket.this.name
}

output "bucket_location" {
  description = "Physical location of the objects; the residency check reads this (SPEC 12)."
  value       = google_storage_bucket.this.location
}

output "kms_key_id" {
  value = google_kms_crypto_key.this.id
}

output "kms_key_location" {
  description = "CMEK location; must equal bucket_location or the data is not fully in-region."
  value       = google_kms_key_ring.this.location
}
