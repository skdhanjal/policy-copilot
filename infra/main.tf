terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_sql_database_instance" "main" {
  name             = "policy-copilot-db"
  database_version = "POSTGRES_16"
  region           = var.region
  deletion_protection = false

  settings {
    tier = "db-f1-micro"
  }
}

resource "google_sql_database" "app_db" {
  name     = "copilot"
  instance = google_sql_database_instance.main.name
}

resource "google_sql_user" "app_user" {
  name     = "copilot"
  instance = google_sql_database_instance.main.name
  password = var.db_password
}
