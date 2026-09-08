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

# Os três abaixo (ver terraform/observability_vm.tf) são usados pelos
# jobs deploy_sdr_bot_api/deploy_litellm_proxy pra alcançar a VM de
# observabilidade via Serverless VPC Access -- --vpc-connector precisa
# do NOME do connector, não do CIDR nem de nenhum outro identificador.
output "VPC_CONNECTOR_NAME" {
  value = google_vpc_access_connector.cloud_run_connector.name
}

# IP interno (10.10.0.0/24) -- só alcançável de dentro da VPC (via o
# connector acima), nunca da internet pública. Usado pra montar
# PHOENIX_COLLECTOR_ENDPOINT/LANGFUSE_OTEL_HOST (http, não https --
# tráfego interno, sem TLS na frente).
output "OBSERVABILITY_VM_INTERNAL_IP" {
  value = google_compute_instance.observability_vm.network_interface[0].network_ip
}
