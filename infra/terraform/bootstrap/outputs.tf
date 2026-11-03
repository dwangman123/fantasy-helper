output "state_bucket" {
  description = "Bucket for the main stack's state."
  value       = aws_s3_bucket.state.bucket
}

output "main_stack_init" {
  description = "Run in infra/terraform to point the main stack at its state."
  value       = "terraform init -backend-config=\"bucket=${aws_s3_bucket.state.bucket}\" -backend-config=\"region=${var.region}\""
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "app_secret_name" {
  description = "Put {\"FH_DATABASE_URL\": \"...\"} here before the first deploy."
  value       = aws_secretsmanager_secret.app.name
}

output "deploy_role_arn" {
  description = "GitHub repository variable AWS_DEPLOY_ROLE_ARN."
  value       = aws_iam_role.deploy.arn
}
