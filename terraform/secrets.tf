# Recursos de secret VAZIOS -- os valores reais nunca entram aqui nem no
# state, são populados manualmente uma vez via `gcloud secrets versions
# add` (ver README > "Deploy via Terraform").

resource "google_secret_manager_secret" "openrouter_api_key" {
  secret_id = "openrouter-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "litellm_proxy_key" {
  secret_id = "litellm-proxy-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "session_db_url" {
  secret_id = "session-db-url"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# litellm-proxy é deployado sem --service-account, então roda como a
# Compute Engine default SA -- essa identidade precisa ler os dois secrets
# que o job deploy_litellm_proxy referencia via --set-secrets. Sem isso,
# o container sobe e falha ao buscar o secret em runtime (permission
# denied), silenciosamente, só descoberto na primeira execução real do
# pipeline.
resource "google_secret_manager_secret_iam_member" "compute_default_reads_openrouter" {
  secret_id = google_secret_manager_secret.openrouter_api_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.compute_default_sa}"
}

resource "google_secret_manager_secret_iam_member" "compute_default_reads_litellm_key" {
  secret_id = google_secret_manager_secret.litellm_proxy_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.compute_default_sa}"
}
