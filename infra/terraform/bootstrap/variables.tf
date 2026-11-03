variable "region" {
  description = "AWS region for everything."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "fantasy-helper"
}

variable "github_repo" {
  description = "owner/name of the GitHub repository allowed to deploy."
  type        = string
  default     = "dwangman123/fantasy-helper"
}

variable "create_github_oidc_provider" {
  description = "An account holds one GitHub OIDC provider; set false if it already has one."
  type        = bool
  default     = true
}
