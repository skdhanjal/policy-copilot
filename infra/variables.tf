variable "project_id" {
  default = "sentinel-desk-dev"
}

variable "region" {
  default = "asia-south1"
}

variable "db_password" {
  sensitive = true
}
