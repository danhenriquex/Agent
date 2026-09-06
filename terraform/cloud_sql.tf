resource "google_sql_database_instance" "sessions_db" {
  name             = var.cloud_sql_instance_name
  database_version = "POSTGRES_15"
  region           = var.gcp_region

  settings {
    tier              = var.cloud_sql_tier
    availability_type = "ZONAL" # sem HA -- projeto de portfólio, não vale o dobro do custo
    disk_size         = 10      # GB, o mínimo prático
    disk_autoresize   = false   # teto de custo previsível em vez de crescimento silencioso

    backup_configuration {
      enabled = false # sem PITR/backups -- é um armazenamento de sessão de baixo risco, não dado de negócio
    }
  }

  # Permite que `terraform destroy` realmente remova a instância (ex: pra
  # economizar custo entre demonstrações do projeto). Documentado no README
  # como decisão consciente, não acidente.
  deletion_protection = false

  depends_on = [google_project_service.apis]
}

resource "google_sql_database" "sdr_bot" {
  name     = var.cloud_sql_database_name
  instance = google_sql_database_instance.sessions_db.name
}

# Deliberadamente NENHUM google_sql_user nem random_password aqui: o
# usuário/senha do Postgres são criados manualmente (`gcloud sql users
# create`) e a SESSION_DB_URL completa é montada à mão e populada direto
# no secret `session-db-url` -- mesma regra dos outros 5 secrets, nenhuma
# credencial jamais entra em .tf ou no state.

# Service account de RUNTIME do sdr-bot-api -- identidade DIFERENTE da SA
# de deploy (gitlab-ci-deployer, em workload_identity.tf). Uma faz o
# `gcloud run deploy` acontecer (tempo de deploy); esta é quem o container
# em execução usa depois de já estar rodando (tempo de execução).
resource "google_service_account" "sdr_bot_api_runtime" {
  account_id   = var.sdr_bot_api_runtime_sa_account_id
  display_name = "sdr-bot-api Cloud Run runtime SA"
}

resource "google_project_iam_member" "runtime_sa_cloudsql_client" {
  project = var.gcp_project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.sdr_bot_api_runtime.email}"
}

# Per-secret, não secretAccessor a nível de projeto: sdr-bot-api só lê
# estes dois secrets, nunca openrouter-api-key ou os dois do telegram --
# escopo mais apertado do que "acessar qualquer secret do projeto" pelo
# custo de dois blocos de recurso a mais.
resource "google_secret_manager_secret_iam_member" "runtime_sa_reads_litellm_key" {
  secret_id = google_secret_manager_secret.litellm_proxy_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.sdr_bot_api_runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "runtime_sa_reads_session_db_url" {
  secret_id = google_secret_manager_secret.session_db_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.sdr_bot_api_runtime.email}"
}
