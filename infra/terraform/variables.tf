variable "region" {
  description = "AWS region; the same as the bootstrap's."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Name prefix for every resource; the same as the bootstrap's."
  type        = string
  default     = "fantasy-helper"
}

variable "memory_mb" {
  description = "Memory for each function. Lambda's CPU scales with it: 1769 MB is one vCPU."
  type        = number
  default     = 2048
}

variable "api_reserved_concurrency" {
  description = <<-EOT
    Caps how many API requests run at once, which bounds cost if the public URL is hammered.
    Reserving needs 10 unreserved executions left over, so on a new account (a quota of 10)
    leave it null; check with `aws lambda get-account-settings`.
  EOT
  type        = number
  default     = null
}

variable "agent_daily_budget_usd" {
  description = <<-EOT
    The chat's spending cap: OpenAI dollars over any 24 hours, counting each turn in progress at
    its worst-case cost. Past it the chat answers "try again tomorrow".
  EOT
  type        = number
  default     = 1
}

variable "agent_visitor_daily_messages" {
  description = "Chat messages one visitor (by IP address) can send in 24 hours."
  type        = number
  default     = 30
}

variable "api_streaming" {
  description = <<-EOT
    Send API responses to the client as they're written (the function URL's RESPONSE_STREAM mode
    and the Lambda Web Adapter's response_stream), which the chat needs. False buffers each
    response whole, as before. Changing it updates the function and its URL seconds apart, and
    requests in between may fail.
  EOT
  type        = bool
  default     = true
}

variable "refresh_cooldown_seconds" {
  description = "A Refresh this soon after a successful ingest returns that run instead of fetching again."
  type        = number
  default     = 120
}

variable "ingest_schedule" {
  description = "EventBridge Scheduler expression for the ingest."
  type        = string
  default     = "cron(0 * * * ? *)" # hourly, on the hour
}

variable "schedule_timezone" {
  description = "Time zone the schedule is read in."
  type        = string
  default     = "America/New_York"
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "alert_email" {
  description = "Where the monthly budget alert goes; empty skips the budget."
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  description = "Alert at 80% of this (actual) and 100% (forecast). Expected spend is about $1."
  type        = number
  default     = 5
}
