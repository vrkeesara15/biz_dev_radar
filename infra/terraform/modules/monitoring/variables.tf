variable "enabled" {
  description = "Alert policies cost nothing but noise; dev may turn them off."
  type        = bool
  default     = true
}

variable "project_id" {
  type = string
}

variable "environment" {
  type = string
}

variable "api_service_name" {
  type = string
}

variable "ops_email" {
  type    = string
  default = ""
}

variable "extra_notification_channels" {
  description = "Existing channel ids (e.g. a Slack channel created out of band)."
  type        = list(string)
  default     = []
}

variable "error_rate_threshold" {
  description = "5xx responses per second before paging."
  type        = number
  default     = 0.2
}

variable "disk_threshold_ratio" {
  type    = number
  default = 0.85
}
