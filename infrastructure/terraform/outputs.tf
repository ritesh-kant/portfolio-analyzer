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

output "atlas_project_id" {
  description = "MongoDB Atlas project ID"
  value       = module.database.project_id
}

output "atlas_cluster_name" {
  description = "MongoDB Atlas cluster / serverless instance name"
  value       = module.database.cluster_name
}
