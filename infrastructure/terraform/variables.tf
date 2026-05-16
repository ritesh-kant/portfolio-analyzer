variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "ap-south-1"
}

variable "environment" {
  description = "Deployment environment (dev | staging | prod)"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod"
  }
}

variable "atlas_org_id" {
  description = "MongoDB Atlas Organisation ID (find it under Organisation > Settings)"
  type        = string
  sensitive   = true
}

variable "atlas_public_key" {
  description = "MongoDB Atlas API public key (Organisation > Access Manager > API Keys)"
  type        = string
  sensitive   = true
}

variable "atlas_private_key" {
  description = "MongoDB Atlas API private key"
  type        = string
  sensitive   = true
}

variable "signal_engine_image_tag" {
  description = "ECR image tag for the signal-engine container"
  type        = string
  default     = "latest"
}
