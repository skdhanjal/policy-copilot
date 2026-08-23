variable "project_id" {
  default = "sentinel-desk-dev"
}

variable "region" {
  default = "asia-south1"
}

variable "db_password" {
  sensitive = true
}

variable "openai_api_key" { sensitive = true }
variable "gemini_api_key" { sensitive = true }
variable "litellm_master_key" { sensitive = true }
variable "gateway_app_key" { sensitive = true }
