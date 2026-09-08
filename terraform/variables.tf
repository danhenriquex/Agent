variable "gcp_project_id" {
  type        = string
  description = "ID do projeto GCP de destino (mesmo valor da CI/CD variable GCP_PROJECT_ID)."
}

variable "gcp_project_number" {
  type        = string
  description = "Número do projeto GCP (gcloud projects describe --format='value(projectNumber)'). Necessário pro WIF."
}

variable "gcp_region" {
  type        = string
  default     = "us-central1"
  description = "Região dos serviços Cloud Run e da instância Cloud SQL."
}

variable "ar_region" {
  type        = string
  default     = "us-central1"
  description = "Região do repositório Docker no Artifact Registry."
}

variable "ar_repository" {
  type        = string
  default     = "sdr-bot-repo"
  description = "Nome do repositório Docker no Artifact Registry (já hardcoded nas image tags do .gitlab-ci.yml)."
}

variable "wif_pool_id" {
  type        = string
  default     = "github-pool"
  description = "ID do Workload Identity Pool usado pelo GitHub Actions."
}

variable "wif_provider_id" {
  type        = string
  default     = "github-provider"
  description = "ID do Provider OIDC dentro do pool."
}

variable "github_repository" {
  type        = string
  description = "owner/repo completo no GitHub (ex: \"danhenriquex/Agent\"), usado na attribute_condition do WIF -- não é o nome do projeto GCP."
}

variable "deploy_sa_account_id" {
  type        = string
  default     = "gitlab-ci-deployer"
  description = "account_id da service account que o pipeline do GitLab impersona via WIF."
}

variable "sdr_bot_api_runtime_sa_account_id" {
  type        = string
  default     = "sdr-bot-api-runtime"
  description = "account_id da service account de RUNTIME do Cloud Run sdr-bot-api (identidade diferente da SA de deploy)."
}

variable "cloud_sql_instance_name" {
  type        = string
  default     = "sdr-bot-sessions-db"
  description = "Nome da instância Cloud SQL usada pelo DatabaseSessionService."
}

variable "cloud_sql_tier" {
  type        = string
  default     = "db-f1-micro"
  description = "Tier da instância Cloud SQL. db-f1-micro é o menor/mais barato disponível para Postgres -- suficiente para um projeto de portfólio, sem HA."
}

variable "cloud_sql_database_name" {
  type        = string
  default     = "sdr_bot"
  description = "Nome do banco de dados Postgres dentro da instância."
}

variable "observability_vm_machine_type" {
  type        = string
  default     = "e2-medium"
  description = "Tipo da VM que roda Phoenix + LangFuse self-hospedados (ver terraform/observability_vm.tf). Suba pra e2-standard-2 se o ClickHouse OOMar."
}
