# Estes outputs viram, um a um, os secrets/variables do GitHub Actions
# (Settings > Secrets and variables > Actions) -- ver README > "Deploy via
# Terraform".

output "GCP_PROJECT_ID" {
  value = var.gcp_project_id
}

output "GCP_PROJECT_NUMBER" {
  value = var.gcp_project_number
}

output "GCP_REGION" {
  value = var.gcp_region
}

output "AR_REGION" {
  value = var.ar_region
}

output "AR_REPOSITORY" {
  value = google_artifact_registry_repository.sdr_bot_repo.repository_id
}

output "WIF_POOL_ID" {
  value = google_iam_workload_identity_pool.cicd_pool.workload_identity_pool_id
}

output "WIF_PROVIDER_ID" {
  value = google_iam_workload_identity_pool_provider.github_provider.workload_identity_pool_provider_id
}

output "WIF_SERVICE_ACCOUNT" {
  value = google_service_account.deploy_sa.email
}

# Novo -- usado pelo job deploy_sdr_bot_api pra montar a flag
# --add-cloudsql-instances. Formato PROJETO:REGIAO:INSTANCIA, já vem
# assim do provider (não precisa ser montado à mão).
output "CLOUD_SQL_CONNECTION_NAME" {
  value = google_sql_database_instance.sessions_db.connection_name
}
