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

