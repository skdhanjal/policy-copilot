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

