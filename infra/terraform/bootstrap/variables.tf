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

variable "github_subject_prefix" {
  description = <<-EOT
    How GitHub names the repository allowed to deploy in its OIDC tokens: the sub_claim_prefix from
    `gh api repos/OWNER/NAME/actions/oidc/customization/sub`. With immutable subjects on, that is
    repo:OWNER@OWNER_ID/NAME@REPO_ID; without, repo:OWNER/NAME.
  EOT
  type        = string
  default     = "repo:dwangman123@31421903/fantasy-helper@1431753034"
}

variable "create_github_oidc_provider" {
  description = "An account holds one GitHub OIDC provider; set false if it already has one."
  type        = bool
  default     = true
}
