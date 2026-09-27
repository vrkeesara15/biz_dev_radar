variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "name" {
  type = string
}

variable "component" {
  description = "api | worker | beat | frontend; becomes a label."
  type        = string
}

variable "image" {
  description = "Fully qualified image, ideally by digest so a promotion cannot drift."
  type        = string
}

variable "args" {
  description = "Entrypoint mode, e.g. [\"api\"]. Empty uses the image CMD."
  type        = list(string)
  default     = []
}

variable "service_account" {
  type = string
}

variable "env" {
  description = "Plain environment variables."
  type        = map(string)
  default     = {}
}

variable "secret_env" {
  description = "env var name -> Secret Manager secret id, read at container start."
  type        = map(string)
  default     = {}
}

variable "cloudsql_instance" {
  description = "project:region:instance, or empty for a service with no database."
  type        = string
  default     = ""
}

variable "vpc_connector" {
  type    = string
  default = ""
}

variable "min_instances" {
  type    = number
  default = 0
}

variable "max_instances" {
  type    = number
  default = 10
}

variable "concurrency" {
  type    = number
  default = 80
}

variable "cpu" {
  type    = string
  default = "1"
}

variable "memory" {
  type    = string
  default = "1Gi"
}

variable "cpu_idle" {
  description = "False keeps the CPU allocated between requests (Celery needs this)."
  type        = bool
  default     = true
}

variable "startup_cpu_boost" {
  type    = bool
  default = true
}

variable "port" {
  type    = number
  default = 8080
}

variable "health_path" {
  description = "Startup and liveness probe path; empty disables both probes."
  type        = string
  default     = "/healthz"
}

variable "request_timeout_seconds" {
  type    = number
  default = 300
}

variable "ingress" {
  type    = string
  default = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
}

variable "allow_unauthenticated" {
  description = "True only for the public API and the front end."
  type        = bool
  default     = false
}

variable "invoker_members" {
  type    = list(string)
  default = []
}

variable "deletion_protection" {
  type    = bool
  default = false
}

variable "labels" {
  type    = map(string)
  default = {}
}
