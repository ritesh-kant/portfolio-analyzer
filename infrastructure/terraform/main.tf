terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    mongodbatlas = {
      source  = "mongodb/mongodbatlas"
      version = "~> 1.15"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }

  # Remote state — swap out for your S3 bucket before first apply.
  # backend "s3" {
  #   bucket  = "portfolio-analyzer-tfstate"
  #   key     = "portfolio-analyzer/terraform.tfstate"
  #   region  = "ap-south-1"
  #   encrypt = true
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "portfolio-analyzer"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# ─── Modules ──────────────────────────────────────────────────────────────────

module "database" {
  source           = "./modules/database"
  environment      = var.environment
  atlas_org_id     = var.atlas_org_id
  atlas_public_key = var.atlas_public_key
  atlas_private_key = var.atlas_private_key
}

module "signal_engine" {
  source           = "./modules/signal-engine"
  environment      = var.environment
  ecr_repo_name    = "portfolio-analyzer/signal-engine"
  lambda_image_tag = var.signal_engine_image_tag
  mongodb_uri      = module.database.connection_string
  ssm_prefix       = "/portfolio-analyzer/${var.environment}"
}

module "trading_service" {
  source         = "./modules/trading-service"
  environment    = var.environment
  signal_engine_lambda_name = module.signal_engine.lambda_function_name
}
