output "job_names" {
  description = "key -> full Cloud Run job name; the scheduler module triggers these."
  value       = { for key, job in google_cloud_run_v2_job.this : key => job.name }
}

output "job_ids" {
  value = { for key, job in google_cloud_run_v2_job.this : key => job.id }
}
