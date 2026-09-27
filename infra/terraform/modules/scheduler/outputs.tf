output "job_names" {
  value = { for key, job in google_cloud_scheduler_job.this : key => job.name }
}

output "schedules" {
  description = "key -> cron, so the residency/ops outputs can show what runs when."
  value       = { for key, job in google_cloud_scheduler_job.this : key => job.schedule }
}
