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


data "google_project" "current" {
  project_id = var.project_id
}


resource "google_cloud_run_v2_service" "api" {
  name       = "policy-copilot-api"
  location   = var.region
  depends_on = [google_artifact_registry_repository.docker_repo]

  template {
    vpc_access {
      connector = google_vpc_access_connector.connector.id
      egress    = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker_repo.repository_id}/api:v1"

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          memory = "2Gi"
          cpu    = "2000m"
        }
      }

      startup_probe {
        tcp_socket {
          port = 8080
        }
        initial_delay_seconds = 10
        timeout_seconds       = 5
        period_seconds        = 5
        failure_threshold     = 10
      }

      env {
        name  = "POSTGRES_DSN"
        value = "postgresql://copilot:${var.db_password}@${google_sql_database_instance.main.private_ip_address}:5432/copilot"
      }
      env {
        name  = "REDIS_DSN"
        value = "redis://${google_redis_instance.cache.host}:6379/0"
      }
      env {
        name  = "GATEWAY_BASE_URL"
        value = google_cloud_run_v2_service.litellm.uri
      }
      env {
        name  = "GATEWAY_APP_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.litellm_master_key.secret_id
            version = "latest"
          }
        }
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

