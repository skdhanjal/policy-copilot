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

