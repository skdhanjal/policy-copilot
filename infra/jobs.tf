resource "google_cloud_run_v2_job" "ingest" {
  name       = "policy-copilot-ingest"
  location   = var.region
  depends_on = [google_artifact_registry_repository.docker_repo]

  template {
    template {
      vpc_access {
        connector = google_vpc_access_connector.connector.id
        egress    = "PRIVATE_RANGES_ONLY"
      }

      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker_repo.repository_id}/api:v1"
        command = ["python3", "-m", "app.rag.ingest"]

        resources {
          limits = {
            memory = "2Gi"
            cpu    = "2000m"
          }
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
          name  = "GATEWAY_APP_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.gateway_app_key.secret_id
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
      }

      max_retries = 1
      timeout     = "1800s"
    }
  }
}

resource "google_cloud_run_v2_job" "schema_setup" {
  name       = "policy-copilot-schema-setup"
  location   = var.region
  depends_on = [google_artifact_registry_repository.docker_repo]

  template {
    template {
      vpc_access {
        connector = google_vpc_access_connector.connector.id
        egress    = "PRIVATE_RANGES_ONLY"
      }

      containers {
        image   = "postgres:16"
        command = ["psql"]
        args = [
          "-h", google_sql_database_instance.main.private_ip_address,
          "-U", "copilot",
          "-d", "copilot",
          "-v", "ON_ERROR_STOP=1",
          "-c", file("${path.module}/schema.sql")
        ]
        env {
          name  = "PGPASSWORD"
          value = var.db_password
        }
      }
      max_retries = 0
    }
  }
}

