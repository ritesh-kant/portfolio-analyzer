# Database module — Amazon DocumentDB (MongoDB-compatible) cluster
#
# DocumentDB is a drop-in replacement for the self-hosted MongoDB used in local
# dev.  Set MONGODB_URI in SSM to the cluster endpoint after first apply.
#
# Minimum viable cluster: 1 instance (t3.medium).
# Production: use db.r6g.large with 3 instances across 3 AZs.

variable "environment"  { type = string }
variable "vpc_id"       { type = string }
variable "subnet_ids"   { type = list(string) }

locals {
  identifier = "portfolio-analyzer-${var.environment}"
  port       = 27017
}

# ─── Security group ───────────────────────────────────────────────────────────

resource "aws_security_group" "docdb" {
  name        = "${local.identifier}-docdb"
  description = "Allow MongoDB traffic from Lambda functions"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = local.port
    to_port     = local.port
    protocol    = "tcp"
    self        = true  # allow same-SG (Lambda → DocumentDB)
    description = "MongoDB from Lambda"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ─── Subnet group ─────────────────────────────────────────────────────────────

resource "aws_docdb_subnet_group" "main" {
  name       = local.identifier
  subnet_ids = var.subnet_ids
}

# ─── Cluster ──────────────────────────────────────────────────────────────────

resource "aws_docdb_cluster_parameter_group" "main" {
  family = "docdb5.0"
  name   = "${local.identifier}-params"

  parameter {
    name  = "tls"
    value = "enabled"
  }
}

resource "random_password" "docdb_master" {
  length  = 32
  special = false  # DocumentDB doesn't allow some special chars
}

resource "aws_docdb_cluster" "main" {
  cluster_identifier              = local.identifier
  engine                          = "docdb"
  engine_version                  = "5.0.0"
  master_username                 = "portfolioadmin"
  master_password                 = random_password.docdb_master.result
  db_subnet_group_name            = aws_docdb_subnet_group.main.name
  vpc_security_group_ids          = [aws_security_group.docdb.id]
  db_cluster_parameter_group_name = aws_docdb_cluster_parameter_group.main.name
  backup_retention_period         = 7
  preferred_backup_window         = "02:00-03:00"  # 7:30 AM IST — before market open
  skip_final_snapshot             = var.environment != "prod"
  deletion_protection             = var.environment == "prod"

  lifecycle {
    ignore_changes = [master_password]  # rotated outside Terraform
  }
}

resource "aws_docdb_cluster_instance" "main" {
  count              = var.environment == "prod" ? 3 : 1
  identifier         = "${local.identifier}-${count.index}"
  cluster_identifier = aws_docdb_cluster.main.id
  instance_class     = var.environment == "prod" ? "db.r6g.large" : "db.t3.medium"
}

# ─── Write connection string to SSM ───────────────────────────────────────────

resource "aws_ssm_parameter" "mongodb_uri" {
  name  = "/portfolio-analyzer/${var.environment}/MONGODB_URI"
  type  = "SecureString"
  value = "mongodb://${aws_docdb_cluster.main.master_username}:${random_password.docdb_master.result}@${aws_docdb_cluster.main.endpoint}:${local.port}/portfolio_analyzer?tls=true&tlsCAFile=/opt/rds-combined-ca-bundle.pem&replicaSet=rs0&readPreference=secondaryPreferred"
}

# ─── Outputs ──────────────────────────────────────────────────────────────────

output "endpoint"          { value = aws_docdb_cluster.main.endpoint; sensitive = true }
output "connection_string" { value = aws_ssm_parameter.mongodb_uri.value; sensitive = true }
