# Secrets do telegram_service -- mesma regra dos outros: recurso vazio
# aqui, valor real populado manualmente depois via `gcloud secrets
# versions add`, nunca em .tf nem no state.

resource "google_secret_manager_secret" "telegram_bot_token" {
  secret_id = "telegram-bot-token"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "telegram_webhook_secret" {
  secret_id = "telegram-webhook-secret"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# telegram-service também é deployado sem --service-account (roda como a
# Compute Engine default SA), mesma razão do secrets.tf.
resource "google_secret_manager_secret_iam_member" "compute_default_reads_telegram_token" {
  secret_id = google_secret_manager_secret.telegram_bot_token.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.compute_default_sa}"
}

resource "google_secret_manager_secret_iam_member" "compute_default_reads_telegram_webhook_secret" {
  secret_id = google_secret_manager_secret.telegram_webhook_secret.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.compute_default_sa}"
}

# Nota: nenhuma role NOVA é necessária na SA de deploy (gitlab-ci-deployer)
# por causa deste terceiro serviço. roles/run.admin e
# roles/artifactregistry.writer (concedidas em workload_identity.tf) já são
# a nível de projeto/repositório -- cobrem QUALQUER serviço Cloud Run e
# QUALQUER imagem dentro do repositório do Artifact Registry, incluindo
# telegram-service, sem nenhuma mudança adicional.
