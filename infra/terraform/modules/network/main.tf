# Private VPC for BidRadar: Cloud SQL and Memorystore get private IPs only, and Cloud Run
# reaches them through a Serverless VPC Access connector. Nothing in the data tier has a
# public address (SPEC 11).

resource "google_compute_network" "this" {
  project                 = var.project_id
  name                    = "${var.name_prefix}-vpc"
  auto_create_subnetworks = false
  description             = "BidRadar ${var.name_prefix}: private data tier + serverless connector"
}

resource "google_compute_subnetwork" "this" {
  project                  = var.project_id
  name                     = "${var.name_prefix}-subnet"
  region                   = var.region
  network                  = google_compute_network.this.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true
}

# Range handed to the service producer (Cloud SQL, Memorystore) for private service access.
resource "google_compute_global_address" "private_service_range" {
  project       = var.project_id
  name          = "${var.name_prefix}-psa-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = var.private_service_prefix_length
  network       = google_compute_network.this.id
}

resource "google_service_networking_connection" "this" {
  network                 = google_compute_network.this.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_service_range.name]
}

resource "google_vpc_access_connector" "this" {
  project       = var.project_id
  name          = "${var.name_prefix}-conn"
  region        = var.region
  network       = google_compute_network.this.name
  ip_cidr_range = var.connector_cidr
  min_instances = var.connector_min_instances
  max_instances = var.connector_max_instances
  machine_type  = var.connector_machine_type
}

# Egress to the public internet for the adapters (SAM.gov, GePNIC, Anthropic) while the
# data tier stays private: one NAT on a router, no external IPs on anything.
resource "google_compute_router" "this" {
  project = var.project_id
  name    = "${var.name_prefix}-router"
  region  = var.region
  network = google_compute_network.this.id
}

resource "google_compute_router_nat" "this" {
  project                            = var.project_id
  name                               = "${var.name_prefix}-nat"
  router                             = google_compute_router.this.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}
