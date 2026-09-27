output "network_id" {
  description = "Self link of the VPC."
  value       = google_compute_network.this.id
}

output "network_name" {
  value = google_compute_network.this.name
}

output "subnet_id" {
  value = google_compute_subnetwork.this.id
}

output "connector_id" {
  description = "Serverless VPC Access connector, referenced by every Cloud Run service and job."
  value       = google_vpc_access_connector.this.id
}

output "private_service_connection" {
  description = "Depend on this so Cloud SQL is not created before the peering exists."
  value       = google_service_networking_connection.this.id
}
