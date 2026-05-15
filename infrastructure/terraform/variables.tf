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

variable "vpc_id" {
  description = "VPC ID for DocumentDB and Lambda placement"
  type        = string
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs (≥2 AZs required for DocumentDB)"
  type        = list(string)
}

variable "signal_engine_image_tag" {
  description = "ECR image tag for the signal-engine container"
  type        = string
  default     = "latest"
}
