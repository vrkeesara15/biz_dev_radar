variable "create" {
  description = "False when this environment pulls from another project's registry."
  type        = bool
  default     = true
}

variable "project_id" {
  type = string
}

variable "location" {
  type = string
}

variable "environment" {
  type = string
}

variable "repository_id" {
  type    = string
  default = "bidradar"
}

variable "immutable_tags" {
  description = "A tag must never move: deploys are promoted by digest (SPEC 12)."
  type        = bool
  default     = true
}

variable "keep_versions" {
  type    = number
  default = 30
}

variable "untagged_ttl" {
  type    = string
  default = "604800s"
}

variable "cleanup_dry_run" {
  type    = bool
  default = false
}

variable "writer_members" {
  type    = list(string)
  default = []
}

variable "reader_members" {
  type    = list(string)
  default = []
}

variable "labels" {
  type    = map(string)
  default = {}
}
