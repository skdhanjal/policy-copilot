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
  depends_on = [google_service_networking_connection.private_vpc_connection]

  settings {
    tier = "db-f1-micro"
    ip_configuration {
      ipv4_enabled    = false
      private_network = "projects/${var.project_id}/global/networks/default"
    }
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

resource "google_vpc_access_connector" "connector" {
  name          = "policy-copilot-conn"
  region        = var.region
  ip_cidr_range = "10.8.0.0/28"
  network       = "default"
  depends_on = [google_project_service.compute, google_project_service.vpcaccess]
}

resource "google_redis_instance" "cache" {
  name           = "policy-copilot-redis"
  region         = var.region
  tier           = "BASIC"
  memory_size_gb = 1
  authorized_network = "default"
  depends_on = [google_project_service.redis, google_project_service.compute]
}

resource "google_compute_global_address" "private_ip_range" {
  name          = "policy-copilot-private-ip"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = "projects/${var.project_id}/global/networks/default"
  depends_on = [google_project_service.compute, google_project_service.servicenetworking]
}

resource "google_service_networking_connection" "private_vpc_connection" {
  network                 = "projects/${var.project_id}/global/networks/default"
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_range.name]
}

resource "google_project_service" "compute" {
  service            = "compute.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "servicenetworking" {
  service            = "servicenetworking.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "vpcaccess" {
  service            = "vpcaccess.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "redis" {
  service            = "redis.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "secretmanager" {
  service            = "secretmanager.googleapis.com"
  disable_on_destroy = false
}

resource "google_secret_manager_secret" "db_password" {
  secret_id = "db-password"
  replication {
    auto {}
  }
  depends_on = [google_project_service.secretmanager]
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = var.db_password
}

resource "google_secret_manager_secret" "openai_api_key" {
  secret_id = "openai-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.secretmanager]
}
resource "google_secret_manager_secret_version" "openai_api_key" {
  secret      = google_secret_manager_secret.openai_api_key.id
  secret_data = var.openai_api_key
}

resource "google_secret_manager_secret" "gemini_api_key" {
  secret_id = "gemini-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.secretmanager]
}
resource "google_secret_manager_secret_version" "gemini_api_key" {
  secret      = google_secret_manager_secret.gemini_api_key.id
  secret_data = var.gemini_api_key
}

resource "google_secret_manager_secret" "litellm_master_key" {
  secret_id = "litellm-master-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.secretmanager]
}
resource "google_secret_manager_secret_version" "litellm_master_key" {
  secret      = google_secret_manager_secret.litellm_master_key.id
  secret_data = var.litellm_master_key
}

resource "google_secret_manager_secret" "gateway_app_key" {
  secret_id = "gateway-app-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.secretmanager]
}
resource "google_secret_manager_secret_version" "gateway_app_key" {
  secret      = google_secret_manager_secret.gateway_app_key.id
  secret_data = var.gateway_app_key
}

resource "google_cloud_run_v2_service" "litellm" {
  name     = "policy-copilot-litellm"
  location = var.region
  depends_on = [google_artifact_registry_repository.docker_repo]

  template {
    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker_repo.repository_id}/litellm:v1"
      args = ["--config", "/app/config.yaml", "--port", "4000"]
      ports {
        container_port = 4000
      }
      resources {
        limits = {
          memory = "2Gi"
        }
      }
      startup_probe {
        tcp_socket {
          port = 4000
        }
        initial_delay_seconds = 10
        timeout_seconds       = 5
        period_seconds        = 5
        failure_threshold     = 10
      }
      env {
        name  = "OPENAI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.openai_api_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name  = "GEMINI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.gemini_api_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name  = "LITELLM_MASTER_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.litellm_master_key.secret_id
            version = "latest"
          }
        }
      }
    }
  }
}

resource "google_artifact_registry_repository" "docker_repo" {
  location      = var.region
  repository_id = "policy-copilot"
  format        = "DOCKER"
  depends_on    = [google_project_service.compute]
}

resource "google_secret_manager_secret_iam_member" "litellm_openai" {
  secret_id = google_secret_manager_secret.openai_api_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

resource "google_secret_manager_secret_iam_member" "litellm_gemini" {
  secret_id = google_secret_manager_secret.gemini_api_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

resource "google_secret_manager_secret_iam_member" "litellm_master" {
  secret_id = google_secret_manager_secret.litellm_master_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

data "google_project" "current" {
  project_id = var.project_id
}

