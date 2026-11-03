output "api_url" {
  description = "Set as API_URL in the Vercel project (before its first build)."
  value       = trimsuffix(aws_lambda_function_url.api.function_url, "/")
}

output "api_function" {
  value = aws_lambda_function.api.function_name
}

output "ingest_function" {
  value = aws_lambda_function.ingest.function_name
}

output "ingest_logs" {
  description = "Where each scheduled ingest logs `ingest N succeeded`."
  value       = aws_cloudwatch_log_group.ingest.name
}
