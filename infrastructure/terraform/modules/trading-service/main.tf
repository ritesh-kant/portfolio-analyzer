# Trading-service module — SQS FIFO queue + pipeline consumer IAM
#
# The trading-service Lambda functions themselves are deployed by Serverless
# Framework (apps/trading-service/serverless.yml).  This module provisions the
# shared infrastructure that Serverless references but doesn't own:
#   - SQS FIFO queue for pipeline-run dispatch
#   - Dead-letter queue for failed messages

variable "environment"                 { type = string }
variable "signal_engine_lambda_name"   { type = string }

# ─── SQS ──────────────────────────────────────────────────────────────────────

resource "aws_sqs_queue" "pipeline_dlq" {
  name                        = "pipeline-runs-dlq-${var.environment}.fifo"
  fifo_queue                  = true
  message_retention_seconds   = 604800  # 7 days
}

resource "aws_sqs_queue" "pipeline_runs" {
  name                        = "pipeline-runs-${var.environment}.fifo"
  fifo_queue                  = true
  content_based_deduplication = false
  message_retention_seconds   = 86400   # 24 h
  visibility_timeout_seconds  = 120

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.pipeline_dlq.arn
    maxReceiveCount     = 3
  })
}

# ─── IAM: allow trading-service Lambdas to send to the queue ─────────────────

resource "aws_iam_policy" "sqs_send" {
  name = "pipeline-queue-send-${var.environment}"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:SendMessage"]
      Resource = aws_sqs_queue.pipeline_runs.arn
    }]
  })
}

# Attach to the Serverless-created Lambda role (name follows Serverless convention)
resource "aws_iam_role_policy_attachment" "trading_sqs_send" {
  role       = "trading-service-${var.environment}-ap-south-1-lambdaRole"
  policy_arn = aws_iam_policy.sqs_send.arn
}

# ─── Outputs ──────────────────────────────────────────────────────────────────

output "queue_url" { value = aws_sqs_queue.pipeline_runs.url }
output "queue_arn" { value = aws_sqs_queue.pipeline_runs.arn }
