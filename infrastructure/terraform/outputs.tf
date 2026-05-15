output "signal_engine_function_name" {
  description = "Lambda function name for the signal-engine"
  value       = module.signal_engine.lambda_function_name
}

output "signal_engine_function_url" {
  description = "Lambda function URL (if enabled)"
  value       = module.signal_engine.function_url
}

output "pipeline_queue_url" {
  description = "SQS FIFO queue URL for pipeline-run dispatch"
  value       = module.trading_service.queue_url
}

output "database_endpoint" {
  description = "DocumentDB cluster endpoint"
  value       = module.database.endpoint
  sensitive   = true
}
