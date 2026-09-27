variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "name_prefix" {
  type = string
}

variable "triggers" {
  description = "key -> { schedule (5-field cron), job_name }."
  type = map(object({
    schedule = string
    job_name = string
  }))
}

variable "service_account" {
  description = "Scheduler's identity; needs run.invoker and nothing else."
  type        = string
}

variable "time_zone" {
  description = "Adapter crons are written in UTC (SourceAdapter.schedule)."
  type        = string
  default     = "Etc/UTC"
}

variable "attempt_deadline" {
  type    = string
  default = "320s"
}

variable "retry_count" {
  type    = number
  default = 3
}

variable "min_backoff" {
  type    = string
  default = "30s"
}

variable "max_backoff" {
  type    = string
  default = "600s"
}
