# The deployed backend: one container image run as two Lambda functions. The API serves the
# REST endpoints through a public function URL (the Next.js frontend proxies /api/* to it), and
# the ingest runs on an EventBridge Scheduler cron. The database is Postgres outside AWS (Neon);
# its URL lives in the app secret from ../bootstrap, which both functions read at startup.

terraform {
  required_version = ">= 1.11"
  backend "s3" {
    # bucket and region come from `terraform init -backend-config=...` (see the bootstrap's outputs)
    key          = "fantasy-helper/terraform.tfstate"
    use_lockfile = true
    encrypt      = true
  }
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.68"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = var.project }
  }
}

# --- From the bootstrap stack --------------------------------------------------------------

data "aws_ecr_repository" "app" {
  name = var.project
}

# Only the first apply uses this: afterwards the deploy workflow moves the functions to each
# new image, and Terraform leaves image_uri alone.
data "aws_ecr_image" "latest" {
  repository_name = data.aws_ecr_repository.app.name
  most_recent     = true
}

data "aws_secretsmanager_secret" "app" {
  name = "${var.project}/app"
}

locals {
  image_uri = "${data.aws_ecr_repository.app.repository_url}@${data.aws_ecr_image.latest.image_digest}"
  environment = {
    FH_SECRET_ID                = data.aws_secretsmanager_secret.app.name
    FH_REFRESH_COOLDOWN_SECONDS = tostring(var.refresh_cooldown_seconds)
  }
}

# --- Execution role ------------------------------------------------------------------------

resource "aws_iam_role" "lambda" {
  name = "${var.project}-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_secret" {
  name = "read-app-secret"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "secretsmanager:GetSecretValue"
      Resource = data.aws_secretsmanager_secret.app.arn
    }]
  })
}

# --- API -----------------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${var.project}-api"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "api" {
  function_name = "${var.project}-api"
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  architectures = ["x86_64"]
  memory_size   = var.memory_mb
  timeout       = 90 # a refresh runs a whole ingest

  reserved_concurrent_executions = var.api_reserved_concurrency

  environment {
    variables = local.environment
  }
  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.api.name
  }

  depends_on = [aws_iam_role_policy_attachment.lambda_logs]
  lifecycle {
    ignore_changes = [image_uri]
  }
}

resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "NONE"
}

# A public function URL needs both permissions (required for new URLs since October 2025).
resource "aws_lambda_permission" "api_url" {
  statement_id           = "PublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.api.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "api_url_invoke" {
  statement_id             = "PublicFunctionUrlInvoke"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.api.function_name
  principal                = "*"
  invoked_via_function_url = true
}

# --- Scheduled ingest ----------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${var.project}-ingest"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "ingest" {
  function_name = "${var.project}-ingest"
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  architectures = ["x86_64"]
  memory_size   = var.memory_mb
  timeout       = 300

  image_config {
    command = ["fantasy_helper.aws.ingest"]
  }
  environment {
    variables = local.environment
  }
  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.ingest.name
  }

  depends_on = [aws_iam_role_policy_attachment.lambda_logs]
  lifecycle {
    ignore_changes = [image_uri]
  }
}

# The scheduler invokes asynchronously, and Lambda would retry a failed async run twice; the
# next scheduled run is soon enough.
resource "aws_lambda_function_event_invoke_config" "ingest" {
  function_name          = aws_lambda_function.ingest.function_name
  maximum_retry_attempts = 0
}

resource "aws_iam_role" "scheduler" {
  name = "${var.project}-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  name = "invoke-ingest"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.ingest.arn
    }]
  })
}

resource "aws_scheduler_schedule" "ingest" {
  name                         = "${var.project}-ingest"
  schedule_expression          = var.ingest_schedule
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = aws_lambda_function.ingest.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ source = "schedule" })
    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}

# --- Cost alarm ----------------------------------------------------------------------------

# The whole account's spend, so anything unexpected shows up, not just this stack.
resource "aws_budgets_budget" "monthly" {
  count        = var.alert_email == "" ? 0 : 1
  name         = "${var.project}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
