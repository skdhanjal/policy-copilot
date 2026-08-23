output "db_connection_name" {
  value = google_sql_database_instance.main.connection_name
}

output "db_public_ip" {
  value = google_sql_database_instance.main.public_ip_address
}
output "redis_host" {
  value = google_redis_instance.cache.host
}
output "vpc_connector_id" {
  value = google_vpc_access_connector.connector.id
}
