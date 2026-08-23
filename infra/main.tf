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

resource "null_resource" "enable_pgvector" {
  depends_on = [google_sql_database.app_db]

  provisioner "local-exec" {
    command = <<-EOT
      cloud-sql-proxy ${google_sql_database_instance.main.connection_name} --port 5433 &
      PROXY_PID=$!
      sleep 5
      PGPASSWORD='${var.db_password}' psql -h localhost -p 5433 -U copilot -d copilot -c "CREATE EXTENSION IF NOT EXISTS vector;"
      kill $PROXY_PID
    EOT
  }
}
