# Signal-engine module — ECR repository + Lambda container function
#
# The signal-engine is deployed as a Docker container image because its Python
# dependencies (pandas, yfinance, langgraph) exceed the Lambda 250 MB zip limit.
# Build + push the image from apps/signal-engine/ before running terraform apply.
#
#   docker build -t signal-engine apps/signal-engine/
#   docker tag signal-engine <account>.dkr.ecr.ap-south-1.amazonaws.com/portfolio-analyzer/signal-engine:<tag>
#   docker push ...

variable "environment"      { type = string }
variable "ecr_repo_name"    { type = string }
variable "lambda_image_tag" { type = string; default = "latest" }
variable "mongodb_uri"      { type = string; sensitive = true }
variable "ssm_prefix"       { type = string }

# ─── ECR ──────────────────────────────────────────────────────────────────────

resource "aws_ecr_repository" "signal_engine" {
  name                 = var.ecr_repo_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_ecr_lifecycle_policy" "signal_engine" {
  repository = aws_ecr_repository.signal_engine.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 5 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}

# ─── SSM secrets (populated by CI/CD — not managed here to avoid state drift) ─

data "aws_ssm_parameter" "anthropic_api_key" {
  name            = "${var.ssm_prefix}/ANTHROPIC_API_KEY"
  with_decryption = true
}

data "aws_ssm_parameter" "signal_engine_api_key" {
  name            = "${var.ssm_prefix}/SIGNAL_ENGINE_API_KEY"
  with_decryption = true
}

# ─── IAM ──────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "signal_engine" {
  name               = "signal-engine-lambda-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "basic" {
  role       = aws_iam_role.signal_engine.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "ssm" {
  name = "ssm-read"
  role = aws_iam_role.signal_engine.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameter", "ssm:GetParameters"]
      Resource = "arn:aws:ssm:*:*:parameter${var.ssm_prefix}/*"
    }]
  })
}

# ─── Lambda function ──────────────────────────────────────────────────────────

resource "aws_lambda_function" "signal_engine" {
  function_name = "signal-engine-${var.environment}"
  role          = aws_iam_role.signal_engine.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.signal_engine.repository_url}:${var.lambda_image_tag}"
  timeout       = 900
  memory_size   = 2048

  environment {
    variables = {
      TRADING_MODE         = "paper"
      AI_PROVIDER          = "anthropic"
      AWS_SECRETS_ENABLED  = "true"
      STAGE                = var.environment
    }
  }

  lifecycle {
    ignore_changes = [image_uri]  # updated by CI/CD, not Terraform
  }
}

resource "aws_cloudwatch_log_group" "signal_engine" {
  name              = "/aws/lambda/${aws_lambda_function.signal_engine.function_name}"
  retention_in_days = 14
}

# Optional: Lambda Function URL (alternative to API Gateway for direct HTTP access)
resource "aws_lambda_function_url" "signal_engine" {
  function_name      = aws_lambda_function.signal_engine.function_name
  authorization_type = "AWS_IAM"
}

# ─── Outputs ──────────────────────────────────────────────────────────────────

output "lambda_function_name" { value = aws_lambda_function.signal_engine.function_name }
output "function_url"         { value = aws_lambda_function_url.signal_engine.function_url }
output "ecr_repository_url"   { value = aws_ecr_repository.signal_engine.repository_url }
