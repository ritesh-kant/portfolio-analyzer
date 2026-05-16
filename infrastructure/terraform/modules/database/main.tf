# Database module — MongoDB Atlas
#
# Replaces Amazon DocumentDB. Atlas runs outside the VPC, so Lambda functions
# need no vpc_config and no NAT Gateway to reach the database.
#
# Free tier (M0) is used for dev. Serverless (pay-per-operation) is used for
# staging and prod — cost is typically $0–$5/month at low traffic.
#
# Prerequisites:
#   1. Create a MongoDB Atlas account at https://cloud.mongodb.com
#   2. Create an API key (Organisation > Access Manager > API Keys)
#      with "Organisation Project Creator" permission
#   3. Pass the org_id, public_key, and private_key via tfvars / env vars

variable "environment"         { type = string }
variable "atlas_org_id"        { type = string; sensitive = true }
variable "atlas_public_key"    { type = string; sensitive = true }
variable "atlas_private_key"   { type = string; sensitive = true }
variable "atlas_region"        { type = string; default = "AP_SOUTH_1" }

terraform {
  required_providers {
    mongodbatlas = {
      source  = "mongodb/mongodbatlas"
      version = "~> 1.15"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "mongodbatlas" {
  public_key  = var.atlas_public_key
  private_key = var.atlas_private_key
}

locals {
  identifier = "portfolio-analyzer-${var.environment}"
  is_dev     = var.environment == "dev"
}

# ─── Atlas Project ────────────────────────────────────────────────────────────

resource "mongodbatlas_project" "main" {
  name   = local.identifier
  org_id = var.atlas_org_id
}

# ─── Cluster — M0 free tier for dev ──────────────────────────────────────────

resource "mongodbatlas_cluster" "free" {
  count      = local.is_dev ? 1 : 0
  project_id = mongodbatlas_project.main.id
  name       = local.identifier

  # Shared (free) tier — no cost, 512 MB storage limit
  provider_name               = "TENANT"
  backing_provider_name       = "AWS"
  provider_region_name        = var.atlas_region
  provider_instance_size_name = "M0"
}

# ─── Serverless instance — pay-per-operation for staging / prod ───────────────

resource "mongodbatlas_serverless_instance" "main" {
  count      = local.is_dev ? 0 : 1
  project_id = mongodbatlas_project.main.id
  name       = local.identifier

  provider_settings_backing_provider_name = "AWS"
  provider_settings_provider_name         = "SERVERLESS"
  provider_settings_region_name           = var.atlas_region
}

# ─── DB credentials ───────────────────────────────────────────────────────────

resource "random_password" "atlas" {
  length  = 32
  special = false
}

resource "mongodbatlas_database_user" "app" {
  project_id         = mongodbatlas_project.main.id
  username           = "portfolioadmin"
  password           = random_password.atlas.result
  auth_database_name = "admin"

  roles {
    role_name     = "readWrite"
    database_name = "portfolio_analyzer"
  }
}

# ─── IP access list ───────────────────────────────────────────────────────────
# Lambda functions have dynamic IPs that change on every cold start; allowing
# all IPs is the standard pattern. Security is enforced via credentials + TLS.

resource "mongodbatlas_project_ip_access_list" "all" {
  project_id = mongodbatlas_project.main.id
  cidr_block = "0.0.0.0/0"
  comment    = "Lambda functions have dynamic IPs; security enforced via credentials + TLS"
}

# ─── Build connection string ──────────────────────────────────────────────────

locals {
  # standard_srv format: mongodb+srv://<cluster-host>
  atlas_srv_host = local.is_dev ? (
    mongodbatlas_cluster.free[0].connection_strings[0].standard_srv
  ) : (
    mongodbatlas_serverless_instance.main[0].connection_strings_standard_srv
  )

  # Embed credentials and database name into the SRV URI
  connection_string = "${replace(local.atlas_srv_host, "mongodb+srv://", "mongodb+srv://${mongodbatlas_database_user.app.username}:${random_password.atlas.result}@")}/portfolio_analyzer?retryWrites=true&w=majority"
}

# ─── Write connection string to SSM (same path as before — no Lambda changes) ─

resource "aws_ssm_parameter" "mongodb_uri" {
  name  = "/portfolio-analyzer/${var.environment}/MONGODB_URI"
  type  = "SecureString"
  value = local.connection_string

  lifecycle {
    ignore_changes = [value]  # rotated outside Terraform
  }
}

# ─── Outputs ──────────────────────────────────────────────────────────────────

output "connection_string" { value = local.connection_string; sensitive = true }
output "project_id"        { value = mongodbatlas_project.main.id }
output "cluster_name"      { value = local.identifier }
